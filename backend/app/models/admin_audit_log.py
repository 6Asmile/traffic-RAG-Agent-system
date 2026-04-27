from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT

from app.db.base import Base


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    actor_user_id = Column(String(64), index=True, nullable=False, default="unknown")
    actor_username = Column(String(80), index=True, nullable=False, default="")
    action = Column(String(64), index=True, nullable=False, default="unknown_action")
    target_type = Column(String(64), index=True, nullable=False, default="")
    target_id = Column(String(120), index=True, nullable=False, default="")
    result = Column(String(24), index=True, nullable=False, default="success")

    detail_json = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False, default="{}")
    client_ip = Column(String(64), nullable=False, default="")
    user_agent = Column(String(255), nullable=False, default="")
    created_at = Column(DateTime, default=datetime.now, index=True)
