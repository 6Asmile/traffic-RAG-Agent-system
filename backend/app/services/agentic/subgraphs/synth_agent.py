from .schemas import SynthHandoff


class SynthAgentSubgraph:
    @staticmethod
    def build_handoff(answer: str, law_count: int, tool_count: int, memory_count: int) -> SynthHandoff:
        return {
            "answer": str(answer or ""),
            "used_law_sources": int(law_count or 0),
            "used_tool_contexts": int(tool_count or 0),
            "used_memory_contexts": int(memory_count or 0),
        }
