# backend/app/api/endpoints/admin.py
from __future__ import annotations

import html
import os
import time
from datetime import datetime, timedelta
from typing import Optional

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Request, BackgroundTasks
from pydantic import BaseModel, Field
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.api.endpoints.chat import get_current_user
from app.core.config import settings
from app.core.constants import SystemConfig
from app.db.session import SessionLocal
from app.models import User, ChatSession, ChatMessage, AgentRun, AdminAuditLog
from app.models.config import AIConfig
from app.models.knowledge import KnowledgeDoc
from app.services.admin_audit_service import AdminAuditService
from app.services.admin_notification_service import AdminNotificationService
from app.services.admin_runtime_setting_service import AdminRuntimeSettingService
from app.services.knowledge_parse_service import KnowledgeParseService
from app.services.rag_service import RAGService
from app.services.retrieval_metrics_service import RetrievalMetricsService

router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 校验超级管理员权限的依赖
def verify_admin(current_user: User = Depends(get_current_user)):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="无权访问管理员控制台")
    return current_user


def _write_admin_audit(
    db: Session,
    current_user: User,
    action: str,
    target_type: str = "",
    target_id: str = "",
    detail: Optional[dict] = None,
    result: str = "success",
    request: Optional[Request] = None,
):
    ip = ""
    user_agent = ""
    if request:
        forwarded = str(request.headers.get("x-forwarded-for", "") or "").strip()
        if forwarded:
            ip = forwarded.split(",")[0].strip()
        if not ip and request.client:
            ip = str(request.client.host or "")
        user_agent = str(request.headers.get("user-agent", "") or "")

    AdminAuditService(db).record(
        actor_user_id=getattr(current_user, "id", None),
        actor_username=getattr(current_user, "username", ""),
        action=action,
        target_type=target_type,
        target_id=target_id,
        result=result,
        detail=detail or {},
        client_ip=ip,
        user_agent=user_agent,
    )


def _safe_pct(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _calc_p95(values: list[float | int]) -> float:
    cleaned = sorted([float(v) for v in values if v is not None])
    if not cleaned:
        return 0.0
    idx = int((len(cleaned) - 1) * 0.95)
    return round(cleaned[idx], 2)


def _check_mysql(db: Session) -> tuple[str, str]:
    try:
        db.execute(text("SELECT 1"))
        return "ok", ""
    except Exception as e:
        return "error", str(e)


def _check_redis() -> tuple[str, str]:
    try:
        import redis
        r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, socket_timeout=1)
        if r.ping():
            return "ok", ""
        return "error", "redis ping failed"
    except Exception as e:
        return "error", str(e)


def _check_llm_api(db: Session) -> tuple[str, str, int]:
    cfg = db.query(AIConfig).filter(AIConfig.config_type == "llm", AIConfig.is_active == True).first()
    if not cfg:
        return "error", "未配置活跃 LLM", 0
    started = time.perf_counter()
    try:
        test_url = f"{str(cfg.base_url or '').rstrip('/')}/models"
        with httpx.Client(timeout=3.5) as client:
            resp = client.get(test_url, headers={"Authorization": f"Bearer {cfg.api_key}"})
        latency_ms = int((time.perf_counter() - started) * 1000)
        if resp.status_code in (200, 401, 403):
            return "ok", "", latency_ms
        return "error", f"http_{resp.status_code}", latency_ms
    except Exception as e:
        latency_ms = int((time.perf_counter() - started) * 1000)
        return "error", str(e), latency_ms


def _check_amap_api() -> tuple[str, str]:
    amap_key = str(settings.AMAP_KEY or "").strip()
    if not amap_key:
        return "error", "未配置 AMAP_KEY"
    try:
        # 使用固定城市做轻量探针，不依赖业务参数
        with httpx.Client(timeout=3.5) as client:
            resp = client.get(
                "https://restapi.amap.com/v3/weather/weatherInfo",
                params={"city": "110000", "key": amap_key},
            )
        if resp.status_code != 200:
            return "error", f"http_{resp.status_code}"
        payload = resp.json() if resp.text else {}
        if str(payload.get("status", "")) == "1":
            return "ok", ""
        return "error", str(payload.get("info", "amap_error"))
    except Exception as e:
        return "error", str(e)


