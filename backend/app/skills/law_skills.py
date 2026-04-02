# app/skills/law_skills.py

import jieba
from typing import List
from langchain_core.tools import StructuredTool
from langchain_core.documents import Document
from pydantic import BaseModel, Field

from app.core.agentic.agent_constants import AgentToolNames, AgentToolDesc, RAGToolConfig


class LawSearchInput(BaseModel):
    query: str = Field(description=AgentToolDesc.LAW_SEARCH_QUERY)


def create_law_search_tool(vector_db, bm25_instance, bm25_corpus: List[str], reranker):
    """
    工厂函数：动态创建法规检索工具，实现与外部组件的解耦
    """

    async def search_traffic_law_database(query: str) -> str:
        if not vector_db:
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

        if not candidate_list:
            return RAGToolConfig.FALLBACK_MESSAGE

        # 2. Rerank 精排与分数截断
        final_docs = []
        try:
            rerank_results = reranker.rerank(query, candidate_list, top_n=RAGToolConfig.RERANK_TOP_N)
            for res in rerank_results:
                score = res.get('relevance_score', -100)
                idx = res.get('index')
                if score >= RAGToolConfig.SCORE_THRESHOLD:
                    final_docs.append(candidate_list[idx])
        except Exception:
            final_docs = candidate_list[:5]

        if not final_docs:
            return RAGToolConfig.FALLBACK_MESSAGE

        # 3. 组合并返回文本给大模型
        return "\n\n".join([f"[检索片段 {i + 1}]: {d}" for i, d in enumerate(final_docs)])

    # 返回结构化工具
    return StructuredTool.from_function(
        coroutine=search_traffic_law_database,
        name=AgentToolNames.LAW_SEARCH,
        description=AgentToolDesc.LAW_SEARCH,
        args_schema=LawSearchInput,
    )