# app/services/analytics_service.py
import re
import numpy as np
from sklearn.cluster import KMeans
from sqlalchemy.orm import Session
import json

from app.models import ChatMessage, HotTopic
from app.core.prompts import ANALYTICS_SUMMARY_PROMPT
from app.core.constants import AnalyticsConstants  # 🌟 引入数据分析常量

class AnalyticsService:
    def __init__(self, embedding_model, llm):
        self.embedding_model = embedding_model
        self.llm = llm

    async def perform_deep_analysis(self, db: Session):
        """深度聚类分析"""
        messages = db.query(ChatMessage).filter(ChatMessage.role == "user").all()
        # 使用常量替换硬编码的最小文本长度
        texts =[m.content for m in messages if len(m.content) > AnalyticsConstants.MIN_TEXT_LENGTH]

        # 使用常量替换最小样本数
        if len(texts) < AnalyticsConstants.MIN_SAMPLES:
            return "数据量不足，无法分析"

        vectors = self.embedding_model.embed_documents(texts)
        X = np.array(vectors)

        # 使用常量替换聚类核心参数
        n_clusters = min(AnalyticsConstants.MAX_CLUSTERS, len(texts))
        kmeans = KMeans(
            n_clusters=n_clusters,
            n_init=AnalyticsConstants.N_INIT,
            random_state=AnalyticsConstants.RANDOM_STATE
        )
        labels = kmeans.fit_predict(X)

        db.query(HotTopic).delete()

        for i in range(n_clusters):
            cluster_msgs = [texts[j] for j in range(len(texts)) if labels[j] == i]
            if not cluster_msgs: continue

            prompt = ANALYTICS_SUMMARY_PROMPT.format(cluster_messages="\n".join(cluster_msgs[:10]))
            try:
                res = self.llm.invoke(prompt)
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