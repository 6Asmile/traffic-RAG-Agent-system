from datetime import datetime

from sqlalchemy import Column, DateTime, Integer, String, Text
from sqlalchemy.dialects.mysql import LONGTEXT

from app.db.base import Base


class AdminRuntimeSetting(Base):
    __tablename__ = "admin_runtime_settings"

    id = Column(Integer, primary_key=True, index=True)
    setting_key = Column(String(80), unique=True, index=True, nullable=False)
    value_json = Column(Text().with_variant(LONGTEXT(), "mysql"), nullable=False, default="{}")
    updated_by = Column(String(64), nullable=False, default="system")
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now, index=True)