def _check_vector_db() -> tuple[str, str]:
    index_path = os.path.abspath(os.path.join(settings.BASE_DIR, SystemConfig.FAISS_INDEX_DIR))
    faiss_file = os.path.join(index_path, "index.faiss")
    if os.path.exists(faiss_file):
        return "ok", ""
    return "error", "未发现 index.faiss"


def _build_health_history(db: Session, hours: int = 12) -> list[dict]:
    now = datetime.now()
    start_time = now - timedelta(hours=max(int(hours), 1))
    points = []
    for i in range(hours):
        bucket_start = start_time + timedelta(hours=i)
        bucket_end = bucket_start + timedelta(hours=1)
        total = db.query(AgentRun.id).filter(
            AgentRun.started_at >= bucket_start,
            AgentRun.started_at < bucket_end,
        ).count()
        failed = db.query(AgentRun.id).filter(
            AgentRun.started_at >= bucket_start,
            AgentRun.started_at < bucket_end,
            AgentRun.status == "failed",
        ).count()
        rate = _safe_pct(total - failed, total) if total > 0 else 100.0
        points.append(
            {
                "hour": bucket_start.strftime("%m-%d %H:00"),
                "total_runs": total,
                "failed_runs": failed,
                "success_rate": rate,
            }
        )
    return points


def _build_admin_notices(db: Session) -> list[dict]:
    now = datetime.now()
    retrieval_service = RetrievalMetricsService(db)
    retrieval_summary = retrieval_service.dashboard(days=1).get("summary", {})
    total_queries = int(retrieval_summary.get("total_queries", 0) or 0)
    avg_latency = float(retrieval_summary.get("avg_latency_ms", 0.0) or 0.0)
    avg_final = float(retrieval_summary.get("avg_final_count", 0.0) or 0.0)

    mysql_status, mysql_error = _check_mysql(db)
    redis_status, redis_error = _check_redis()
    llm_status, llm_error, _ = _check_llm_api(db)
    amap_status, amap_error = _check_amap_api()
    vector_status, vector_error = _check_vector_db()

    notices = []
    if mysql_status != "ok":
        notices.append(
            {
                "key": "dep_mysql_down",
                "severity": "error",
                "title": "MySQL 异常",
                "message": mysql_error or "MySQL 探针失败",
                "page_id": "system",
                "route": "/admin?tab=system",
            }
        )
    if redis_status != "ok":
        notices.append(
            {
                "key": "dep_redis_down",
                "severity": "error",
                "title": "Redis 异常",
                "message": redis_error or "Redis 探针失败",
                "page_id": "system",
                "route": "/admin?tab=system",
            }
        )
    if llm_status != "ok":
        notices.append(
            {
                "key": "dep_llm_degraded",
                "severity": "warn",
                "title": "LLM 接口波动",
                "message": llm_error or "模型接口不可用",
                "page_id": "system",
                "route": "/admin?tab=system",
            }
        )
    if amap_status != "ok":
        notices.append(
            {
                "key": "dep_amap_degraded",
                "severity": "warn",
                "title": "地图能力异常",
                "message": amap_error or "高德探针失败",
                "page_id": "system",
                "route": "/admin?tab=system",
            }
        )
    if vector_status != "ok":
        notices.append(
            {
                "key": "dep_vector_missing",
                "severity": "warn",
                "title": "向量索引缺失",
                "message": vector_error or "FAISS 索引文件不存在",
                "page_id": "knowledge",
                "route": "/admin?tab=knowledge",
            }
        )
    if total_queries >= 10 and avg_final < 2:
        notices.append(
            {
                "key": "retrieval_quality_low",
                "severity": "warn",
                "title": "检索命中偏低",
                "message": f"近24小时平均最终命中仅 {avg_final:.2f}",
                "page_id": "retrieval",
                "route": "/admin?tab=retrieval",
            }
        )
    if total_queries >= 10 and avg_latency > 12000:
        notices.append(
            {
                "key": "retrieval_latency_high",
                "severity": "warn",
                "title": "检索时延升高",
                "message": f"近24小时平均检索耗时 {avg_latency:.0f}ms",
                "page_id": "retrieval",
                "route": "/admin?tab=retrieval",
            }
        )
    if not notices:
        notices.append(
            {
                "key": "system_nominal",
                "severity": "info",
                "title": "系统运行正常",
                "message": "核心依赖健康，暂无高风险告警。",
                "page_id": "dashboard",
                "route": "/admin?tab=dashboard",
            }
        )

    for item in notices:
        item["created_at"] = now.isoformat()
    return notices


