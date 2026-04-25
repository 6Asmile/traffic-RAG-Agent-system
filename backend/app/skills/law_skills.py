# app/skills/law_skills.py

import json
import re
import logging
from typing import List
from langchain_core.tools import StructuredTool
from pydantic import BaseModel, Field

from app.core.agentic.agent_constants import AgentToolNames, AgentToolDesc, RAGToolConfig
from app.services.hybrid_search import (
    HybridRetrievalConfig,
    parallel_hybrid_retrieve,
    rerank_with_dynamic_threshold,
)

logger = logging.getLogger(__name__)


class LawSearchInput(BaseModel):
    query: str = Field(description=AgentToolDesc.LAW_SEARCH_QUERY)


def create_law_search_tool(vector_db, bm25_instance, bm25_corpus: List[str], reranker):
    """
    工厂函数：动态创建法规检索工具，实现与外部组件的解耦
    """

    def extract_law_metadata(text: str, index: int) -> dict:
        law_name_match = re.search(r"(《[^》]+》)", text)
        article_match = re.search(r"第[一二三四五六七八九十百千万0-9]+条", text)

        law_name = law_name_match.group(1) if law_name_match else ""
        article_no = article_match.group(0) if article_match else ""
        title_parts = [part for part in [law_name, article_no] if part]

        return {
            "type": "law",
            "title": " ".join(title_parts) if title_parts else f"法规依据 {index + 1}",
            "label": article_no or f"检索片段 {index + 1}",
            "law_name": law_name,
            "article_no": article_no,
            "content": text,
        }

    async def search_traffic_law_database(query: str) -> str:
        if not vector_db:
            print(f"[召回指标] query='{query[:30]}' | faiss=0 | bm25=0 | merged=0 | final=0 | recall_rate=0.0% | reason=no_vector_db")
            return RAGToolConfig.FALLBACK_MESSAGE

        # 1. 并行混合召回 + Weighted RRF 融合
        retrieval_config = HybridRetrievalConfig(
            faiss_top_k=RAGToolConfig.FAISS_TOP_K,
            bm25_top_n=RAGToolConfig.BM25_TOP_N,
            fusion_top_n=RAGToolConfig.FUSION_TOP_N,
            rerank_top_n=RAGToolConfig.RERANK_TOP_N,
            rrf_k=RAGToolConfig.RRF_K,
            weight_faiss=RAGToolConfig.RRF_WEIGHT_FAISS,
            weight_bm25=RAGToolConfig.RRF_WEIGHT_BM25,
            score_threshold=RAGToolConfig.SCORE_THRESHOLD,
            dynamic_margin=RAGToolConfig.DYNAMIC_MARGIN,
            min_keep=RAGToolConfig.MIN_KEEP,
        )
        retrieval_result = await parallel_hybrid_retrieve(
            query=query,
            vector_db=vector_db,
            bm25_instance=bm25_instance,
            bm25_corpus=bm25_corpus,
            config=retrieval_config,
        )
        faiss_docs = retrieval_result.get("faiss_docs", [])
        bm25_docs = retrieval_result.get("bm25_docs", [])
        candidate_list = retrieval_result.get("fused_docs", [])

        # 召回指标：各路召回数量
        faiss_count = len(faiss_docs)
        bm25_count = len(bm25_docs)
        merged_count = len(candidate_list)

        if not candidate_list:
            print(
                f"[召回指标] query='{query[:30]}' | "
                f"faiss={faiss_count} | bm25={bm25_count} | merged=0 | "
                f"final=0 | recall_rate=0.0%"
            )
            return RAGToolConfig.FALLBACK_MESSAGE

        # 2. Rerank + 动态阈值过滤（并保证最小保留）
        rerank_result = rerank_with_dynamic_threshold(
            query=query,
            candidates=candidate_list,
            reranker=reranker,
            config=retrieval_config,
        )
        final_docs = rerank_result.get("final_docs", [])
        rerank_scores = rerank_result.get("rerank_scores", [])
        threshold_used = float(rerank_result.get("threshold_used", RAGToolConfig.SCORE_THRESHOLD))
        top_score = float(rerank_result.get("top_score", 0.0))
        fallback_rerank = bool(rerank_result.get("fallback", False))

        if not final_docs:
            print(
                f"[召回指标] query='{query[:30]}' | "
                f"faiss={faiss_count} | bm25={bm25_count} | merged={merged_count} | "
                f"final=0 | recall_rate=0.0% | rerank_filtered_all=True | "
                f"threshold={threshold_used:.4f}"
            )
            return RAGToolConfig.FALLBACK_MESSAGE

        # 召回率 = final / merged × 100%
        recall_rate = len(final_docs) / merged_count * 100 if merged_count > 0 else 0
        avg_score = sum(rerank_scores) / len(rerank_scores) if rerank_scores else 0
        fusion_count = len(candidate_list)
        print(
            f"[召回指标] query='{query[:30]}' | "
            f"faiss={faiss_count} | bm25={bm25_count} | merged={merged_count} | fusion={fusion_count} | "
            f"final={len(final_docs)} | recall_rate={recall_rate:.1f}% | "
            f"avg_rerank={avg_score:.4f} | top_rerank={top_score:.4f} | "
            f"threshold={threshold_used:.4f} | rerank_fallback={fallback_rerank}"
        )

        # 3. 同时返回结构化来源与供模型使用的纯文本
        sources = [
            extract_law_metadata(doc, i)
            for i, doc in enumerate(final_docs)
        ]
        text_data = "\n\n".join([f"[检索片段 {i + 1}]: {d}" for i, d in enumerate(final_docs)])
        return json.dumps({"text_data": text_data, "sources": sources}, ensure_ascii=False)

    # 返回结构化工具
    return StructuredTool.from_function(
        coroutine=search_traffic_law_database,
        name=AgentToolNames.LAW_SEARCH,
        description=AgentToolDesc.LAW_SEARCH,
        args_schema=LawSearchInput,
    )
