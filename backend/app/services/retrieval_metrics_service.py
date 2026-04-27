import logging
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy import case, func
from sqlalchemy.orm import Session

from app.models.retrieval_metric import RetrievalMetric

logger = logging.getLogger(__name__)


class RetrievalMetricsService:
    TABLE_READY = False

    def __init__(self, db: Optional[Session] = None):
        self.db = db
        self._ensure_table()

    def _ensure_table(self):
        if not self.db or RetrievalMetricsService.TABLE_READY:
            return
        try:
            bind = getattr(self.db, "bind", None)
            if not bind:
                return
            RetrievalMetric.__table__.create(bind=bind, checkfirst=True)
            RetrievalMetricsService.TABLE_READY = True
        except Exception as e:
            logger.warning(f"RetrievalMetricsService 初始化表失败: {e}")

    @staticmethod
    def _safe_user_id(user_id: Optional[int | str]) -> str:
        if user_id in (None, ""):
            return "anonymous"
        return str(user_id)

    def record(
        self,
        query: str,
        mode: str,
        source: str,
        faiss_count: int,
        bm25_count: int,
        fusion_count: int,
        final_count: int,
        threshold_used: float,
        top_score: float,
        rerank_fallback: bool,
        latency_ms: int,
        session_id: str = "default",
        user_id: Optional[int | str] = None,
        run_id: str = "",
    ):
        if not self.db:
            return
        try:
            row = RetrievalMetric(
                run_id=str(run_id or "").strip() or None,
                session_id=str(session_id or "default"),
                user_id=self._safe_user_id(user_id),
                mode=str(mode or "unknown"),
                source=str(source or "unknown"),
                query=str(query or "").strip(),
                faiss_count=int(faiss_count or 0),
                bm25_count=int(bm25_count or 0),
                fusion_count=int(fusion_count or 0),
                final_count=int(final_count or 0),
                threshold_used=float(threshold_used or 0.0),
                top_score=float(top_score or 0.0),
                rerank_fallback=bool(rerank_fallback),
                latency_ms=int(latency_ms or 0),
            )
            self.db.add(row)
            self.db.commit()
        except Exception as e:
            self.db.rollback()
            logger.warning(f"RetrievalMetricsService 记录失败: {e}")

    def dashboard(self, days: int = 7, mode: str = "", source: str = "") -> dict:
        if not self.db:
            return {"summary": {}, "trend": [], "mode_breakdown": []}

        safe_days = max(int(days or 7), 1)
        from_time = datetime.now() - timedelta(days=safe_days)
        q = self.db.query(RetrievalMetric).filter(RetrievalMetric.created_at >= from_time)

        normalized_mode = str(mode or "").strip()
        if normalized_mode:
            q = q.filter(RetrievalMetric.mode == normalized_mode)
        normalized_source = str(source or "").strip()
        if normalized_source:
            q = q.filter(RetrievalMetric.source == normalized_source)

        total = q.count()
        if total <= 0:
            return {
                "summary": {
                    "total_queries": 0,
                    "avg_faiss_count": 0.0,
                    "avg_bm25_count": 0.0,
                    "avg_fusion_count": 0.0,
                    "avg_final_count": 0.0,
                    "avg_threshold_used": 0.0,
                    "avg_top_score": 0.0,
                    "rerank_fallback_rate": 0.0,
                    "avg_latency_ms": 0.0,
                },
                "trend": [],
                "mode_breakdown": [],
            }

        summary_row = q.with_entities(
            func.count(RetrievalMetric.id),
            func.avg(RetrievalMetric.faiss_count),
            func.avg(RetrievalMetric.bm25_count),
            func.avg(RetrievalMetric.fusion_count),
            func.avg(RetrievalMetric.final_count),
            func.avg(RetrievalMetric.threshold_used),
            func.avg(RetrievalMetric.top_score),
            func.avg(case((RetrievalMetric.rerank_fallback == True, 1), else_=0)),
            func.avg(RetrievalMetric.latency_ms),
        ).first()

        trend_rows = q.with_entities(
            func.date(RetrievalMetric.created_at).label("d"),
            func.count(RetrievalMetric.id).label("total_queries"),
            func.avg(RetrievalMetric.fusion_count).label("avg_fusion_count"),
            func.avg(RetrievalMetric.final_count).label("avg_final_count"),
            func.avg(RetrievalMetric.threshold_used).label("avg_threshold"),
            func.avg(RetrievalMetric.top_score).label("avg_top_score"),
            func.avg(case((RetrievalMetric.rerank_fallback == True, 1), else_=0)).label("fallback_rate"),
            func.avg(RetrievalMetric.latency_ms).label("avg_latency_ms"),
        ).group_by(func.date(RetrievalMetric.created_at)).order_by(func.date(RetrievalMetric.created_at).asc()).all()

        mode_rows = q.with_entities(
            RetrievalMetric.mode,
            func.count(RetrievalMetric.id).label("total_queries"),
            func.avg(RetrievalMetric.final_count).label("avg_final_count"),
            func.avg(case((RetrievalMetric.rerank_fallback == True, 1), else_=0)).label("fallback_rate"),
            func.avg(RetrievalMetric.latency_ms).label("avg_latency_ms"),
        ).group_by(RetrievalMetric.mode).order_by(func.count(RetrievalMetric.id).desc()).all()

        return {
            "summary": {
                "total_queries": int(summary_row[0] or 0),
                "avg_faiss_count": round(float(summary_row[1] or 0.0), 4),
                "avg_bm25_count": round(float(summary_row[2] or 0.0), 4),
                "avg_fusion_count": round(float(summary_row[3] or 0.0), 4),
                "avg_final_count": round(float(summary_row[4] or 0.0), 4),
                "avg_threshold_used": round(float(summary_row[5] or 0.0), 6),
                "avg_top_score": round(float(summary_row[6] or 0.0), 6),
                "rerank_fallback_rate": round(float(summary_row[7] or 0.0), 6),
                "avg_latency_ms": round(float(summary_row[8] or 0.0), 2),
            },
            "trend": [
                {
                    "date": str(row.d),
                    "total_queries": int(row.total_queries or 0),
                    "avg_fusion_count": round(float(row.avg_fusion_count or 0.0), 4),
                    "avg_final_count": round(float(row.avg_final_count or 0.0), 4),
                    "avg_threshold_used": round(float(row.avg_threshold or 0.0), 6),
                    "avg_top_score": round(float(row.avg_top_score or 0.0), 6),
                    "rerank_fallback_rate": round(float(row.fallback_rate or 0.0), 6),
                    "avg_latency_ms": round(float(row.avg_latency_ms or 0.0), 2),
                }
                for row in trend_rows
            ],
            "mode_breakdown": [
                {
                    "mode": str(row.mode or "unknown"),
                    "total_queries": int(row.total_queries or 0),
                    "avg_final_count": round(float(row.avg_final_count or 0.0), 4),
                    "rerank_fallback_rate": round(float(row.fallback_rate or 0.0), 6),
                    "avg_latency_ms": round(float(row.avg_latency_ms or 0.0), 2),
                }
                for row in mode_rows
            ],
        }

    def run_details(self, run_id: str) -> list[dict]:
        if not self.db:
            return []
        safe_run_id = str(run_id or "").strip()
        if not safe_run_id:
            return []
        rows = (
            self.db.query(RetrievalMetric)
            .filter(RetrievalMetric.run_id == safe_run_id)
            .order_by(RetrievalMetric.created_at.asc(), RetrievalMetric.id.asc())
            .all()
        )
        return [self._to_item(row) for row in rows]

    @staticmethod
    def _to_item(row: RetrievalMetric) -> dict:
        return {
            "id": row.id,
            "run_id": row.run_id,
            "session_id": row.session_id,
            "user_id": row.user_id,
            "mode": row.mode,
            "source": row.source,
            "query": row.query,
            "faiss_count": row.faiss_count,
            "bm25_count": row.bm25_count,
            "fusion_count": row.fusion_count,
            "final_count": row.final_count,
            "threshold_used": row.threshold_used,
            "top_score": row.top_score,
            "rerank_fallback": bool(row.rerank_fallback),
            "latency_ms": row.latency_ms,
            "created_at": row.created_at.isoformat() if row.created_at else None,
        }

    def recent_items(
        self,
        days: int = 7,
        mode: str = "",
        source: str = "",
        limit: int = 20,
        run_only: bool = True,
    ) -> list[dict]:
        if not self.db:
            return []

        safe_days = max(int(days or 7), 1)
        safe_limit = min(max(int(limit or 20), 1), 100)
        from_time = datetime.now() - timedelta(days=safe_days)

        q = self.db.query(RetrievalMetric).filter(RetrievalMetric.created_at >= from_time)
        if run_only:
            q = q.filter(RetrievalMetric.run_id.isnot(None)).filter(RetrievalMetric.run_id != "")

        normalized_mode = str(mode or "").strip()
        if normalized_mode:
            q = q.filter(RetrievalMetric.mode == normalized_mode)
        normalized_source = str(source or "").strip()
        if normalized_source:
            q = q.filter(RetrievalMetric.source == normalized_source)

        rows = (
            q.order_by(RetrievalMetric.created_at.desc(), RetrievalMetric.id.desc())
            .limit(safe_limit)
            .all()
        )
        return [self._to_item(row) for row in rows]