@router.get("/dashboard/stats")
def get_dashboard_stats(
    days: int = Query(default=7, ge=1, le=30),
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    """获取后台看板数据（总量 + 质量指标）。"""
    safe_days = max(int(days or 7), 1)

    # 1. 总量指标
    total_users = db.query(User).count()
    chunk_result = db.query(func.sum(KnowledgeDoc.chunk_count)).scalar()
    total_chunks = int(chunk_result) if chunk_result else 0

    # 2. 缓存命中率
    cache_hit_rate = 0.0
    try:
        import redis
        r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True)
        hits = int(r.get("metrics:cache_hits") or 0)
        queries = int(r.get("metrics:total_queries") or 0)
        if queries > 0:
            cache_hit_rate = round((hits / queries) * 100, 2)
    except Exception:
        pass

    # 3. token 趋势
    today = datetime.now().date()
    labels = [(today - timedelta(days=i)).strftime("%m-%d") for i in range(safe_days - 1, -1, -1)]
    input_tokens = [0] * safe_days
    output_tokens = [0] * safe_days

    from_time = datetime.now() - timedelta(days=safe_days)
    recent_msgs = db.query(ChatMessage.created_at, ChatMessage.content, ChatMessage.role).filter(
        ChatMessage.created_at >= from_time
    ).all()
    label_to_index = {label: idx for idx, label in enumerate(labels)}
    for msg in recent_msgs:
        day_str = msg.created_at.strftime("%m-%d")
        idx = label_to_index.get(day_str)
        if idx is None:
            continue
        token_count = int(len(msg.content or "") * 1.5)
        if msg.role == "user":
            input_tokens[idx] += token_count
        elif msg.role == "ai":
            output_tokens[idx] += token_count
    total_tokens = sum(input_tokens) + sum(output_tokens)
    estimated_cost = round((total_tokens / 1000) * 0.002, 4)

    # 4. 高频用户
    top_users = (
        db.query(User.username, func.count(ChatMessage.id).label("msg_count"))
        .join(ChatSession, User.id == ChatSession.user_id)
        .join(ChatMessage, ChatSession.id == ChatMessage.session_id)
        .filter(ChatMessage.role == "user", ChatMessage.created_at >= from_time)
        .group_by(User.username)
        .order_by(func.count(ChatMessage.id).desc())
        .limit(5)
        .all()
    )

    # 5. 质量指标（基于 retrieval_metrics）
    retrieval_service = RetrievalMetricsService(db)
    retrieval_data = retrieval_service.dashboard(days=safe_days)
    total_retrieval = int(retrieval_data.get("summary", {}).get("total_queries", 0) or 0)
    rows = retrieval_service.recent_items(days=safe_days, limit=500, run_only=False)
    success_count = sum(1 for row in rows if int(row.get("final_count", 0) or 0) > 0)
    success_rate = _safe_pct(success_count, total_retrieval)
    failure_rate = round(max(0.0, 100.0 - success_rate), 2)
    p95_latency = _calc_p95([row.get("latency_ms", 0) for row in rows])

    anomaly_flags = []
    if success_rate < 65 and total_retrieval >= 10:
        anomaly_flags.append("success_rate_low")
    if p95_latency > 12000 and total_retrieval >= 10:
        anomaly_flags.append("latency_p95_high")
    if cache_hit_rate < 5 and total_retrieval >= 10:
        anomaly_flags.append("cache_hit_low")

    return {
        "days": safe_days,
        "metrics": {
            "total_users": total_users,
            "total_chunks": total_chunks,
            "cache_hit_rate": f"{cache_hit_rate}%",
            "estimated_cost": f"${estimated_cost}",
            "success_rate": success_rate,
            "failure_rate": failure_rate,
            "p95_latency_ms": p95_latency,
            "retrieval_total": total_retrieval,
            "anomaly_flags": anomaly_flags,
        },
        "chart": {
            "days": labels,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
        },
        "top_users": [{"username": u[0], "count": u[1]} for u in top_users],
    }


