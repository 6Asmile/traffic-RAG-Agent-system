# app/services/agentic/agentic_workflow.py

import json
import re
import logging
from typing import Literal
from langgraph.graph import StateGraph, END
from langchain_core.messages import HumanMessage, ToolMessage, SystemMessage

from app.services.agentic.agentic_state import AgenticState
from app.core.agentic.agent_constants import AgentLimits, NodeNames, GraderThresholds, UIEventTypes, AgentToolNames
from app.core.agentic.agent_prompts import ExpertPrompts

logger = logging.getLogger(__name__)


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

    def _extract_json_score(self, text: str) -> str:
        """剥离 Markdown 干扰，安全提取裁判模型的打分结果"""
        match = re.search(r'\{[\s\S]*\}', text)
        if match:
            try:
                data = json.loads(match.group())
                return data.get("score", GraderThresholds.SCORE_NO).lower()
            except json.JSONDecodeError:
                pass
        return GraderThresholds.SCORE_NO

    # ==========================================
    # 节点具体实现 (Nodes)
    # ==========================================

    async def node_agent(self, state: AgenticState):
        """决策中枢：决定工具调用或直接回应"""
        messages = state.get("messages", [])
        sys_msg = SystemMessage(content=ExpertPrompts.MAIN_AGENT.format(history="历史记录已包含在上下文"))
        invoke_msgs = [sys_msg] + list(messages)

        response = await self.llm_with_tools.ainvoke(invoke_msgs)
        return {"messages": [response], "intermediate_steps": [UIEventTypes.THINKING]}

    async def node_action(self, state: AgenticState):
        """工具执行器"""
        messages = state.get("messages", [])
        last_message = messages[-1]

        tool_outputs = []
        retrieved_docs = state.get("documents", [])

        for tool_call in last_message.tool_calls:
            tool_name = tool_call["name"]
            tool_args = tool_call["args"]

            tool_instance = self.tools.get(tool_name)
            try:
                result = await tool_instance.ainvoke(tool_args)
                tool_msg = ToolMessage(content=str(result), tool_call_id=tool_call["id"])
                tool_outputs.append(tool_msg)

                # 隔离逻辑：仅当调用法律检索时，才将结果纳入审核上下文
                if tool_name == AgentToolNames.LAW_SEARCH:
                    retrieved_docs.append(str(result))
            except Exception as e:
                tool_outputs.append(ToolMessage(content=f"工具执行异常: {e}", tool_call_id=tool_call["id"]))

        return {"messages": tool_outputs, "documents": retrieved_docs}

    async def node_grade_docs(self, state: AgenticState):
        """相关性质检员"""
        question = state.get("search_query") or state.get("original_query")
        documents = state.get("documents", [])

        if not documents:
            return {"intermediate_steps": [UIEventTypes.GRADING_DOCS]}

        doc_text = "\n".join(documents)
        prompt = ExpertPrompts.RELEVANCE_GRADER.format(question=question, document=doc_text)
        res = await self.json_llm.ainvoke(prompt)

        grade = self._extract_json_score(res.content)
        if grade == GraderThresholds.SCORE_NO:
            # 判定为不相关，清空废弃文档，准备触发重写
            return {"documents": [], "intermediate_steps": [UIEventTypes.GRADING_DOCS]}

        return {"intermediate_steps": [UIEventTypes.GRADING_DOCS]}

    async def node_rewrite(self, state: AgenticState):
        """意图反思与重写器"""
        question = state.get("search_query") or state.get("original_query")
        prompt = ExpertPrompts.QUERY_REWRITER.format(question=question)

        res = await self.llm.ainvoke(prompt)
        new_query = res.content.strip()

        current_retries = state.get("retries", 0) + 1
        return {"search_query": new_query, "retries": current_retries, "intermediate_steps": [UIEventTypes.REWRITING]}

    async def node_retrieve_retry(self, state: AgenticState):
        """独立重试检索器：脱离 Agent 直接查库，防止大模型陷入工具选择死循环"""
        new_query = state.get("search_query")
        law_tool = self.tools.get(AgentToolNames.LAW_SEARCH)

        try:
            # 直接调用底层检索工具逻辑
            result = await law_tool.ainvoke({"query": new_query})
            return {"documents": [str(result)], "intermediate_steps": [UIEventTypes.RETRIEVING]}
        except Exception as e:
            logger.error(f"重试检索异常: {e}")
            return {"documents": [], "intermediate_steps": [UIEventTypes.RETRIEVING]}

    async def node_generate(self, state: AgenticState):
        """综合生成器"""
        question = state.get("original_query")
        documents = state.get("documents", [])
        messages = state.get("messages", [])

        # 收集工具产生的地图/天气数据
        tool_contexts = [m.content for m in messages if isinstance(m, ToolMessage)]
        all_context = "\n---\n".join(documents + tool_contexts)

        prompt = ExpertPrompts.FINAL_GENERATOR.format(context=all_context, question=question)
        res = await self.llm.ainvoke(prompt)

        return {"generation": res.content, "intermediate_steps": [UIEventTypes.GENERATING]}

    async def node_grade_hallucination(self, state: AgenticState):
        """幻觉质检员"""
        documents = state.get("documents", [])
        generation = state.get("generation", "")

        if not documents:
            return {"intermediate_steps": [UIEventTypes.CHECKING_HALLUCINATION]}

        prompt = ExpertPrompts.HALLUCINATION_GRADER.format(documents="\n".join(documents), generation=generation)
        res = await self.json_llm.ainvoke(prompt)

        grade = self._extract_json_score(res.content)
        if grade == GraderThresholds.SCORE_NO:
            current_retries = state.get("retries", 0) + 1
            return {"retries": current_retries, "intermediate_steps": [UIEventTypes.CHECKING_HALLUCINATION]}

        return {"intermediate_steps": [UIEventTypes.CHECKING_HALLUCINATION]}

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
        messages = state.get("messages", [])
        tool_calls = messages[-2].tool_calls if len(messages) >= 2 else []

        # 法律问题走打分车道，地图/生活服务问题直接走生成快车道
        for tc in tool_calls:
            if tc["name"] == AgentToolNames.LAW_SEARCH:
                return NodeNames.GRADE_DOCS
        return NodeNames.GENERATE

    def route_from_grade_docs(self, state: AgenticState) -> Literal[NodeNames.REWRITE, NodeNames.GENERATE]:
        documents = state.get("documents", [])
        if not documents:
            return NodeNames.REWRITE
        return NodeNames.GENERATE

    def route_from_rewrite(self, state: AgenticState) -> Literal[NodeNames.RETRIEVE_RETRY, NodeNames.GENERATE]:
        retries = state.get("retries", 0)
        # 超过上限硬性切断死循环
        if retries >= AgentLimits.MAX_RETRIES:
            logger.warning("达到最大重写次数，强制终止检索阶段。")
            return NodeNames.GENERATE
        return NodeNames.RETRIEVE_RETRY

    def route_from_grade_hallucination(self, state: AgenticState) -> Literal[NodeNames.REWRITE, END]:
        retries = state.get("retries", 0)
        # 如果发现幻觉，打回重写逻辑（若已达上限则放行，前端提示可能存在风险）
        # 在极致严谨的系统中，达到上限可强制覆盖为 REJECTED 话术
        if retries < AgentLimits.MAX_RETRIES:
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