# app/services/agentic/agentic_state.py

import operator
from typing import TypedDict, Annotated, Sequence, List
from langchain_core.messages import BaseMessage


class AgenticState(TypedDict):
    """
    Agentic RAG 全局状态对象 (State Management)
    该对象在 LangGraph 的各个 Node 之间流转与更新

    更新语义标注：
    - 【追加语义】使用 Annotated[..., operator.add]，节点返回值自动追加，不覆盖
    - 【替换语义】普通字段，节点返回值直接替换旧值
    - 【只读语义】仅在初始状态注入，各节点不应修改
    """

    # 【追加语义】当前轮消息链，各节点返回的 messages 自动追加到末尾
    messages: Annotated[Sequence[BaseMessage], operator.add]

    # 【只读语义】会话历史消息，仅作为本轮决策参考，各节点不应修改
    history_messages: Sequence[BaseMessage]

    # 【只读语义】跨轮滚动摘要，仅作为上下文参考，不在节点内回写
    memory_summary: str

    # 【只读语义】用户原始问题，全生命周期不变
    original_query: str

    # 【替换语义】供 RAG 检索使用的搜索词，由 node_rewrite / node_agent 更新
    search_query: str

    # 【替换语义】私有检索文档（仅内部使用，不直接透传前端）
    private_documents: List[str]

    # 【替换语义】公共引用源（允许透传前端展示）
    public_sources: List[dict]

    # 【追加语义】私有工具上下文（仅生成器使用）
    private_tool_contexts: Annotated[List[str], operator.add]

    # 【替换语义】私有工具调用记录（仅路由决策使用）
    private_latest_tool_names: List[str]

    # 【替换语义】相关性重试计数器，由 node_rewrite 递增后替换
    relevance_retries: int

    # 【替换语义】幻觉重试计数器，由 node_grade_hallucination 递增后替换
    hallucination_retries: int

    # 【替换语义】大模型生成的最终回答，由 node_generate 全量替换
    generation: str

    # 【替换语义】幻觉审查是否通过，由 node_grade_hallucination 替换
    hallucination_passed: bool

    # 【追加语义】流式输出状态追踪，各节点返回的步骤自动追加
    intermediate_steps: Annotated[List[str], operator.add]
