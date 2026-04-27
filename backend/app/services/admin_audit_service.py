import json
import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import func, or_
from sqlalchemy.orm import Session

from app.models.admin_audit_log import AdminAuditLog

logger = logging.getLogger(__name__)


class AdminAuditService:
    TABLE_READY = False
    DETAIL_MAX_BYTES = 60000

    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self._ensure_table()

    def _ensure_table(self):
        if not self.db or AdminAuditService.TABLE_READY:
            return
        try:
            bind = getattr(self.db, "bind", None)
            if not bind:
                return
            AdminAuditLog.__table__.create(bind=bind, checkfirst=True)
            AdminAuditService.TABLE_READY = True
        except Exception as e:
            logger.warning(f"AdminAuditService 初始化表失败: {e}")

    @staticmethod
    def _safe_text(value: Optional[str], default: str = "") -> str:
        if value is None:
            return default
        raw = str(value).strip()
        return raw if raw else default

    @classmethod
    def _safe_detail_json(cls, detail: Optional[dict | list | str]) -> str:
        if detail is None:
            return "{}"
        if isinstance(detail, str):
            payload = detail
        else:
            try:
                payload = json.dumps(detail, ensure_ascii=False)
            except Exception:
                payload = json.dumps({"raw": str(detail)}, ensure_ascii=False)

        payload_bytes = payload.encode("utf-8")
        if len(payload_bytes) <= cls.DETAIL_MAX_BYTES:
            return payload

        fallback = json.dumps(
            {
                "truncated": True,
                "detail_preview": payload[:2000],
                "max_bytes": cls.DETAIL_MAX_BYTES,
            },
            ensure_ascii=False,
        )
        if len(fallback.encode("utf-8")) <= cls.DETAIL_MAX_BYTES:
            return fallback
        return "{}"

    def record(
        self,
        actor_user_id: Optional[int | str],
        actor_username: Optional[str],
        action: str,
        target_type: str = "",
        target_id: str = "",
        result: str = "success",
        detail: Optional[dict | list | str] = None,
        client_ip: str = "",
        user_agent: str = "",
    ):
        if not self.db:
            return
        try:
            row = AdminAuditLog(
                actor_user_id=self._safe_text(actor_user_id, default="unknown"),
                actor_username=self._safe_text(actor_username, default=""),
                action=self._safe_text(action, default="unknown_action"),
                target_type=self._safe_text(target_type, default=""),
                target_id=self._safe_text(target_id, default=""),
                result=self._safe_text(result, default="success"),
                detail_json=self._safe_detail_json(detail),
                client_ip=self._safe_text(client_ip, default=""),
                user_agent=self._safe_text(user_agent, default=""),
            )
            self.db.add(row)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"AdminAuditService 记录失败: {e}")

    def list_logs(
        self,
        days: int = 7,
        action: str = "",
        actor_user_id: str = "",
        result: str = "",
        keyword: str = "",
        page: int = 1,
        page_size: int = 20,
    ) -> dict:
        if not self.db:
            return {
                "page": 1,
                "page_size": page_size,
                "total": 0,
                "items": [],
                "action_breakdown": [],
            }

        safe_days = max(int(days or 7), 1)
        safe_page = max(int(page or 1), 1)
        safe_page_size = min(max(int(page_size or 20), 1), 100)
        from_time = datetime.now() - timedelta(days=safe_days)

        q = self.db.query(AdminAuditLog).filter(AdminAuditLog.created_at >= from_time)

        normalized_action = self._safe_text(action, default="")
        if normalized_action:
            q = q.filter(AdminAuditLog.action == normalized_action)

        normalized_actor_user_id = self._safe_text(actor_user_id, default="")
        if normalized_actor_user_id:
            q = q.filter(AdminAuditLog.actor_user_id == normalized_actor_user_id)

        normalized_result = self._safe_text(result, default="")
        if normalized_result:
            q = q.filter(AdminAuditLog.result == normalized_result)

        normalized_keyword = self._safe_text(keyword, default="")
        if normalized_keyword:
            kw = f"%{normalized_keyword}%"
            q = q.filter(
                or_(
                    AdminAuditLog.actor_username.like(kw),
                    AdminAuditLog.action.like(kw),
                    AdminAuditLog.target_type.like(kw),
                    AdminAuditLog.target_id.like(kw),
                    AdminAuditLog.detail_json.like(kw),
                )
            )

        total = q.count()

        rows = (
            q.order_by(AdminAuditLog.created_at.desc(), AdminAuditLog.id.desc())
            .offset((safe_page - 1) * safe_page_size)
            .limit(safe_page_size)
            .all()
        )

        breakdown_rows = (
            q.with_entities(
                AdminAuditLog.action,
                func.count(AdminAuditLog.id).label("count"),
            )
            .group_by(AdminAuditLog.action)
            .order_by(func.count(AdminAuditLog.id).desc())
            .all()
        )

        return {
            "page": safe_page,
            "page_size": safe_page_size,
            "total": int(total or 0),
            "items": [
                {
                    "id": row.id,
                    "actor_user_id": row.actor_user_id,
                    "actor_username": row.actor_username,
                    "action": row.action,
                    "target_type": row.target_type,
                    "target_id": row.target_id,
                    "result": row.result,
                    "detail_json": row.detail_json,
                    "client_ip": row.client_ip,
                    "user_agent": row.user_agent,
                    "created_at": row.created_at.isoformat() if row.created_at else None,
                }
                for row in rows
            ],
            "action_breakdown": [
                {"action": str(row.action or ""), "count": int(row.count or 0)}
                for row in breakdown_rows
            ],
        }
