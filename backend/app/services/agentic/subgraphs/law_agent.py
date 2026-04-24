from .schemas import LawHandoff


class LawAgentSubgraph:
    @staticmethod
    def build_handoff(law_docs: list[str], law_sources: list[dict], confidence: float = 0.7) -> LawHandoff:
        return {
            "law_docs": list(law_docs or []),
            "law_sources": list(law_sources or []),
            "source_count": len(law_sources or []),
            "confidence": float(confidence),
        }
