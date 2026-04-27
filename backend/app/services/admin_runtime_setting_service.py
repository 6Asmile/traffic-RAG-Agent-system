import json
import logging
from typing import Optional

from sqlalchemy.orm import Session

from app.models.admin_runtime_setting import AdminRuntimeSetting

logger = logging.getLogger(__name__)


class AdminRuntimeSettingService:
    TABLE_READY = False

    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self._ensure_table()

    def _ensure_table(self):
        if not self.db or AdminRuntimeSettingService.TABLE_READY:
            return
        try:
            bind = getattr(self.db, "bind", None)
            if not bind:
                return
            AdminRuntimeSetting.__table__.create(bind=bind, checkfirst=True)
            AdminRuntimeSettingService.TABLE_READY = True
        except Exception as e:
            logger.warning(f"AdminRuntimeSettingService 初始化表失败: {e}")

    @staticmethod
    def _safe_json_dumps(data: dict) -> str:
        try:
            return json.dumps(data or {}, ensure_ascii=False)
        except Exception:
            return "{}"

    @staticmethod
    def _safe_json_loads(raw: str) -> dict:
        try:
            payload = json.loads(raw or "{}")
            return payload if isinstance(payload, dict) else {}
        except Exception:
            return {}

    def get(self, setting_key: str, default: Optional[dict] = None) -> dict:
        if not self.db:
            return default or {}
        key = str(setting_key or "").strip()
        if not key:
            return default or {}
        row = self.db.query(AdminRuntimeSetting).filter(AdminRuntimeSetting.setting_key == key).first()
        if not row:
            return default or {}
        return self._safe_json_loads(row.value_json)

    def upsert(self, setting_key: str, value: dict, updated_by: str = "system") -> dict:
        if not self.db:
            return {}
        key = str(setting_key or "").strip()
        if not key:
            return {}
        try:
            row = self.db.query(AdminRuntimeSetting).filter(AdminRuntimeSetting.setting_key == key).first()
            if not row:
                row = AdminRuntimeSetting(setting_key=key)
                self.db.add(row)
            row.value_json = self._safe_json_dumps(value or {})
            row.updated_by = str(updated_by or "system")
            self.db.commit()
            return self._safe_json_loads(row.value_json)
        except Exception as e:
            self.db.rollback()
            logger.warning(f"Runtime setting upsert 失败: {e}")
            return {}
