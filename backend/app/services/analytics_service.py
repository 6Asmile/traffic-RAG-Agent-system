# app/services/analytics_service.py 完整优化版
import re

import numpy as np
from sklearn.cluster import KMeans
from sklearn.feature_extraction.text import TfidfVectorizer
from sqlalchemy.orm import Session
from app.models import ChatMessage, HotTopic
import json
from app.core.prompts import ANALYTICS_SUMMARY_PROMPT

class AnalyticsService:
    def __init__(self, embedding_model, llm):
        self.embedding_model = embedding_model
        self.llm = llm

    async def perform_deep_analysis(self, db: Session):
        """深度聚类分析"""
        # 1. 提取最近 1000 条用户提问
        messages = db.query(ChatMessage).filter(ChatMessage.role == "user").all()
        texts = [m.content for m in messages if len(m.content) > 5]

        if len(texts) < 10: return "数据量不足，无法分析"

        # 2. 向量化
        vectors = self.embedding_model.embed_documents(texts)
        X = np.array(vectors)

        # 3. 聚类 (固定 5 簇，或动态计算)
        n_clusters = min(5, len(texts))
        kmeans = KMeans(n_clusters=n_clusters, n_init=10, random_state=42)
        labels = kmeans.fit_predict(X)

        # 4. 针对每一个簇进行 AI 总结和关键词提取
        db.query(HotTopic).delete()  # 清空旧记录

        for i in range(n_clusters):
            cluster_msgs = [texts[j] for j in range(len(texts)) if labels[j] == i]
            if not cluster_msgs: continue

            # 使用专业的分析提示词
            prompt = ANALYTICS_SUMMARY_PROMPT.format(cluster_messages="\n".join(cluster_msgs[:10]))
            try:
                res = self.llm.invoke(prompt)
                # 同样的 JSON 提取逻辑
                json_match = re.search(r'\{.*\}', res.content, re.DOTALL)
                analysis_result = json.loads(json_match.group())

                topic_name = analysis_result.get("topic_name", "未分类主题")
                keywords = analysis_result.get("keywords", ["交通"])
            except:
                topic_name = f"热点话题 {i + 1}"
                keywords = ["点击查看详情"]

            db.add(HotTopic(
                topic_name=topic_name,
                hit_count=len(cluster_msgs),
                keywords=keywords,
                representative_queries=cluster_msgs[:3]
            ))

        db.commit()
        return "分析完成"