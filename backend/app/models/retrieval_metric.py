from datetime import datetime

from sqlalchemy import Boolean, Column, DateTime, Float, Integer, String, Text

from app.db.base import Base


class RetrievalMetric(Base):
    __tablename__ = "retrieval_metrics"

    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(160), index=True, nullable=True)
    session_id = Column(String(80), index=True, nullable=False, default="default")
    user_id = Column(String(64), index=True, nullable=False, default="anonymous")
    mode = Column(String(32), index=True, nullable=False, default="fast")
    source = Column(String(48), index=True, nullable=False, default="unknown")
    query = Column(Text, nullable=False, default="")

    faiss_count = Column(Integer, nullable=False, default=0)
    bm25_count = Column(Integer, nullable=False, default=0)
    fusion_count = Column(Integer, nullable=False, default=0)
    final_count = Column(Integer, nullable=False, default=0)

    threshold_used = Column(Float, nullable=False, default=0.0)
    top_score = Column(Float, nullable=False, default=0.0)
    rerank_fallback = Column(Boolean, nullable=False, default=False)
    latency_ms = Column(Integer, nullable=False, default=0)

    created_at = Column(DateTime, default=datetime.now, index=True)

