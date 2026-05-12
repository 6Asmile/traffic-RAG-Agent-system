from datetime import datetime

from sqlalchemy import Column, Integer, String, Text, DateTime, Float, JSON

from app.db.base import Base


class EvalDataset(Base):
    __tablename__ = "eval_datasets"
    id = Column(Integer, primary_key=True, index=True)
    question = Column(Text)
    reference_answer = Column(Text)
    category = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=datetime.now)


class EvalRun(Base):
    __tablename__ = "eval_runs"
    id = Column(Integer, primary_key=True, index=True)
    run_id = Column(String(50), unique=True, index=True)
    status = Column(String(20), default="running")
    total = Column(Integer, default=0)
    completed_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)


class EvalResult(Base):
    __tablename__ = "eval_results"
    id = Column(Integer, primary_key=True, index=True)
    dataset_id = Column(Integer)
    question = Column(Text)
    generated_answer = Column(Text)
    retrieved_contexts = Column(JSON, nullable=True)
    reference_answer = Column(Text)
    faithfulness = Column(Float, nullable=True)
    factual_correctness = Column(Float, nullable=True)
    response_relevancy = Column(Float, nullable=True)
    context_recall = Column(Float, nullable=True)
    avg_score = Column(Float, nullable=True)
    analysis = Column(JSON, nullable=True)
    status = Column(String(20), default="pending")
    run_id = Column(String(50))
    created_at = Column(DateTime, default=datetime.now)
