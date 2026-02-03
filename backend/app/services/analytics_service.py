from pyparsing import results
from sklearn.cluster import KMeans
import numpy as np
from sqlalchemy.orm import Session

from app.models import ChatMessage


def analyze_user_queries(db: Session, embedding_model):
    # 1. 提取所有用户提问
    messages = db.query(ChatMessage).filter(ChatMessage.role == "user").all()
    texts = [m.content for m in messages]

    if len(texts) < 10: return "数据不足，无法聚类"

    # 2. 向量化所有提问
    vectors = embedding_model.embed_documents(texts)

    # 3. K-Means 聚类 (设为 5 个中心)
    kmeans = KMeans(n_clusters=5, random_state=42)
    clusters = kmeans.fit_predict(vectors)

    # 4. 统计每个簇的高频词（简化逻辑）
    # ... 返回结果给前端展示 ECharts 词云或饼图 ...
    return results