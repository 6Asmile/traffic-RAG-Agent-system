# app/services/agentic/agentic_state.py

import operator
from typing import TypedDict, Annotated, Sequence, List
from langchain_core.messages import BaseMessage


class AgenticState(TypedDict):
    """
    Agentic RAG 全局状态对象 (State Management)
    该对象在 LangGraph 的各个 Node 之间流转与更新
    """
    # 历史与当前消息（使用 operator.add 保证消息是追加而不是覆盖）
    messages: Annotated[Sequence[BaseMessage], operator.add]

    # 用户的原始问题
    original_query: str

    # 供 RAG 检索使用的搜索词（在触发重写节点时，此字段会被更新）
    search_query: str

    # 从知识库或工具中检索到的文档切片或数据
    documents: List[str]

    # 内部循环重试次数计数器（防死循环关键）
    retries: int

    # 大模型生成的最终回答
    generation: str

    # 流式输出状态追踪（用于向前端推送中间状态）
    intermediate_steps: Annotated[List[str], operator.add]