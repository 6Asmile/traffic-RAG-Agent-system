from sqlalchemy import Column, Integer, String, Text, DateTime, JSON, Boolean
from app.db.base import Base
from datetime import datetime

class ChatSession(Base):
    __tablename__ = "chat_sessions"
    id = Column(String(50), primary_key=True, index=True)
    user_id = Column(Integer, index=True)  
    title = Column(String(255), default="新对话")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(String(50), index=True)
    role = Column(String(20)) # 'user' or 'ai'
    content = Column(Text)
    sources = Column(JSON, nullable=True) # 存入来源数组
    is_helpful = Column(Boolean, nullable=True)  # True: 点赞, False: 点踩
    feedback_note = Column(Text, nullable=True)  # 用户填写的具体反馈建议
    created_at = Column(DateTime, default=datetime.now)
