from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String

from app.db.base import Base


class AdminNoticeRead(Base):
    __tablename__ = "admin_notice_reads"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(String(64), index=True, nullable=False, default="unknown")
    notice_key = Column(String(120), index=True, nullable=False, default="")
    read_at = Column(DateTime, default=datetime.now, index=True)
