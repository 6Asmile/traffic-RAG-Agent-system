import re
from typing import List

from .schemas import RouterHandoff


class RouterAgentSubgraph:
    CAPABILITY_PATTERNS = {
        "law_search": [
            r"法规", r"法条", r"违法", r"违章", r"处罚", r"罚款", r"扣分", r"记分", r"责任", r"事故",
        ],
        "route_plan": [
            r"路线", r"导航", r"怎么走", r"到.+?怎么", r"从.+?到.+?", r"出行方案",
        ],
        "nearby_search": [
            r"附近", r"周边", r"停车", r"加油站", r"充电桩",
        ],
        "weather_query": [
            r"天气", r"温度", r"降雨", r"路况", r"下雨", r"下雪",
        ],
    }

    @classmethod
    def _match_any(cls, query: str, patterns: List[str]) -> bool:
        text = str(query or "")
        for pattern in patterns:
            candidate = str(pattern or "").strip()
            if not candidate:
                continue
            try:
                if re.search(candidate, text, flags=re.IGNORECASE):
                    return True
            except re.error:
                if candidate in text:
                    return True
        return False

    @classmethod
    def configure_capability_patterns(cls, extra_patterns: dict[str, list[str]] | None = None):
        if not isinstance(extra_patterns, dict):
            return
        for capability, patterns in extra_patterns.items():
            cap = str(capability or "").strip()
            if not cap:
                continue
            normalized = [str(p).strip() for p in (patterns or []) if str(p).strip()]
            if not normalized:
                continue
            merged = list(cls.CAPABILITY_PATTERNS.get(cap, []))
            seen = set(merged)
            for item in normalized:
                if item not in seen:
                    merged.append(item)
                    seen.add(item)
            cls.CAPABILITY_PATTERNS[cap] = merged

    @classmethod
    def build_handoff(cls, query: str, rewritten_query: str = "") -> RouterHandoff:
        source_query = str(rewritten_query or query or "").strip()
        need_capabilities: list[str] = []
        reason_codes: list[str] = []

        for capability, patterns in cls.CAPABILITY_PATTERNS.items():
            if cls._match_any(source_query, patterns):
                need_capabilities.append(capability)
                reason_codes.append(f"{capability}_hit")

        need_law = "law_search" in need_capabilities
        need_tool = any(cap != "law_search" for cap in need_capabilities)

        if need_law and need_tool:
            task_type = "hybrid"
        elif need_law:
            task_type = "law_only"
        elif need_tool:
            task_type = "tool_only"
        else:
            task_type = "chat"
            reason_codes = ["fallback_chat"]

        return {
            "task_type": task_type,  # type: ignore[typeddict-item]
            "need_law": need_law,
            "need_tool": need_tool,
            "need_capabilities": need_capabilities,
            "reason_codes": reason_codes,
            "rewritten_query": source_query or str(query or "").strip(),
        }
