# app/services/agentic/agentic_workflow.py

import asyncio
import json
import re
import logging
from typing import Literal
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage

from app.services.agentic.agentic_state import AgenticState
from app.core.agentic.agent_constants import AgentLimits, NodeNames, GraderThresholds, UIEventTypes, AgentToolNames, RAGToolConfig
from app.core.agentic.agent_prompts import ExpertPrompts

logger = logging.getLogger(__name__)

# 每批文档打分最大数量
_BATCH_GRADE_SIZE = 5


class AgenticWorkflowManager:
    def __init__(self, llm, json_llm, tools_list, vector_db, bm25_instance, bm25_corpus, reranker):
        self.llm = llm
        self.json_llm = json_llm
        self.tools = {t.name: t for t in tools_list}
        self.llm_with_tools = self.llm.bind_tools(tools_list)

        # 注入底层检索组件以供重试节点独立使用
        self.vector_db = vector_db
        self.bm25_instance = bm25_instance
        self.bm25_corpus = bm25_corpus
        self.reranker = reranker

        self.graph = self._build_graph()

    def _extract_json_score(self, text: str, default: str = GraderThresholds.SCORE_NO) -> str:
        """剥离 Markdown 干扰，安全提取裁判模型的打分结果。

        Args:
            default: JSON 解析失败时的默认返回值。
                     相关性检测应传 SCORE_NO（不相关→重试，安全）；
                     幻觉检测应传 SCORE_YES（通过→放行，避免误触发重试）。
        """
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                data = json.loads(match.group())
                return data.get("score", default).lower()
            except json.JSONDecodeError:
                pass
        return default

    def _extract_batch_scores(self, text: str) -> list[dict]:
        """解析批量打分结果，返回 [{"index": 0, "score": "yes"}, ...]"""
        match = re.search(r'\[[\s\S]*\]', text)
        if match:
            try:
                data = json.loads(match.group())
                if isinstance(data, list):
                    return data
            except json.JSONDecodeError:
                pass

        # 尝试逐个提取 {index, score} 对象
        results = []
        for m in re.finditer(r'\{\s*"index"\s*:\s*(\d+)\s*,\s*"score"\s*:\s*"?(yes|no)"?\s*\}', text, re.IGNORECASE):
            results.append({"index": int(m.group(1)), "score": m.group(2).lower()})
        return results

    def _normalize_tool_result(self, raw_result) -> str:
        """提取工具返回中的纯文本部分，避免把 HTML/JSON 噪声直接送入生成器。"""
        if raw_result is None:
            return ""

        result_text = str(raw_result)
        try:
            payload = json.loads(result_text)
            if isinstance(payload, dict):
                return str(payload.get("text_data") or payload.get("content") or "").strip()
        except json.JSONDecodeError:
            pass

        return result_text.strip()

    def _extract_law_sources(self, raw_result) -> list[dict]:
        """从法规检索工具结果中提取结构化引用。"""
        if raw_result is None:
            return []

        result_text = str(raw_result)
        try:
            payload = json.loads(result_text)
        except json.JSONDecodeError:
            return []

        if not isinstance(payload, dict):
            return []

        sources = payload.get("sources", [])
        normalized_sources = []
        if not isinstance(sources, list):
            return normalized_sources

        for index, item in enumerate(sources):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            normalized_sources.append(
                {
                    "type": item.get("type", "law"),
                    "title": str(item.get("title") or f"法规依据 {index + 1}").strip(),
                    "label": str(item.get("label") or f"检索片段 {index + 1}").strip(),
                    "law_name": str(item.get("law_name") or "").strip(),
                    "article_no": str(item.get("article_no") or "").strip(),
                    "content": content,
                }
            )
        return normalized_sources

    def _build_law_context(self, law_sources: list[dict], documents: list[str]) -> str:
        """优先使用结构化法规来源构造生成上下文。"""
        if law_sources:
            blocks = []
            for index, item in enumerate(law_sources):
                header = item.get("title") or item.get("label") or f"法规依据 {index + 1}"
                content = item.get("content", "")
                if not content:
                    continue
                blocks.append(f"[{header}]\n{content}")
            if blocks:
                return "\n\n".join(blocks)

        return "\n\n".join(documents).strip() or "无"

    @staticmethod
    def _render_history(history_messages, max_turns: int = 4) -> str:
        """将 LangChain Message 列表渲染为文本，截断至最近 max_turns 轮。"""
        if not history_messages:
            return ""
        # 取最近 max_turns * 2 条消息
        sliced = list(history_messages[-(max_turns * 2):])
        lines = []
        for msg in sliced:
            role = "用户" if isinstance(msg, HumanMessage) else "助手"
            content = getattr(msg, "content", "")
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _compose_history_context(history_text: str, memory_summary: str) -> str:
        summary = str(memory_summary or "").strip()
        recent = str(history_text or "").strip()
        if summary and recent:
            return f"【历史摘要】\n{summary}\n\n【近期对话】\n{recent}"
        return summary or recent

    # ==========================================
    # 节点具体实现 (Nodes)
    # ==========================================

    async def node_agent(self, state: AgenticState):
        """决策中枢：决定工具调用或直接回应"""
        messages = state.get("messages", [])
        history_messages = state.get("history_messages", [])
        memory_summary = state.get("memory_summary", "")
        original_query = state.get("original_query", "")
        history_text = self._render_history(history_messages) if history_messages else ""
        history_context = self._compose_history_context(history_text, memory_summary)

        # 多轮对话指代消解：当有历史且当前问题可能含指代时，先消解
        resolved_query = original_query
        if history_context:
            try:
                resolve_prompt = ExpertPrompts.CONTEXTUAL_QUERY_REWRITER.format(
                    history=history_context, question=original_query
                )
                resolve_res = await self.llm.ainvoke(resolve_prompt)
                resolved = resolve_res.content.strip()
                if resolved:
                    resolved_query = resolved
                    print(f"🔍 [指代消解] '{original_query}' → '{resolved_query}'")
            except Exception as e:
                logger.warning(f"指代消解失败，降级使用原始问题: {e}")

        # 用消解后的 query 替换 messages 中的 HumanMessage
        updated_messages = []
        for msg in messages:
            if isinstance(msg, HumanMessage) and msg.content == original_query:
                updated_messages.append(HumanMessage(content=resolved_query))
            else:
                updated_messages.append(msg)

        sys_msg = SystemMessage(content=ExpertPrompts.MAIN_AGENT.format(history=history_context or "无"))
        invoke_msgs = [sys_msg] + list(history_messages) + list(updated_messages)

        response = await self.llm_with_tools.ainvoke(invoke_msgs)
        return {
            "messages": [response],
            "search_query": resolved_query,
            "intermediate_steps": [UIEventTypes.THINKING],
        }

    async def node_action(self, state: AgenticState):
        """工具执行器"""
        messages = state.get("messages", [])
        last_message = messages[-1]

        tool_outputs = []
        retrieved_docs = list(state.get("private_documents", []))
        public_sources = []
        private_tool_contexts = []
        private_latest_tool_names = []

        for tool_call in last_message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]
            private_latest_tool_names.append(tool_name)

            tool_instance = self.tools.get(tool_name)
            if tool_instance is None:
                tool_outputs.append(
                    ToolMessage(content=f"工具 {tool_name} 不存在或未注册", tool_call_id=tool_call["id"])
                )
                continue

            try:
                result = await tool_instance.ainvoke(tool_args)
                tool_msg = ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                tool_outputs.append(tool_msg)

                # 隔离逻辑：仅当调用法律检索时，才将结果纳入审核上下文
                if tool_name == AgentToolNames.LAW_SEARCH:
                    extracted_sources = self._extract_law_sources(result)
                    if extracted_sources:
                        public_sources.extend(extracted_sources)
                        retrieved_docs.extend([item["content"] for item in extracted_sources])
                    normalized_result = self._normalize_tool_result(result)
                    if (
                        normalized_result
                        and normalized_result != RAGToolConfig.FALLBACK_MESSAGE
                        and not extracted_sources
                    ):
                        retrieved_docs.append(normalized_result)
                else:
                    normalized_result = self._normalize_tool_result(result)
                    if normalized_result:
                        private_tool_contexts.append(normalized_result)
            except Exception as e:
                tool_outputs.append(ToolMessage(content=f"工具执行异常: {e}", tool_call_id=tool_call["id"]))

        return {
            "messages": tool_outputs,
            "private_documents": retrieved_docs,
            "public_sources": public_sources,
            "private_tool_contexts": private_tool_contexts,
            "private_latest_tool_names": private_latest_tool_names,
        }

    async def node_grade_docs(self, state: AgenticState):
        """相关性质检员（逐条打分）"""
        question = state.get("search_query") or state.get("original_query")
        documents = state.get("private_documents", [])
        public_sources = state.get("public_sources", [])

        if not documents:
            return {"public_sources": [], "intermediate_steps": [UIEventTypes.GRADING_DOCS]}

        # 单条文档：使用原有单条打分提示词
        if len(documents) == 1:
            doc_text = documents[0]
            prompt = ExpertPrompts.RELEVANCE_GRADER.format(question=question, document=doc_text)
            res = await self.json_llm.ainvoke(prompt)
            grade = self._extract_json_score(res.content)
            if grade == GraderThresholds.SCORE_NO:
                return {"private_documents": [], "public_sources": [], "intermediate_steps": [UIEventTypes.GRADING_DOCS]}
            return {"intermediate_steps": [UIEventTypes.GRADING_DOCS]}

        # 多条文档：分批逐条打分
        kept_indices = set()
        batches = [documents[i:i + _BATCH_GRADE_SIZE] for i in range(0, len(documents), _BATCH_GRADE_SIZE)]

        async def _grade_batch(batch_idx: int, batch: list[str]):
            """对一批文档进行打分，返回保留的文档索引列表。"""
            numbered = "\n\n".join(
                f"文档[{batch_idx + j}]：{doc}" for j, doc in enumerate(batch)
            )
            prompt = ExpertPrompts.RELEVANCE_GRADER_BATCH.format(question=question, numbered_documents=numbered)
            try:
                res = await self.json_llm.ainvoke(prompt)
                scores = self._extract_batch_scores(res.content)
                kept = set()
                for item in scores:
                    idx = item.get("index", -1)
                    score = str(item.get("score", "")).lower()
                    if score == GraderThresholds.SCORE_YES and batch_idx <= idx < batch_idx + len(batch):
                        kept.add(idx)
                # 解析失败时保守策略：未出现在结果中的 index 默认保留
                if len(scores) < len(batch):
                    for j in range(len(batch)):
                        kept.add(batch_idx + j)
                return kept
            except Exception as e:
                logger.warning(f"批量打分异常，保守保留该批次: {e}")
                return {batch_idx + j for j in range(len(batch))}

        # 并发执行各批次打分
        batch_tasks = [_grade_batch(i * _BATCH_GRADE_SIZE, batch) for i, batch in enumerate(batches)]
        batch_results = await asyncio.gather(*batch_tasks)
        for kept_set in batch_results:
            kept_indices.update(kept_set)

        # 过滤文档和 law_sources
        filtered_docs = [doc for idx, doc in enumerate(documents) if idx in kept_indices]
        kept_contents = set(filtered_docs)
        filtered_public_sources = [src for src in public_sources if src.get("content", "") in kept_contents]

        if not filtered_docs:
            print(
                f"[相关性打分] query='{question[:30]}' | total={len(documents)} | kept=0 | pass_rate=0.0%"
            )
            return {"private_documents": [], "public_sources": [], "intermediate_steps": [UIEventTypes.GRADING_DOCS]}

        pass_rate = len(filtered_docs) / len(documents) * 100 if documents else 0
        print(
            f"[相关性打分] query='{question[:30]}' | total={len(documents)} | "
            f"kept={len(filtered_docs)} | pass_rate={pass_rate:.1f}% | "
            f"public_sources={len(filtered_public_sources)}/{len(public_sources)}"
        )

        result = {"intermediate_steps": [UIEventTypes.GRADING_DOCS]}
        # 仅在过滤后有变化时才更新
        if len(filtered_docs) < len(documents):
            result["private_documents"] = filtered_docs
            result["public_sources"] = filtered_public_sources
        return result

    async def node_rewrite(self, state: AgenticState):
        """意图反思与重写器"""
        question = state.get("search_query") or state.get("original_query")
        history_messages = state.get("history_messages", [])
        memory_summary = state.get("memory_summary", "")
        history_text = self._render_history(history_messages) if history_messages else ""
        history_context = self._compose_history_context(history_text, memory_summary)

        prompt = ExpertPrompts.QUERY_REWRITER.format(question=question, history=history_context)
        res = await self.llm.ainvoke(prompt)
        new_query = res.content.strip()

        current_retries = state.get("relevance_retries", 0) + 1
        return {
            "search_query": new_query,
            "relevance_retries": current_retries,
            "intermediate_steps": [UIEventTypes.REWRITING],
        }

    async def node_retrieve_retry(self, state: AgenticState):
        """独立重试检索器：脱离 Agent 直接查库，防止大模型陷入工具选择死循环"""
        new_query = state.get("search_query")
        law_tool = self.tools.get(AgentToolNames.LAW_SEARCH)

        try:
            # 直接调用底层检索工具逻辑
            result = await law_tool.ainvoke({"query": new_query})
            extracted_sources = self._extract_law_sources(result)
            if extracted_sources:
                return {
                    "private_documents": [item["content"] for item in extracted_sources],
                    "public_sources": extracted_sources,
                    "intermediate_steps": [UIEventTypes.RETRIEVING],
                }

            normalized_result = self._normalize_tool_result(result)
            if normalized_result and normalized_result != RAGToolConfig.FALLBACK_MESSAGE:
                return {
                    "private_documents": [normalized_result],
                    "public_sources": [],
                    "intermediate_steps": [UIEventTypes.RETRIEVING],
                }

            return {"private_documents": [], "public_sources": [], "intermediate_steps": [UIEventTypes.RETRIEVING]}
        except Exception as e:
            logger.error(f"重试检索异常: {e}")
            return {"private_documents": [], "public_sources": [], "intermediate_steps": [UIEventTypes.RETRIEVING]}

    async def node_generate(self, state: AgenticState):
        """综合生成器"""
        original_query = state.get("original_query", "")
        search_query = state.get("search_query", "")
        documents = state.get("private_documents", [])
        law_sources = state.get("public_sources", [])
        tool_contexts = state.get("private_tool_contexts", [])
        history_messages = state.get("history_messages", [])
        memory_summary = state.get("memory_summary", "")

        law_context = self._build_law_context(law_sources, documents)
        tool_context = "\n\n".join(tool_contexts).strip() or "无"
        history_text = self._render_history(history_messages) if history_messages else ""
        history_context = self._compose_history_context(history_text, memory_summary)

        # 追问场景：如果 search_query 经过指代消解，优先使用消解后的完整问题
        question = search_query if search_query and search_query != original_query else original_query

        prompt = ExpertPrompts.FINAL_GENERATOR.format(
            law_context=law_context,
            tool_context=tool_context,
            question=question,
            history=history_context,
        )
        res = await self.llm.ainvoke(prompt)

        print(
            f"[生成] question='{question[:50]}' | "
            f"docs={len(documents)} | law_sources={len(law_sources)} | "
            f"tool_contexts={len(tool_contexts)} | history_turns={len(history_messages) // 2}"
        )

        return {"generation": res.content, "intermediate_steps": [UIEventTypes.GENERATING]}

    async def node_grade_hallucination(self, state: AgenticState):
        """幻觉质检员"""
        documents = state.get("private_documents", [])
        generation = state.get("generation", "")

        if not documents:
            return {
                "hallucination_passed": True,
                "intermediate_steps": [UIEventTypes.CHECKING_HALLUCINATION],
            }

        prompt = ExpertPrompts.HALLUCINATION_GRADER.format(documents="\n".join(documents), generation=generation)
        res = await self.json_llm.ainvoke(prompt)

        # 幻觉检测：解析失败时默认 YES（放行），避免误触发重试
        grade = self._extract_json_score(res.content, default=GraderThresholds.SCORE_YES)
        if grade == GraderThresholds.SCORE_NO:
            current_retries = state.get("hallucination_retries", 0) + 1
            return {
                "hallucination_retries": current_retries,
                "hallucination_passed": False,
                "intermediate_steps": [UIEventTypes.CHECKING_HALLUCINATION],
            }

        return {
            "hallucination_passed": True,
            "intermediate_steps": [UIEventTypes.CHECKING_HALLUCINATION],
        }

    # ==========================================
    # 路由逻辑 (Conditional Routing)
    # ==========================================

    def route_from_agent(self, state: AgenticState) -> Literal[NodeNames.ACTION, NodeNames.GENERATE]:
        messages = state.get("messages", [])
        last_message = messages[-1]
        if getattr(last_message, "tool_calls", None):
            return NodeNames.ACTION
        return NodeNames.GENERATE

    def route_from_action(self, state: AgenticState) -> Literal[NodeNames.GRADE_DOCS, NodeNames.GENERATE]:
        #路由逻辑：根据本次执行的工具集决定下一步
        latest_tool_names = state.get("private_latest_tool_names", [])
        if not latest_tool_names:
            return NodeNames.GENERATE

        # 只要这一批工具调用中包含"法规检索"，就必须走打分车道审核
        for tool_name in latest_tool_names:
            # 使用常量进行判断，解耦硬编码
            if tool_name == AgentToolNames.LAW_SEARCH:
                print(f"⚖️ [路由决策] 检测到法律检索行为，引导至 {NodeNames.GRADE_DOCS} 节点")
                return NodeNames.GRADE_DOCS

        # 纯生活服务/导航类请求，走生成快车道
        print(f"🚀 [路由决策] 纯外部工具调用，引导至 {NodeNames.GENERATE} 节点")
        return NodeNames.GENERATE

    def route_from_grade_docs(self, state: AgenticState) -> Literal[NodeNames.REWRITE, NodeNames.GENERATE]:
        documents = state.get("private_documents", [])
        if not documents:
            return NodeNames.REWRITE
        return NodeNames.GENERATE

    def route_from_rewrite(self, state: AgenticState) -> Literal[NodeNames.RETRIEVE_RETRY, NodeNames.GENERATE]:
        retries = state.get("relevance_retries", 0)
        # 超过上限硬性切断死循环
        if retries >= AgentLimits.MAX_RELEVANCE_RETRIES:
            logger.warning("达到最大相关性重试次数，强制终止检索阶段。")
            return NodeNames.GENERATE
        return NodeNames.RETRIEVE_RETRY

    def route_from_grade_hallucination(self, state: AgenticState) -> Literal[NodeNames.REWRITE, END]:
        hallucination_passed = state.get("hallucination_passed", True)
        if hallucination_passed:
            return END

        retries = state.get("hallucination_retries", 0)
        # 如果发现幻觉，打回重写逻辑（若已达上限则放行，前端提示可能存在风险）
        if retries < AgentLimits.MAX_HALLUCINATION_RETRIES:
            return NodeNames.REWRITE
        return END

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(AgenticState)

        workflow.add_node(NodeNames.AGENT, self.node_agent)
        workflow.add_node(NodeNames.ACTION, self.node_action)
        workflow.add_node(NodeNames.GRADE_DOCS, self.node_grade_docs)
        workflow.add_node(NodeNames.REWRITE, self.node_rewrite)
        workflow.add_node(NodeNames.RETRIEVE_RETRY, self.node_retrieve_retry)
        workflow.add_node(NodeNames.GENERATE, self.node_generate)
        workflow.add_node(NodeNames.GRADE_HALLUCINATION, self.node_grade_hallucination)

        workflow.set_entry_point(NodeNames.AGENT)

        workflow.add_conditional_edges(NodeNames.AGENT, self.route_from_agent)
        workflow.add_conditional_edges(NodeNames.ACTION, self.route_from_action)
        workflow.add_conditional_edges(NodeNames.GRADE_DOCS, self.route_from_grade_docs)
        workflow.add_conditional_edges(NodeNames.REWRITE, self.route_from_rewrite)

        workflow.add_edge(NodeNames.RETRIEVE_RETRY, NodeNames.GRADE_DOCS)
        workflow.add_edge(NodeNames.GENERATE, NodeNames.GRADE_HALLUCINATION)
        workflow.add_conditional_edges(NodeNames.GRADE_HALLUCINATION, self.route_from_grade_hallucination)

        return workflow.compile()
