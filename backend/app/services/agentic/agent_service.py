# app/services/agentic/agent_service.py

import os
import json
import logging
import re
from typing import AsyncGenerator
from sqlalchemy.orm import Session
from langchain_openai import ChatOpenAI
from langchain_community.vectorstores import FAISS
from langchain_core.messages import HumanMessage

from app.core.config import settings
from app.core.agentic.agent_constants import UIEventTypes, NodeNames, AgentToolNames
from app.core.constants import RedisKeys, SystemConfig, AIModelConstants
from app.services.config_service import ConfigService
from app.services.cache_service import CacheManager
from app.services.chat_history_utils import (
    append_history_entries,
    build_scoped_redis_key,
    build_langchain_messages,
    dump_history_entries,
    load_history_entries,
    load_history_summary,
    maybe_compact_history_entries,
    merge_history_summary,
)
from app.services.rag_service import AliyunEmbeddingWrapper, AliyunReranker

from app.services.agentic.agentic_workflow import AgenticWorkflowManager
from app.services.agentic.state_store import AgentStateStore
from app.services.agentic.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


class AgenticRAGService:
    def __init__(self, db: Session, current_user=None):
        self.db = db
        self.current_user_id = getattr(current_user, "id", None)
        emb_cfg = ConfigService.get_active_config(db, "embedding")
        llm_cfg = ConfigService.get_active_config(db, "llm")
        if not emb_cfg or not llm_cfg:
            raise Exception("系统 AI 配置缺失，请联系管理员")

        import redis
        try:
            self.redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0,
                                            decode_responses=True)
            print("🟢 [Redis] 缓存服务连接成功")
        except Exception:
            self.redis_client = None
            print("🔴 [Redis] 缓存服务连接失败")

        self.cache = CacheManager(self.redis_client)
        self.state_store = AgentStateStore(self.redis_client, db=self.db)

        user_prefs = current_user.ai_preferences if (current_user and current_user.ai_preferences) else {}
        final_llm_model = user_prefs.get("llm_model") or llm_cfg.model_name
        final_llm_key = user_prefs.get("llm_key") or llm_cfg.api_key
        final_emb_model = user_prefs.get("embed_model") or emb_cfg.model_name
        final_emb_key = user_prefs.get("embed_key") or emb_cfg.api_key
        llm_base_url = AIModelConstants.DEEPSEEK_BASE_URL if "deepseek" in final_llm_model else llm_cfg.base_url

        self.llm = ChatOpenAI(model=final_llm_model, openai_api_key=final_llm_key, openai_api_base=llm_base_url,
                              temperature=0, streaming=True)
        self.json_llm = ChatOpenAI(model=final_llm_model, openai_api_key=final_llm_key, openai_api_base=llm_base_url,
                                   temperature=0)

        self.custom_embeddings = AliyunEmbeddingWrapper(final_emb_model, final_emb_key, emb_cfg.base_url)
        self.reranker = AliyunReranker(final_emb_key, emb_cfg.base_url)

        self.index_path = os.path.abspath(os.path.join(settings.BASE_DIR, SystemConfig.FAISS_INDEX_DIR))
        self.vector_db = None
        if os.path.exists(os.path.join(self.index_path, "index.faiss")):
            self.vector_db = FAISS.load_local(self.index_path, self.custom_embeddings,
                                              allow_dangerous_deserialization=True)
            print("🟢 [FAISS] 本地向量库挂载成功")

        self.bm25_corpus = []
        self.bm25_instance = None
        self._init_bm25()

        # 装配 Skills 与工作流（插件化注册加载）
        self.tool_registry = ToolRegistry()
        load_result = self.tool_registry.load_tools(
            context={
                "vector_db": self.vector_db,
                "bm25_instance": self.bm25_instance,
                "bm25_corpus": self.bm25_corpus,
                "reranker": self.reranker,
            }
        )
        all_tools = load_result.tools
        self.tool_metadata_by_name = load_result.tool_metadata_by_name
        self.tool_capability_to_tools = load_result.capability_to_tools
        self.tool_capability_hints = load_result.capability_hints
        if load_result.errors:
            logger.warning(f"ToolRegistry 加载存在告警: {' | '.join(load_result.errors)}")
        if all_tools:
            loaded_names = ", ".join([str(getattr(t, 'name', '')) for t in all_tools])
            print(f"🧩 [Tools] 插件化加载完成: {loaded_names}")
        else:
            print("🟡 [Tools] 未加载到任何插件工具，将仅保留基础对话能力")

        self.workflow_manager = AgenticWorkflowManager(
            self.llm, self.json_llm, all_tools,
            self.vector_db, self.bm25_instance, self.bm25_corpus, self.reranker,
            state_store=self.state_store,
            tool_metadata_by_name=self.tool_metadata_by_name,
            capability_hints=self.tool_capability_hints,
        )
        print("🤖[Agentic] 专家模式工作流引擎初始化完毕")

    def _build_memory_keys(self, session_id: str) -> tuple[str, str]:
        history_key = build_scoped_redis_key(RedisKeys.CHAT_HISTORY, session_id, self.current_user_id)
        summary_key = build_scoped_redis_key(RedisKeys.CHAT_SUMMARY, session_id, self.current_user_id)
        return history_key, summary_key

    def _persist_history_with_summary(
        self,
        history_key: str,
        summary_key: str,
        history_entries: list[dict],
        query: str,
        full_answer: str,
    ):
        if not self.redis_client:
            return

        history_list = append_history_entries(
            history_entries,
            query,
            full_answer,
            RedisKeys.MAX_HISTORY_LENGTH,
        )
        compacted_entries, archived_entries = maybe_compact_history_entries(
            history_list,
            RedisKeys.SUMMARY_TRIGGER_TURNS,
            RedisKeys.SUMMARY_KEEP_TURNS,
        )

        if archived_entries:
            summary_text = load_history_summary(self.redis_client, summary_key)
            merged_summary = merge_history_summary(summary_text, archived_entries)
            self.redis_client.setex(summary_key, RedisKeys.HISTORY_EXPIRE_SECONDS, merged_summary)

        self.redis_client.setex(
            history_key,
            RedisKeys.HISTORY_EXPIRE_SECONDS,
            dump_history_entries(compacted_entries),
        )

    def _init_bm25(self):
        try:
            from app.models.knowledge import KnowledgeDoc
            import jieba
            from rank_bm25 import BM25Okapi

            docs = self.db.query(KnowledgeDoc).all()
            for doc in docs:
                if doc.parsed_content and isinstance(doc.parsed_content, list):
                    self.bm25_corpus.extend(doc.parsed_content)
            if self.bm25_corpus:
                tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
                self.bm25_instance = BM25Okapi(tokenized_corpus)
                print(f"🟢 [BM25] 关键词检索引擎就绪，包含 {len(self.bm25_corpus)} 条切片")
        except Exception as e:
            logger.error(f"BM25 初始化失败: {e}")

    async def agentic_chat_stream(self, query: str, session_id: str) -> AsyncGenerator[str, None]:
        print(f"\n{'=' * 20}[专家模式提问] {'=' * 20}")
        print(f"👤 用户问题: {query}")

        run_id = self.state_store.create_run_id(session_id)
        safe_user_id = str(self.current_user_id) if self.current_user_id not in (None, "") else "anonymous"
        self.state_store.start_run(run_id=run_id, user_id=safe_user_id, session_id=session_id, query=query)

        # 获取上下文记忆
        history_key, summary_key = self._build_memory_keys(session_id)
        history_entries = load_history_entries(self.redis_client, history_key, RedisKeys.MAX_HISTORY_LENGTH)
        history_summary = load_history_summary(self.redis_client, summary_key)
        chat_history_objs = build_langchain_messages(history_entries)
        if history_entries:
            print(f"📚 [记忆加载] 已挂载最近 {len(history_entries)} 条历史对话")
        if history_summary:
            print("📝 [摘要记忆] 已加载历史摘要上下文")

        # 构建图的初始状态
        initial_state = {
            "messages": [HumanMessage(content=query)],
            "history_messages": chat_history_objs,
            "memory_summary": history_summary,
            "run_id": run_id,
            "session_id": str(session_id or "default"),
            "user_id": safe_user_id,
            "resume_from_node": "",
            "checkpoint_recovered": False,
            "original_query": query,
            "search_query": query,
            "handoff_router": {},
            "handoff_law": {},
            "handoff_tool": {},
            "handoff_synth": {},
            "handoff_judge": {},
            "private_documents": [],
            "public_sources": [],
            "private_tool_contexts": [],
            "private_memory_contexts": [],
            "private_latest_tool_names": [],
            "router_scratchpad": {},
            "law_scratchpad": {},
            "tool_scratchpad": {},
            "synth_scratchpad": {},
            "judge_scratchpad": {},
            "relevance_retries": 0,
            "hallucination_retries": 0,
            "generation": "",
            "hallucination_passed": True,
            "intermediate_steps": []
        }

        full_answer = ""
        emitted_steps = set()
        final_sources = []
        final_risk = {"passed": True, "risk_level": "low", "issues": [], "actions": []}
        last_sources_payload = None
        last_risk_payload = None
        done_sent = False

        print("🚀 [Agentic] 正在启动 LangGraph 状态机流转...")

        def _build_sources_payload(state_like: dict) -> list[dict]:
            public_sources = state_like.get("public_sources") or []
            if public_sources:
                return public_sources

            private_docs = state_like.get("private_documents", []) or []
            filtered_docs = [
                doc for doc in private_docs
                if not str(doc).strip().startswith("[长期记忆-")
            ]
            return [
                {"type": "law", "title": f"法规依据 {i + 1}", "label": f"检索片段 {i + 1}", "content": doc}
                for i, doc in enumerate(filtered_docs)
            ]

        async def _consume_graph(state: dict):
            nonlocal full_answer, final_sources, final_risk, last_sources_payload, last_risk_payload
            async for event in self.workflow_manager.graph.astream_events(state, version="v2"):
                kind = event["event"]
                name = event.get("name", "")

                # 1. 状态变更提示 (向前端推送中间状态框)
                if kind == "on_chain_start":
                    step_msg = ""
                    if name == NodeNames.ROUTER_AGENT:
                        step_msg = UIEventTypes.ROUTING
                    elif name == NodeNames.LAW_AGENT:
                        step_msg = UIEventTypes.LAW_WORKING
                    elif name == NodeNames.TOOL_AGENT:
                        step_msg = UIEventTypes.TOOL_WORKING
                    elif name == NodeNames.SYNTH_AGENT:
                        step_msg = UIEventTypes.SYNTHESIZING
                    elif name == NodeNames.JUDGE_AGENT:
                        step_msg = UIEventTypes.JUDGING

                    if step_msg:
                        composite_key = f"{name}|{step_msg}"
                        if composite_key not in emitted_steps:
                            emitted_steps.add(composite_key)
                            print(f"🚦 [状态流转] {step_msg}")
                            yield json.dumps(
                                {"type": "content", "data": f"\n\n> ⚙️ {step_msg}\n\n"},
                                ensure_ascii=False,
                            )

                    # 进入最终生成节点前，抓取检索到的文档并推给前端
                    if name == NodeNames.SYNTH_AGENT:
                        node_input = event.get("data", {}).get("input", {})
                        if isinstance(node_input, dict):
                            final_sources = _build_sources_payload(node_input)
                            payload = json.dumps(final_sources, ensure_ascii=False, sort_keys=True)
                            if payload != last_sources_payload:
                                print(f"📑 [推送依据] 发现 {len(final_sources)} 条有效法规切片，推送至前端引用面板")
                                yield json.dumps({"type": "sources", "data": final_sources}, ensure_ascii=False)
                                last_sources_payload = payload

                # 2. 工具执行完毕拦截
                elif kind == "on_tool_end":
                    print(f"🛠️[工具完成] 工具 {name} 顺利执行完毕")
                    tool_output = event["data"].get("output", "")
                    if isinstance(tool_output, str):
                        try:
                            res_dict = json.loads(tool_output)
                            if "html_widget" in res_dict:
                                print("🗺️ [推送UI组件] 发现高级交互式卡片，推送渲染至前端")
                                yield json.dumps({"type": "content", "data": res_dict["html_widget"] + "\n\n"},
                                                 ensure_ascii=False)
                        except json.JSONDecodeError:
                            pass

                # 3. 截获大模型生成节点，推送最终答案
                elif kind == "on_chat_model_stream":
                    metadata = event.get("metadata", {})
                    langgraph_node = metadata.get("langgraph_node")

                    # 仅当模型处于综合生成阶段（SYNTH_AGENT）才透传前端
                    if langgraph_node == NodeNames.SYNTH_AGENT:
                        chunk = event["data"]["chunk"]
                        if chunk.content and isinstance(chunk.content, str):
                            # 过滤阿里等模型的 DSML 内部思考脏数据
                            clean_content = re.sub(r'<\|?DSML\|?.*?>', '', chunk.content, flags=re.IGNORECASE)
                            clean_content = re.sub(r'</\|?DSML\|?.*?>', '', clean_content, flags=re.IGNORECASE)

                            if clean_content:
                                full_answer += clean_content
                                yield json.dumps({"type": "content", "data": clean_content}, ensure_ascii=False)

                # 4. 图谱流转结束时，如果前端还没收到 sources，发一个兜底数据
                elif kind == "on_chain_end" and name == NodeNames.JUDGE_AGENT:
                    node_output = event.get("data", {}).get("output", {})
                    if isinstance(node_output, dict):
                        risk_data = node_output.get("handoff_judge")
                        if isinstance(risk_data, dict):
                            final_risk = risk_data
                            risk_payload = json.dumps(final_risk, ensure_ascii=False, sort_keys=True)
                            if risk_payload != last_risk_payload:
                                yield json.dumps({"type": "risk", "data": final_risk}, ensure_ascii=False)
                                last_risk_payload = risk_payload
                elif kind == "on_chain_end" and name == "LangGraph":
                    final_state = event.get("data", {}).get("output", {})
                    if isinstance(final_state, dict):
                        final_sources = _build_sources_payload(final_state)
                        risk_data = final_state.get("handoff_judge")
                        if isinstance(risk_data, dict):
                            final_risk = risk_data

                # 5. 图谱节点异常事件
                elif kind == "on_chain_error":
                    error_data = event.get("data", {})
                    logger.error(f"LangGraph chain error: {error_data}")
                    yield json.dumps(
                        {"type": "content", "data": "\n\n> ⚠️ 处理过程中出现异常，正在尝试恢复...\n\n"},
                        ensure_ascii=False,
                    )

        recovery_attempted = False
        try:
            async for payload in _consume_graph(initial_state):
                yield payload
            self.state_store.finish_run(
                run_id,
                status="finished",
                recovered=False,
                answer_length=len(full_answer),
                sources_count=len(final_sources),
            )
        except Exception as e:
            logger.error(f"Agentic 流式处理异常: {e}")
            latest_checkpoint = self.state_store.load_latest_checkpoint(run_id)
            recovered_state = self.state_store.load_state_snapshot(run_id)
            resume_node = str(latest_checkpoint.get("last_node", "") or "").strip()

            if recovered_state and resume_node and not recovery_attempted:
                recovery_attempted = True
                recovered_state.update(
                    {
                        "run_id": run_id,
                        "session_id": str(session_id or "default"),
                        "user_id": safe_user_id,
                        "resume_from_node": resume_node,
                        "checkpoint_recovered": True,
                    }
                )
                try:
                    yield json.dumps(
                        {"type": "content", "data": f"\n\n> ♻️ 检测到中断，正在从节点 `{resume_node}` 恢复执行...\n\n"},
                        ensure_ascii=False,
                    )
                    async for payload in _consume_graph(recovered_state):
                        yield payload
                    self.state_store.finish_run(
                        run_id,
                        status="recovered",
                        recovered=True,
                        answer_length=len(full_answer),
                        sources_count=len(final_sources),
                    )
                except Exception as recover_err:
                    logger.error(f"Agentic 断点恢复失败: {recover_err}")
                    self.state_store.finish_run(run_id, status="failed", error=str(recover_err))
                    try:
                        yield json.dumps(
                            {"type": "content", "data": "\n\n> ⚠️ 自动恢复失败，请稍后重试。\n\n"},
                            ensure_ascii=False,
                        )
                    except GeneratorExit:
                        pass
            else:
                self.state_store.finish_run(run_id, status="failed", error=str(e))
                try:
                    yield json.dumps(
                        {"type": "content", "data": "\n\n> ⚠️ 系统处理异常，请稍后重试。\n\n"},
                        ensure_ascii=False,
                    )
                except GeneratorExit:
                    pass

        finally:
            # 兜底处理，确保不管怎样都闭环
            final_payload = json.dumps(final_sources, ensure_ascii=False, sort_keys=True)
            if final_payload != last_sources_payload:
                print("ℹ️ [引用兜底] 推送最终状态中的引用依据给前端")
                try:
                    yield json.dumps({"type": "sources", "data": final_sources}, ensure_ascii=False)
                except GeneratorExit:
                    pass

            risk_payload = json.dumps(final_risk, ensure_ascii=False, sort_keys=True)
            if risk_payload != last_risk_payload:
                try:
                    yield json.dumps({"type": "risk", "data": final_risk}, ensure_ascii=False)
                except GeneratorExit:
                    pass

            # 全局召回指标汇总日志
            print(
                f"[Agentic汇总] query='{query[:50]}' | "
                f"sources={len(final_sources)} | answer_len={len(full_answer)} | "
                f"history_turns={len(history_entries) // 2}"
            )

            # 结束处理，更新记忆
            print("💾 [持久化] 正在保存上下文记忆，结束本轮对话流")
            if self.redis_client and full_answer.strip():
                self._persist_history_with_summary(
                    history_key,
                    summary_key,
                    history_entries,
                    query,
                    full_answer,
                )
            if full_answer.strip():
                self.state_store.upsert_long_term_memories(
                    user_id=safe_user_id,
                    session_id=session_id,
                    query=query,
                    answer=full_answer,
                    run_id=run_id,
                )

            # 完成信号，让前端结束 Loading（确保仅发送一次）
            if not done_sent:
                done_sent = True
                try:
                    yield json.dumps({"type": "done", "full_answer": full_answer}, ensure_ascii=False)
                except GeneratorExit:
                    pass
