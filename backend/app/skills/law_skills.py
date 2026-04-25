# app/skills/law_skills.py

import jieba
import json
import re
import logging
from typing import List
from langchain_core.tools import StructuredTool
from langchain_core.documents import Document
from pydantic import BaseModel, Field

from app.core.agentic.agent_constants import AgentToolNames, AgentToolDesc, RAGToolConfig

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

        # 1. 混合检索
        faiss_docs = vector_db.similarity_search(query, k=RAGToolConfig.FAISS_TOP_K)

        bm25_docs = []
        if bm25_instance:
            tokenized_query = list(jieba.cut(query))
            scores = bm25_instance.get_scores(tokenized_query)
            top_n_idx = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:RAGToolConfig.BM25_TOP_N]
            bm25_docs = [Document(page_content=bm25_corpus[i]) for i in top_n_idx if scores[i] > 0]

        # 去重
        candidates_map = {d.page_content: d.page_content for d in faiss_docs + bm25_docs}
        candidate_list = list(candidates_map.values())

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

        # 2. Rerank 精排与分数截断
        final_docs = []
        rerank_scores = []
        try:
            rerank_results = reranker.rerank(query, candidate_list, top_n=RAGToolConfig.RERANK_TOP_N)
            for res in rerank_results:
                score = res.get('relevance_score', -100)
                idx = res.get('index')
                if score >= RAGToolConfig.SCORE_THRESHOLD:
                    final_docs.append(candidate_list[idx])
                    rerank_scores.append(score)
        except Exception:
            final_docs = candidate_list[:5]

        if not final_docs:
            print(
                f"[召回指标] query='{query[:30]}' | "
                f"faiss={faiss_count} | bm25={bm25_count} | merged={merged_count} | "
                f"final=0 | recall_rate=0.0% | rerank_filtered_all=True"
            )
            return RAGToolConfig.FALLBACK_MESSAGE

        # 召回率 = final / merged × 100%
        recall_rate = len(final_docs) / merged_count * 100 if merged_count > 0 else 0
        avg_score = sum(rerank_scores) / len(rerank_scores) if rerank_scores else 0
        top_score = max(rerank_scores) if rerank_scores else 0
        print(
            f"[召回指标] query='{query[:30]}' | "
            f"faiss={faiss_count} | bm25={bm25_count} | merged={merged_count} | "
            f"final={len(final_docs)} | recall_rate={recall_rate:.1f}% | "
            f"avg_rerank={avg_score:.4f} | top_rerank={top_score:.4f}"
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
