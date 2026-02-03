# app/services/analytics_service.py
import numpy as np
from sklearn.cluster import KMeans
from collections import Counter
from sqlalchemy.orm import Session
from app.models import ChatMessage
import logging

logger = logging.getLogger(__name__)


class AnalyticsService:
    def __init__(self, embedding_model):
        self.embedding_model = embedding_model

    def analyze_hot_topics(self, db: Session, n_clusters=5):
        """对用户提问进行聚类分析"""
        # 1. 从数据库提取所有用户提问
        messages = db.query(ChatMessage).filter(ChatMessage.role == "user").all()
        texts = [m.content for m in messages if len(m.content) > 2]

        if len(texts) < n_clusters:
            return []

        try:
            # 2. 将提问文本转化为向量
            # 注意：由于我们之前写了 AliyunEmbeddingWrapper，这里直接复用
            vectors = self.embedding_model.embed_documents(texts)
            X = np.array(vectors)

            # 3. 执行 K-Means 聚类
            kmeans = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
            kmeans.fit(X)

            # 4. 统计每个簇的数量并提取核心词（这里简化处理，取每个簇的代表性短语）
            results = []
            labels = kmeans.labels_
            for i in range(n_clusters):
                cluster_msgs = [texts[j] for j in range(len(texts)) if labels[j] == i]
                # 简单逻辑：取该簇中最短的一个句子作为“主题名”
                topic_name = min(cluster_msgs, key=len)
                results.append({
                    "topic": topic_name[:10],  # 截取前10个字作为标签
                    "count": len(cluster_msgs),
                    "full_texts": cluster_msgs[:5]  # 详情预览
                })

            # 按热度排序
            return sorted(results, key=lambda x: x['count'], reverse=True)
        except Exception as e:
            logger.error(f"聚类分析失败: {e}")
            return []