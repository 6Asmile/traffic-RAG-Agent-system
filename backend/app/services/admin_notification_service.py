import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.admin_notice_read import AdminNoticeRead

logger = logging.getLogger(__name__)


class AdminNotificationService:
    TABLE_READY = False

    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self._ensure_table()

    def _ensure_table(self):
        if not self.db or AdminNotificationService.TABLE_READY:
            return
        try:
            bind = getattr(self.db, "bind", None)
            if not bind:
                return
            AdminNoticeRead.__table__.create(bind=bind, checkfirst=True)
            AdminNotificationService.TABLE_READY = True
        except Exception as e:
            logger.warning(f"AdminNotificationService 初始化表失败: {e}")

    @staticmethod
    def _safe_user_id(user_id: Optional[int | str]) -> str:
        if user_id in (None, ""):
            return "unknown"
        return str(user_id)

    def load_read_keys(self, user_id: Optional[int | str]) -> set[str]:
        if not self.db:
            return set()
        safe_user_id = self._safe_user_id(user_id)
        rows = (
            self.db.query(AdminNoticeRead.notice_key)
            .filter(AdminNoticeRead.user_id == safe_user_id)
            .all()
        )
        return {str(row.notice_key or "").strip() for row in rows if str(row.notice_key or "").strip()}

    def mark_all_read(self, user_id: Optional[int | str], notice_keys: list[str]) -> int:
        if not self.db:
            return 0
        safe_user_id = self._safe_user_id(user_id)
        keys = [str(key or "").strip() for key in (notice_keys or []) if str(key or "").strip()]
        if not keys:
            return 0
        existing = (
            self.db.query(AdminNoticeRead.notice_key)
            .filter(AdminNoticeRead.user_id == safe_user_id)
            .all()
        )
        existing_set = {str(row.notice_key or "").strip() for row in existing}
        create_keys = [key for key in keys if key not in existing_set]
        if not create_keys:
            return 0
        try:
            self.db.add_all(
                [
                    AdminNoticeRead(user_id=safe_user_id, notice_key=key, read_at=datetime.now())
                    for key in create_keys
                ]
            )
            self.db.commit()
            return len(create_keys)
        except Exception as e:
            self.db.rollback()
            logger.warning(f"mark_all_read 写入失败: {e}")
            return 0
