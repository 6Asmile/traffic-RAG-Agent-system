import re
from typing import List

from .schemas import RouterHandoff


class RouterAgentSubgraph:
    LAW_PATTERNS = [
        r"法规", r"法条", r"违法", r"违章", r"处罚", r"罚款", r"扣分", r"记分", r"责任", r"事故",
    ]
    TOOL_PATTERNS = [
        r"路线", r"导航", r"怎么走", r"到.+?怎么", r"附近", r"周边", r"停车", r"加油站", r"充电桩",
        r"天气", r"温度", r"降雨", r"路况",
    ]

    @classmethod
    def _match_any(cls, query: str, patterns: List[str]) -> bool:
        text = str(query or "")
        return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in patterns)

    @classmethod
    def build_handoff(cls, query: str, rewritten_query: str = "") -> RouterHandoff:
        source_query = str(rewritten_query or query or "").strip()
        need_law = cls._match_any(source_query, cls.LAW_PATTERNS)
        need_tool = cls._match_any(source_query, cls.TOOL_PATTERNS)

        if need_law and need_tool:
            task_type = "hybrid"
            reason_codes = ["law_keyword_hit", "tool_keyword_hit"]
        elif need_law:
            task_type = "law_only"
            reason_codes = ["law_keyword_hit"]
        elif need_tool:
            task_type = "tool_only"
            reason_codes = ["tool_keyword_hit"]
        else:
            task_type = "chat"
            reason_codes = ["fallback_chat"]

        return {
            "task_type": task_type,  # type: ignore[typeddict-item]
            "need_law": need_law,
            "need_tool": need_tool,
            "reason_codes": reason_codes,
            "rewritten_query": source_query or str(query or "").strip(),
        }
