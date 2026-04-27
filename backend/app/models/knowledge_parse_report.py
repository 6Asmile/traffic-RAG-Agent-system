from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text, JSON

from app.db.base import Base


class KnowledgeParseReport(Base):
    __tablename__ = "knowledge_parse_reports"

    id = Column(Integer, primary_key=True, index=True)
    doc_id = Column(Integer, unique=True, index=True, nullable=False)
    parse_status = Column(String(32), index=True, nullable=False, default="processing")
    parse_error = Column(Text, nullable=False, default="")
    parse_meta = Column(JSON, nullable=True)
    quality_metrics = Column(JSON, nullable=True)

    started_at = Column(DateTime, nullable=True)
    finished_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
