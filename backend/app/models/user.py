# app/models/user.py
from datetime import datetime

from sqlalchemy import Column, Integer, String, Boolean, DateTime, JSON
from app.db.base import Base

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), unique=True, index=True)
    hashed_password = Column(String(255)) # 存储加密后的密码
    role = Column(String(20), default="user") # user:普通用户, admin:管理员
    avatar = Column(String(500), nullable=True)
    # --- 新增：存储个人 AI 配置 ---
    ai_preferences = Column(JSON, nullable=True)
    created_at = Column(DateTime, default=datetime.now)