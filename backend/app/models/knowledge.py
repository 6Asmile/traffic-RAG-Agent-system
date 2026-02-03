from datetime import datetime

from sqlalchemy import Column, Integer, String, DateTime

from app.db.base import Base


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"
    id = Column(Integer, primary_key=True)
    filename = Column(String(255))
    file_path = Column(String(500))
    upload_user_id = Column(Integer)
    created_at = Column(DateTime, default=datetime.now)

class KnowledgeDoc(Base):
    __tablename__ = "knowledge_docs"
    id = Column(Integer, primary_key=True, index=True)
    filename = Column(String(255))
    chunk_count = Column(Integer)
    upload_time = Column(DateTime, default=datetime.now)