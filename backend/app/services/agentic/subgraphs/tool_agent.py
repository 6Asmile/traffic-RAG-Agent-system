from .schemas import ToolHandoff


class ToolAgentSubgraph:
    @staticmethod
    def build_handoff(tool_contexts: list[str], tool_names: list[str], widget_count: int = 0) -> ToolHandoff:
        return {
            "tool_contexts": list(tool_contexts or []),
            "tool_names": list(tool_names or []),
            "widget_count": int(widget_count or 0),
        }