@router.get("/configs")
def get_ai_configs(db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """获取所有 AI 引擎配置"""
    configs = db.query(AIConfig).all()
    return configs


@router.patch("/configs/{config_id}/activate")
def activate_ai_config(
    config_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    """热切换 AI 模型"""
    target_config = db.query(AIConfig).filter(AIConfig.id == config_id).first()
    if not target_config:
        raise HTTPException(status_code=404, detail="配置不存在")

    # 将同类型的其他配置设为不活跃
    db.query(AIConfig).filter(AIConfig.config_type == target_config.config_type).update({"is_active": False})
    # 激活目标配置
    target_config.is_active = True
    db.commit()
    _write_admin_audit(
        db=db,
        current_user=current_user,
        action="activate_ai_config",
        target_type="ai_config",
        target_id=str(target_config.id),
        detail={
            "config_type": target_config.config_type,
            "model_name": target_config.model_name,
            "provider_name": target_config.provider_name,
        },
        request=request,
    )
    return {"status": "success", "message": f"已成功切换至 {target_config.model_name}"}


@router.get("/users")
def get_user_list(db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """获取用户列表"""
    users = db.query(
        User.id,
        User.username,
        User.role,
        User.is_active,
        User.created_at
    ).all()
    return [{"id": u.id, "username": u.username, "role": u.role, "created_at": u.created_at,"is_active":u.is_active} for u in users]


@router.get("/system/status")
def get_system_status(db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """系统链路探针（含依赖健康、错误概览、历史趋势）。"""
    mysql_status, mysql_error = _check_mysql(db)
    redis_status, redis_error = _check_redis()
    llm_status, llm_error, llm_latency = _check_llm_api(db)
    amap_status, amap_error = _check_amap_api()
    vector_status, vector_error = _check_vector_db()

    one_hour_ago = datetime.now() - timedelta(hours=1)
    run_failed_1h = (
        db.query(AgentRun.id)
        .filter(AgentRun.updated_at >= one_hour_ago, AgentRun.status == "failed")
        .count()
    )
    audit_failed_1h = (
        db.query(AdminAuditLog.id)
        .filter(AdminAuditLog.created_at >= one_hour_ago, AdminAuditLog.result == "failed")
        .count()
    )
    error_count_1h = int(run_failed_1h or 0) + int(audit_failed_1h or 0)

    last_run_error = (
        db.query(AgentRun.error, AgentRun.updated_at)
        .filter(AgentRun.status == "failed", AgentRun.error != "")
        .order_by(AgentRun.updated_at.desc())
        .first()
    )
    last_audit_error = (
        db.query(AdminAuditLog.detail_json, AdminAuditLog.created_at)
        .filter(AdminAuditLog.result == "failed")
        .order_by(AdminAuditLog.created_at.desc())
        .first()
    )

    last_error_msg = ""
    last_error_at = None
    if last_run_error and last_run_error[0]:
        last_error_msg = str(last_run_error[0])
        last_error_at = last_run_error[1]
    if last_audit_error and (not last_error_at or (last_audit_error[1] and last_audit_error[1] > last_error_at)):
        last_error_msg = str(last_audit_error[0] or "")
        last_error_at = last_audit_error[1]

    return {
        "mysql": mysql_status,
        "redis": redis_status,
        "llm_api": llm_status,
        "amap_api": amap_status,
        "vector_db": vector_status,
        "tool_services": "ok" if llm_status == "ok" and amap_status == "ok" else "degraded",
        "llm_latency_ms": llm_latency,
        "error_count_1h": error_count_1h,
        "last_error": last_error_msg,
        "last_error_at": last_error_at.isoformat() if last_error_at else None,
        "errors": {
            "mysql": mysql_error,
            "redis": redis_error,
            "llm_api": llm_error,
            "amap_api": amap_error,
            "vector_db": vector_error,
        },
        "health_history": _build_health_history(db, hours=12),
    }


@router.get("/retrieval/metrics")
def get_retrieval_metrics_dashboard(
    days: int = Query(default=7, ge=1, le=60, description="统计天数窗口"),
    mode: str = Query(default="", description="可选: fast/expert"),
    source: str = Query(default="", description="可选: 指标来源"),
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    """检索质量看板数据（聚合 + 趋势 + 模式拆分）"""
    service = RetrievalMetricsService(db)
    data = service.dashboard(days=days, mode=mode, source=source)
    return {
        "days": days,
        "mode": mode,
        "source": source,
        **data,
    }


@router.get("/retrieval/runs/{run_id}")
def get_retrieval_run_details(
    run_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    """按 run_id 查看单次专家检索链路明细"""
    service = RetrievalMetricsService(db)
    items = service.run_details(run_id)
    return {
        "run_id": run_id,
        "count": len(items),
        "items": items,
    }


@router.get("/retrieval/runs_recent")
def get_recent_retrieval_run_details(
    days: int = Query(default=7, ge=1, le=60, description="统计天数窗口"),
    mode: str = Query(default="", description="可选: fast/expert"),
    source: str = Query(default="", description="可选: 指标来源"),
    run_only: bool = Query(default=False, description="是否仅保留 run_id 非空记录"),
    limit: int = Query(default=30, ge=1, le=100, description="最近明细条数"),
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    """获取最近 run 明细（无需输入 run_id）"""
    service = RetrievalMetricsService(db)
    items = service.recent_items(days=days, mode=mode, source=source, limit=limit, run_only=run_only)
    return {
        "days": days,
        "mode": mode,
        "source": source,
        "run_only": run_only,
        "limit": limit,
        "count": len(items),
        "items": items,
    }


@router.get("/audit/logs")
def get_admin_audit_logs(
    days: int = Query(default=7, ge=1, le=90, description="统计天数窗口"),
    action: str = Query(default="", description="可选: 操作类型"),
    actor_user_id: str = Query(default="", description="可选: 操作人用户ID"),
    result: str = Query(default="", description="可选: success/failed"),
    keyword: str = Query(default="", description="可选: 关键词匹配"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    service = AdminAuditService(db)
    return service.list_logs(
        days=days,
        action=action,
        actor_user_id=actor_user_id,
        result=result,
        keyword=keyword,
        page=page,
        page_size=page_size,
    )


@router.get("/notifications")
def get_admin_notifications(
    unread_only: bool = Query(default=False),
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    notices = _build_admin_notices(db)
    read_service = AdminNotificationService(db)
    read_keys = read_service.load_read_keys(getattr(current_user, "id", None))

    items = []
    for item in notices:
        key = str(item.get("key", "") or "")
        merged = dict(item)
        merged["read"] = key in read_keys
        items.append(merged)
    if unread_only:
        items = [item for item in items if not bool(item.get("read", False))]

    severity_rank = {"error": 0, "warn": 1, "info": 2}
    items.sort(key=lambda x: (severity_rank.get(str(x.get("severity", "info")), 2), str(x.get("key", ""))))
    return {
        "unread_count": len([item for item in items if not item.get("read", False)]),
        "items": items,
    }


@router.post("/notifications/read_all")
def mark_admin_notifications_read_all(
    payload: NoticeReadAllRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    provided_keys = [str(key or "").strip() for key in (payload.notice_keys or []) if str(key or "").strip()]
    notice_keys = provided_keys if provided_keys else [item.get("key", "") for item in _build_admin_notices(db)]
    service = AdminNotificationService(db)
    created = service.mark_all_read(getattr(current_user, "id", None), notice_keys)
    return {"status": "success", "marked": created, "total": len(notice_keys)}


@router.get("/model/rollout")
def get_model_rollout(
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    service = AdminRuntimeSettingService(db)
    llm_cfg = service.get(
        "llm_rollout",
        default={"enabled": False, "baseline_config_id": 0, "canary_config_id": 0, "ratio_pct": 0},
    )
    emb_cfg = service.get(
        "embedding_rollout",
        default={"enabled": False, "baseline_config_id": 0, "canary_config_id": 0, "ratio_pct": 0},
    )
    llm_list = db.query(AIConfig).filter(AIConfig.config_type == "llm").all()
    emb_list = db.query(AIConfig).filter(AIConfig.config_type == "embedding").all()
    return {
        "llm": llm_cfg,
        "embedding": emb_cfg,
        "options": {
            "llm": [{"id": c.id, "model_name": c.model_name, "provider_name": c.provider_name} for c in llm_list],
            "embedding": [{"id": c.id, "model_name": c.model_name, "provider_name": c.provider_name} for c in emb_list],
        },
    }


@router.patch("/model/rollout")
def update_model_rollout(
    payload: ModelRolloutUpdate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    config_type = str(payload.config_type or "").strip().lower()
    if config_type not in {"llm", "embedding"}:
        raise HTTPException(status_code=400, detail="config_type 仅支持 llm / embedding")
    if payload.ratio_pct < 0 or payload.ratio_pct > 100:
        raise HTTPException(status_code=400, detail="ratio_pct 必须在 0-100")

    setting_key = "llm_rollout" if config_type == "llm" else "embedding_rollout"
    data = {
        "enabled": bool(payload.enabled),
        "baseline_config_id": int(payload.baseline_config_id or 0),
        "canary_config_id": int(payload.canary_config_id or 0),
        "ratio_pct": int(payload.ratio_pct or 0),
    }
    saved = AdminRuntimeSettingService(db).upsert(
        setting_key=setting_key,
        value=data,
        updated_by=str(getattr(current_user, "id", "admin")),
    )
    _write_admin_audit(
        db=db,
        current_user=current_user,
        action="update_model_rollout",
        target_type="runtime_setting",
        target_id=setting_key,
        detail=saved,
        request=request,
    )
    return {"status": "success", "config_type": config_type, "rollout": saved}


@router.post("/model/rollout/ping_compare")
def ping_compare_model_rollout(
    config_type: str = Query(..., description="llm/embedding"),
    baseline_config_id: int = Query(...),
    canary_config_id: int = Query(...),
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    cfg_type = str(config_type or "").strip().lower()
    if cfg_type not in {"llm", "embedding"}:
        raise HTTPException(status_code=400, detail="config_type 仅支持 llm / embedding")

    rows = (
        db.query(AIConfig)
        .filter(AIConfig.config_type == cfg_type, AIConfig.id.in_([baseline_config_id, canary_config_id]))
        .all()
    )
    if len(rows) != 2:
        raise HTTPException(status_code=404, detail="baseline/canary 配置不存在")

    def _ping_cfg(cfg: AIConfig) -> dict:
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=5.0) as client:
                resp = client.get(
                    f"{str(cfg.base_url or '').rstrip('/')}/models",
                    headers={"Authorization": f"Bearer {cfg.api_key}"},
                )
            delay = int((time.perf_counter() - started) * 1000)
            reachable = resp.status_code in (200, 401, 403)
            return {
                "id": cfg.id,
                "model_name": cfg.model_name,
                "provider_name": cfg.provider_name,
                "delay_ms": delay,
                "status": "ok" if reachable else f"http_{resp.status_code}",
            }
        except Exception as e:
            delay = int((time.perf_counter() - started) * 1000)
            return {
                "id": cfg.id,
                "model_name": cfg.model_name,
                "provider_name": cfg.provider_name,
                "delay_ms": delay,
                "status": f"error:{e}",
            }

    baseline = next(row for row in rows if row.id == baseline_config_id)
    canary = next(row for row in rows if row.id == canary_config_id)
    return {"baseline": _ping_cfg(baseline), "canary": _ping_cfg(canary)}


@router.get("/knowledge/docs_enhanced")
def get_knowledge_docs_enhanced(
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    docs = db.query(KnowledgeDoc).order_by(KnowledgeDoc.upload_time.desc()).all()
    now = datetime.now()
    report_map = KnowledgeParseService(db).get_report_map([int(doc.id) for doc in docs if getattr(doc, "id", None)])
    items = []
    for doc in docs:
        report = report_map.get(int(doc.id), {})
        chunks = doc.parsed_content if isinstance(doc.parsed_content, list) else []
        chunk_count = int(doc.chunk_count or 0)
        report_status = str(report.get("parse_status", "") or "").strip().lower()
        if report_status in {"processing", "ready", "failed"}:
            status = report_status
        elif chunk_count > 0:
            status = "ready"
        elif doc.upload_time and (now - doc.upload_time) <= timedelta(minutes=10):
            status = "processing"
        else:
            status = "failed"

        if status == "ready":
            progress = 100
        elif status == "processing":
            progress = 60
        else:
            progress = 0

        preview = [str(text)[:200] for text in (chunks[:3] if chunks else [])]
        filename_keyword = str(doc.filename or "").split(".")[0][:8]
        recall_hits_estimated = 0
        if filename_keyword:
            recall_hits_estimated = (
                db.query(ChatMessage.id)
                .filter(ChatMessage.role == "user", ChatMessage.content.like(f"%{filename_keyword}%"))
                .count()
            )

        items.append(
            {
                "id": doc.id,
                "filename": doc.filename,
                "chunk_count": chunk_count,
                "status": status,
                "progress_pct": progress,
                "upload_time": doc.upload_time.isoformat() if doc.upload_time else None,
                "chunk_preview": preview,
                "recall_hits_estimated": int(recall_hits_estimated or 0),
                "parse_error": str(report.get("parse_error", "") or ""),
                "parse_meta": report.get("parse_meta", {}) or {},
                "quality_metrics": report.get("quality_metrics", {}) or {},
                "parse_started_at": report.get("started_at"),
                "parse_finished_at": report.get("finished_at"),
            }
        )
    return {"items": items, "total": len(items)}


@router.get("/knowledge/{doc_id}/chunks")
def get_knowledge_doc_chunks(
    doc_id: int,
    limit: int = Query(default=60, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _: User = Depends(verify_admin),
):
    """获取单篇知识文档的切片详情（用于前端点击预览渲染）。"""
    doc = db.query(KnowledgeDoc).filter(KnowledgeDoc.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")

    chunks = doc.parsed_content if isinstance(doc.parsed_content, list) else []
    total = len(chunks)
    if total <= 0:
        return {
            "doc_id": doc_id,
            "filename": doc.filename,
            "total": 0,
            "offset": int(offset or 0),
            "limit": int(limit or 60),
            "items": [],
        }

    safe_offset = min(max(int(offset or 0), 0), max(total - 1, 0))
    safe_limit = max(int(limit or 60), 1)
    sliced = chunks[safe_offset: safe_offset + safe_limit]

    def _normalize_text(raw: object) -> str:
        text = html.unescape(str(raw or ""))
        return text.strip()

    items = []
    for idx, chunk in enumerate(sliced, start=safe_offset):
        content = _normalize_text(chunk)
        if not content:
            continue
        items.append(
            {
                "index": idx,
                "preview": content[:180],
                "content": content,
            }
        )

    return {
        "doc_id": doc_id,
        "filename": doc.filename,
        "total": total,
        "offset": safe_offset,
        "limit": safe_limit,
        "items": items,
    }


@router.post("/knowledge/{doc_id}/retry_parse")
def retry_parse_knowledge_doc(
    doc_id: int,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    doc = db.query(KnowledgeDoc).filter(KnowledgeDoc.id == doc_id).first()
    if not doc:
        raise HTTPException(status_code=404, detail="文档不存在")
    file_path = os.path.join(settings.BASE_DIR, "data", "uploads", str(doc.filename or ""))
    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="原始文件不存在，无法重试")
    ext = str(doc.filename or "").split(".")[-1].lower()

    doc.chunk_count = 0
    doc.parsed_content = []
    db.commit()
    KnowledgeParseService(db).mark_processing(
        doc_id=doc.id,
        parse_meta={"source_ext": ext, "pipeline": "modern_rag_parse", "retry": True},
    )

    rag_service = RAGService(db, current_user)
    background_tasks.add_task(rag_service.async_process_and_store, file_path, ext, doc.id)
    return {"status": "processing", "message": "已重新提交后台解析任务"}


@router.post("/knowledge/rebuild_bm25")
def rebuild_knowledge_bm25(
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    rag_service = RAGService(db, current_user)
    rag_service._init_bm25()
    total = len(getattr(rag_service, "bm25_corpus", []) or [])
    return {"status": "success", "message": f"BM25 索引已重建，当前切片数: {total}"}


# ==========================================
# 1. AI 模型管理增强
# ==========================================

class AIConfigCreate(BaseModel):
    config_type: str
    provider_name: str
    model_name: str
    base_url: str
    api_key: str


class NoticeReadAllRequest(BaseModel):
    notice_keys: list[str] = Field(default_factory=list)


class ModelRolloutUpdate(BaseModel):
    config_type: str
    enabled: bool = False
    baseline_config_id: int
    canary_config_id: int
    ratio_pct: int = 0


@router.post("/configs")
def add_ai_config(
    cfg: AIConfigCreate,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    """新增 AI 模型节点"""
    new_cfg = AIConfig(**cfg.dict(), is_active=False)  # 新增的默认不激活
    db.add(new_cfg)
    db.commit()
    db.refresh(new_cfg)
    _write_admin_audit(
        db=db,
        current_user=current_user,
        action="add_ai_config",
        target_type="ai_config",
        target_id=str(new_cfg.id),
        detail={
            "config_type": new_cfg.config_type,
            "model_name": new_cfg.model_name,
            "provider_name": new_cfg.provider_name,
            "base_url": new_cfg.base_url,
        },
        request=request,
    )
    return {"status": "success", "message": "新增节点成功"}


@router.post("/configs/{config_id}/ping")
async def ping_ai_config(config_id: int, db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """测试模型 API 的网络连通性与延迟"""
    config = db.query(AIConfig).filter(AIConfig.id == config_id).first()
    if not config:
        raise HTTPException(status_code=404, detail="配置不存在")

    start_time = time.time()
    try:
        # 构造一个极简的探测请求
        test_url = f"{config.base_url.rstrip('/')}/models"
        async with httpx.AsyncClient() as client:
            resp = await client.get(test_url, headers={"Authorization": f"Bearer {config.api_key}"}, timeout=5.0)

        delay = int((time.time() - start_time) * 1000)

        if resp.status_code in [200, 401, 403]:
            # 只要能连上（哪怕 key 是错的报 401），说明网络通了
            msg = "连接正常" if resp.status_code == 200 else "网络通畅，但 Key 无效"
            return {"status": "success", "delay": delay, "message": msg}
        else:
            raise Exception(f"HTTP {resp.status_code}")

    except Exception as e:
        raise HTTPException(status_code=500, detail=f"连接超时或失败: {str(e)}")


# ==========================================
# 2. 用户与权限管理增强
# ==========================================

@router.patch("/users/{user_id}/role")
def update_user_role(
    user_id: int,
    role: str,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    """修改用户角色 (admin / user)"""
    normalized_role = str(role or "").strip().lower()
    if normalized_role not in {"admin", "user"}:
        raise HTTPException(status_code=400, detail="角色仅支持 admin / user")
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能修改自己的权限")
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="用户不存在")

    old_role = str(user.role or "")
    user.role = normalized_role
    db.commit()
    _write_admin_audit(
        db=db,
        current_user=current_user,
        action="update_user_role",
        target_type="user",
        target_id=str(user.id),
        detail={
            "target_username": user.username,
            "old_role": old_role,
            "new_role": normalized_role,
        },
        request=request,
    )
    return {"status": "success", "message": f"角色已更新为 {normalized_role}"}


@router.post("/users/{user_id}/reset_password")
def reset_user_password(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    """强制重置密码为 123456"""
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    from app.core.security import get_password_hash
    user.hashed_password = get_password_hash("123456")
    db.commit()
    _write_admin_audit(
        db=db,
        current_user=current_user,
        action="reset_user_password",
        target_type="user",
        target_id=str(user.id),
        detail={"target_username": user.username},
        request=request,
    )
    return {"status": "success", "message": "密码已重置为 123456"}


@router.delete("/users/{user_id}")
def delete_user(
    user_id: int,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    """物理删除用户"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="超级管理员不能删除自己")
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(status_code=404, detail="用户不存在")
    target_username = str(user.username or "")
    db.delete(user)
    db.commit()
    _write_admin_audit(
        db=db,
        current_user=current_user,
        action="delete_user",
        target_type="user",
        target_id=str(user_id),
        detail={"target_username": target_username},
        request=request,
    )
    return {"status": "success", "message": "用户已彻底删除"}


@router.patch("/users/{user_id}/status")
def toggle_user_status(
    user_id: int,
    is_active: bool,
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(verify_admin),
):
    """封禁 / 解封用户"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能封禁自己")
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="用户不存在")

    old_status = bool(user.is_active)
    user.is_active = is_active
    db.commit()
    _write_admin_audit(
        db=db,
        current_user=current_user,
        action="toggle_user_status",
        target_type="user",
        target_id=str(user.id),
        detail={
            "target_username": user.username,
            "old_is_active": old_status,
            "new_is_active": bool(is_active),
        },
        request=request,
    )
    status_str = "解封" if is_active else "封禁"
    return {"status": "success", "message": f"用户已被{status_str}"}
