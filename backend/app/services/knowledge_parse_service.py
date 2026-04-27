from __future__ import annotations

import logging
from datetime import datetime
from typing import Optional

from sqlalchemy.orm import Session

from app.models.knowledge_parse_report import KnowledgeParseReport

logger = logging.getLogger(__name__)


class KnowledgeParseService:
    TABLE_READY = False

    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self._ensure_table()

    def _ensure_table(self):
        if not self.db or KnowledgeParseService.TABLE_READY:
            return
        try:
            bind = getattr(self.db, "bind", None)
            if not bind:
                return
            KnowledgeParseReport.__table__.create(bind=bind, checkfirst=True)
            KnowledgeParseService.TABLE_READY = True
        except Exception as e:
            logger.warning(f"KnowledgeParseService 初始化表失败: {e}")

    def _get_or_create(self, doc_id: int) -> KnowledgeParseReport:
        row = self.db.query(KnowledgeParseReport).filter(KnowledgeParseReport.doc_id == int(doc_id)).first()
        if row:
            return row
        row = KnowledgeParseReport(doc_id=int(doc_id))
        self.db.add(row)
        self.db.flush()
        return row

    def mark_processing(self, doc_id: int, parse_meta: Optional[dict] = None):
        if not self.db:
            return
        try:
            row = self._get_or_create(doc_id)
            row.parse_status = "processing"
            row.parse_error = ""
            row.parse_meta = parse_meta or row.parse_meta or {}
            row.quality_metrics = row.quality_metrics or {}
            row.started_at = datetime.now()
            row.finished_at = None
            row.updated_at = datetime.now()
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"mark_processing 失败: {e}")

    def mark_ready(self, doc_id: int, quality_metrics: Optional[dict] = None, parse_meta: Optional[dict] = None):
        if not self.db:
            return
        try:
            row = self._get_or_create(doc_id)
            row.parse_status = "ready"
            row.parse_error = ""
            row.quality_metrics = quality_metrics or {}
            row.parse_meta = parse_meta or row.parse_meta or {}
            if not row.started_at:
                row.started_at = datetime.now()
            row.finished_at = datetime.now()
            row.updated_at = datetime.now()
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"mark_ready 失败: {e}")

    def mark_failed(self, doc_id: int, error: str, parse_meta: Optional[dict] = None):
        if not self.db:
            return
        try:
            row = self._get_or_create(doc_id)
            row.parse_status = "failed"
            row.parse_error = str(error or "")[:2000]
            row.parse_meta = parse_meta or row.parse_meta or {}
            row.quality_metrics = row.quality_metrics or {}
            if not row.started_at:
                row.started_at = datetime.now()
            row.finished_at = datetime.now()
            row.updated_at = datetime.now()
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"mark_failed 失败: {e}")

    def get_report_map(self, doc_ids: list[int]) -> dict[int, dict]:
        if not self.db or not doc_ids:
            return {}
        try:
            rows = (
                self.db.query(KnowledgeParseReport)
                .filter(KnowledgeParseReport.doc_id.in_([int(x) for x in doc_ids]))
                .all()
            )
            result = {}
            for row in rows:
                result[int(row.doc_id)] = {
                    "parse_status": str(row.parse_status or "processing"),
                    "parse_error": str(row.parse_error or ""),
                    "parse_meta": row.parse_meta or {},
                    "quality_metrics": row.quality_metrics or {},
                    "started_at": row.started_at.isoformat() if row.started_at else None,
                    "finished_at": row.finished_at.isoformat() if row.finished_at else None,
                }
            return result
        except Exception as e:
            logger.warning(f"get_report_map 失败: {e}")
            return {}
