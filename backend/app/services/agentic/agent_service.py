# app/services/agentic/agent_service.py

import os
import json
import logging
from typing import AsyncGenerator
from sqlalchemy.orm import Session
from langchain_openai import ChatOpenAI
from langchain_community.vectorstores import FAISS
from langchain_core.messages import HumanMessage, AIMessage

from app.core.config import settings
from app.core.agentic.agent_constants import  UIEventTypes, NodeNames, AgentToolNames
from app.core.constants import RedisKeys,SystemConfig,AIModelConstants
from app.services.config_service import ConfigService
from app.services.cache_service import CacheManager
from app.services.rag_service import AliyunEmbeddingWrapper, AliyunReranker

from app.skills.law_skills import create_law_search_tool
from app.skills.amap_skills import create_amap_tools
from app.services.agentic.agentic_workflow import AgenticWorkflowManager

logger = logging.getLogger(__name__)


class AgenticRAGService:
    def __init__(self, db: Session, current_user=None):
        self.db = db
        emb_cfg = ConfigService.get_active_config(db, "embedding")
        llm_cfg = ConfigService.get_active_config(db, "llm")
        if not emb_cfg or not llm_cfg:
            raise Exception("系统 AI 配置缺失，请联系管理员")

        import redis
        try:
            self.redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0,
                                            decode_responses=True)
        except Exception:
            self.redis_client = None

        self.cache = CacheManager(self.redis_client)

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

        self.bm25_corpus = []
        self.bm25_instance = None
        self._init_bm25()

        # 装配 Skills 与工作流
        law_tool = create_law_search_tool(self.vector_db, self.bm25_instance, self.bm25_corpus, self.reranker)
        all_tools = [law_tool] + create_amap_tools()

        self.workflow_manager = AgenticWorkflowManager(
            self.llm, self.json_llm, all_tools,
            self.vector_db, self.bm25_instance, self.bm25_corpus, self.reranker
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
        except Exception as e:
            logger.error(f"BM25 初始化失败: {e}")

    async def agentic_chat_stream(self, query: str, session_id: str) -> AsyncGenerator[str, None]:
        # 获取上下文记忆
        history_key = RedisKeys.CHAT_HISTORY.format(session_id=session_id)
        chat_history_objs = []
        if self.redis_client:
            raw = self.redis_client.get(history_key)
            if raw:
                history_list = json.loads(raw)[-RedisKeys.MAX_HISTORY_LENGTH:]
                for i, msg in enumerate(history_list):
                    if i % 2 == 0:
                        chat_history_objs.append(HumanMessage(content=msg))
                    else:
                        chat_history_objs.append(AIMessage(content=msg))

        initial_state = {
            "messages": chat_history_objs + [HumanMessage(content=query)],
            "original_query": query,
            "search_query": query,
            "documents": [],
            "retries": 0,
            "generation": "",
            "intermediate_steps": []
        }

        full_answer = ""
        emitted_steps = set()  # 防止同一事件向前端重复发送

        # 核心事件解析器
        async for event in self.workflow_manager.graph.astream_events(initial_state, version="v2"):
            kind = event["event"]
            name = event.get("name", "")

            # 1. 状态变更提示
            if kind == "on_chain_start":
                step_msg = ""
                if name == NodeNames.GRADE_DOCS:
                    step_msg = UIEventTypes.GRADING_DOCS
                elif name == NodeNames.REWRITE:
                    step_msg = UIEventTypes.REWRITING
                elif name == NodeNames.RETRIEVE_RETRY:
                    step_msg = UIEventTypes.RETRIEVING
                elif name == NodeNames.GRADE_HALLUCINATION:
                    step_msg = UIEventTypes.CHECKING_HALLUCINATION

                if step_msg and step_msg not in emitted_steps:
                    emitted_steps.add(step_msg)
                    yield json.dumps({"type": "content", "data": f"\n\n> ⚙️ {step_msg}\n\n"}, ensure_ascii=False)

            # 2. 拦截并发送独立提取的高德 H5 交互地图卡片
            elif kind == "on_tool_end":
                tool_output = event["data"].get("output", "")
                if isinstance(tool_output, str):
                    try:
                        res_dict = json.loads(tool_output)
                        if "html_widget" in res_dict:
                            yield json.dumps({"type": "content", "data": res_dict["html_widget"] + "\n\n"},
                                             ensure_ascii=False)
                    except json.JSONDecodeError:
                        pass

            # 3. 过滤并输出真正的生成内容
            elif kind == "on_chat_model_stream":
                metadata = event.get("metadata", {})
                langgraph_node = metadata.get("langgraph_node")

                # 仅当模型处于最终生成阶段（GENERATE）且不含系统内置 tag 时，才透传前端
                if langgraph_node == NodeNames.GENERATE:
                    chunk = event["data"]["chunk"]
                    if chunk.content and isinstance(chunk.content, str):
                        full_answer += chunk.content
                        yield json.dumps({"type": "content", "data": chunk.content}, ensure_ascii=False)

        # 结束处理，更新记忆
        if self.redis_client and full_answer.strip():
            history_list = []
            raw = self.redis_client.get(history_key)
            if raw: history_list = json.loads(raw)
            history_list.extend([f"用户: {query}", f"助手: {full_answer}"])
            self.redis_client.setex(history_key, RedisKeys.HISTORY_EXPIRE_SECONDS,
                                    json.dumps(history_list[-RedisKeys.MAX_HISTORY_LENGTH * 2:]))

        yield json.dumps({"type": "done", "full_answer": full_answer}, ensure_ascii=False)