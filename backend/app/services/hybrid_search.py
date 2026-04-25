import asyncio
from dataclasses import dataclass
from typing import Any, List

import jieba
from rank_bm25 import BM25Okapi


@dataclass
class HybridRetrievalConfig:
    faiss_top_k: int = 40
    bm25_top_n: int = 20
    fusion_top_n: int = 60
    rerank_top_n: int = 10
    rrf_k: int = 60
    weight_faiss: float = 0.6
    weight_bm25: float = 0.4
    score_threshold: float = 0.1
    dynamic_margin: float = 0.18
    min_keep: int = 3


def _extract_text(item: Any) -> str:
    value = getattr(item, "page_content", item)
    text = str(value or "").strip()
    return text


def _dedup_keep_rank(items: list[str]) -> list[str]:
    seen = set()
    deduped: list[str] = []
    for text in items:
        normalized = str(text or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _search_faiss_sync(vector_db, query: str, top_k: int) -> list[str]:
    if not vector_db or not query or top_k <= 0:
        return []
    docs = vector_db.similarity_search(query, k=top_k)
    return _dedup_keep_rank([_extract_text(doc) for doc in docs])


def _search_bm25_sync(bm25_instance, bm25_corpus: list[str], query: str, top_n: int) -> list[str]:
    if not bm25_instance or not query or top_n <= 0:
        return []
    tokenized_query = list(jieba.cut(query))
    scores = bm25_instance.get_scores(tokenized_query)
    top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_n]
    docs = [bm25_corpus[i] for i in top_indices if scores[i] > 0]
    return _dedup_keep_rank([_extract_text(doc) for doc in docs])


def weighted_rrf_fusion(
    faiss_docs: list[str],
    bm25_docs: list[str],
    top_n: int,
    rrf_k: int = 60,
    weight_faiss: float = 0.6,
    weight_bm25: float = 0.4,
) -> tuple[list[str], dict[str, float]]:
    score_map: dict[str, float] = {}
    for rank, text in enumerate(faiss_docs):
        score_map[text] = score_map.get(text, 0.0) + (weight_faiss / (rrf_k + rank + 1))
    for rank, text in enumerate(bm25_docs):
        score_map[text] = score_map.get(text, 0.0) + (weight_bm25 / (rrf_k + rank + 1))

    ranked = sorted(score_map.items(), key=lambda x: x[1], reverse=True)
    clipped = ranked[: max(int(top_n or 0), 1)]
    docs = [text for text, _ in clipped]
    scores = {text: score for text, score in clipped}
    return docs, scores


async def parallel_hybrid_retrieve(
    query: str,
    vector_db,
    bm25_instance,
    bm25_corpus: list[str],
    config: HybridRetrievalConfig,
) -> dict:
    faiss_task = asyncio.to_thread(_search_faiss_sync, vector_db, query, int(config.faiss_top_k))
    bm25_task = asyncio.to_thread(_search_bm25_sync, bm25_instance, bm25_corpus, query, int(config.bm25_top_n))
    faiss_docs, bm25_docs = await asyncio.gather(faiss_task, bm25_task)

    fused_docs, fused_scores = weighted_rrf_fusion(
        faiss_docs=faiss_docs,
        bm25_docs=bm25_docs,
        top_n=int(config.fusion_top_n),
        rrf_k=int(config.rrf_k),
        weight_faiss=float(config.weight_faiss),
        weight_bm25=float(config.weight_bm25),
    )
    return {
        "faiss_docs": faiss_docs,
        "bm25_docs": bm25_docs,
        "fused_docs": fused_docs,
        "fused_scores": fused_scores,
    }


def rerank_with_dynamic_threshold(
    query: str,
    candidates: list[str],
    reranker,
    config: HybridRetrievalConfig,
) -> dict:
    if not candidates:
        return {
            "final_docs": [],
            "rerank_scores": [],
            "threshold_used": float(config.score_threshold),
            "top_score": 0.0,
            "fallback": False,
        }

    try:
        rerank_results = reranker.rerank(query, candidates, top_n=int(config.rerank_top_n))
    except Exception:
        fallback_docs = candidates[: max(int(config.min_keep), 1)]
        return {
            "final_docs": fallback_docs,
            "rerank_scores": [],
            "threshold_used": float(config.score_threshold),
            "top_score": 0.0,
            "fallback": True,
        }

    scored_docs: list[tuple[str, float]] = []
    for row in rerank_results or []:
        if not isinstance(row, dict):
            continue
        idx = row.get("index")
        if idx is None:
            continue
        try:
            idx_int = int(idx)
        except (TypeError, ValueError):
            continue
        if idx_int < 0 or idx_int >= len(candidates):
            continue
        try:
            score = float(row.get("relevance_score", -1e9))
        except (TypeError, ValueError):
            score = -1e9
        scored_docs.append((candidates[idx_int], score))

    if not scored_docs:
        fallback_docs = candidates[: max(int(config.min_keep), 1)]
        return {
            "final_docs": fallback_docs,
            "rerank_scores": [],
            "threshold_used": float(config.score_threshold),
            "top_score": 0.0,
            "fallback": True,
        }

    top_score = max(score for _, score in scored_docs)
    dynamic_threshold = max(float(config.score_threshold), float(top_score) - float(config.dynamic_margin))
    final_docs = [doc for doc, score in scored_docs if score >= dynamic_threshold]

    min_keep = max(int(config.min_keep), 1)
    if len(final_docs) < min_keep:
        final_docs = [doc for doc, _ in scored_docs[:min_keep]]

    rerank_scores = [score for _, score in scored_docs]
    return {
        "final_docs": _dedup_keep_rank(final_docs),
        "rerank_scores": rerank_scores,
        "threshold_used": dynamic_threshold,
        "top_score": top_score,
        "fallback": False,
    }


class HybridSearcher:
    """
    兼容旧用法的 BM25 检索器。
    新项目建议直接使用 parallel_hybrid_retrieve + rerank_with_dynamic_threshold。
    """

    def __init__(self, documents: List[str]):
        self.tokenized_docs = [list(jieba.cut(doc)) for doc in documents]
        self.bm25 = BM25Okapi(self.tokenized_docs)
        self.documents = documents

    def search(self, query: str, top_k: int = 5):
        tokenized_query = list(jieba.cut(query))
        scores = self.bm25.get_scores(tokenized_query)
        top_indices = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:top_k]
        return [self.documents[i] for i in top_indices]

