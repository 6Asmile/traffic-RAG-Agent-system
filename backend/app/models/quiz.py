from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, JSON
from app.db.base import Base
from datetime import datetime

class Question(Base):
    """题库表：存储 AI 生成的题目"""
    __tablename__ = "quiz_questions"
    id = Column(Integer, primary_key=True, index=True)
    source_doc_id = Column(Integer) # 关联的知识文档ID，用于溯源
    content = Column(Text)          # 题目描述
    options = Column(JSON)          # 选项 ["A. xxx", "B. xxx"...]
    correct_answer = Column(String(10)) # "A"
    explanation = Column(Text)      # 解析
    difficulty = Column(Integer, default=1) # 难度 1-5
    created_at = Column(DateTime, default=datetime.now)

class UserQuizRecord(Base):
    """用户刷题记录表"""
    __tablename__ = "user_quiz_records"
    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, index=True)
    question_id = Column(Integer, ForeignKey("quiz_questions.id"))
    user_answer = Column(String(10))
    is_correct = Column(Boolean)
    created_at = Column(DateTime, default=datetime.now)