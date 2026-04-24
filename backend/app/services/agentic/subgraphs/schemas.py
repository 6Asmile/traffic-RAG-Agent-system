from typing import List, Literal, TypedDict


class RouterHandoff(TypedDict):
    task_type: Literal["law_only", "tool_only", "hybrid", "chat"]
    need_law: bool
    need_tool: bool
    reason_codes: List[str]
    rewritten_query: str


class LawHandoff(TypedDict):
    law_docs: List[str]
    law_sources: List[dict]
    source_count: int
    confidence: float


class ToolHandoff(TypedDict):
    tool_contexts: List[str]
    tool_names: List[str]
    widget_count: int


class SynthHandoff(TypedDict):
    answer: str
    used_law_sources: int
    used_tool_contexts: int
    used_memory_contexts: int


class JudgeHandoff(TypedDict):
    passed: bool
    risk_level: Literal["low", "medium", "high"]
    issues: List[str]
    actions: List[str]
