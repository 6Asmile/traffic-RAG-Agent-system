# backend/app/api/endpoints/admin.py
import time
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.api.endpoints.chat import get_current_user
from app.core.config import settings
from app.db.session import SessionLocal
from app.models import User, ChatSession, ChatMessage
from app.models.config import AIConfig
from app.models.knowledge import KnowledgeDoc

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


@router.get("/dashboard/stats")
def get_dashboard_stats(db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """获取完全真实的 Dashboard 数据"""

    # 1. 真实用户与切片总数
    total_users = db.query(User).count()
    chunk_result = db.query(func.sum(KnowledgeDoc.chunk_count)).scalar()
    total_chunks = int(chunk_result) if chunk_result else 0

    # 2. 真实 Redis 缓存拦截率
    cache_hit_rate = 0.0
    try:
        import redis
        r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True)
        hits = int(r.get("metrics:cache_hits") or 0)
        queries = int(r.get("metrics:total_queries") or 0)
        if queries > 0:
            cache_hit_rate = round((hits / queries) * 100, 1)
    except:
        pass

    # 3. 真实 7 天 Token 消耗 (通过统计聊天表最近 7 天的字数估算，1汉字约等于1.5 Token)
    today = datetime.now().date()
    days = [(today - timedelta(days=i)).strftime("%m-%d") for i in range(6, -1, -1)]
    input_tokens = [0] * 7
    output_tokens = [0] * 7

    seven_days_ago = datetime.now() - timedelta(days=7)
    # 取出最近7天的所有消息
    recent_msgs = db.query(ChatMessage.created_at, ChatMessage.content, ChatMessage.role).filter(
        ChatMessage.created_at >= seven_days_ago
    ).all()

    for msg in recent_msgs:
        day_str = msg.created_at.strftime("%m-%d")
        if day_str in days:
            idx = days.index(day_str)
            # 粗略估算 Token 数量：字数 * 1.5
            token_count = int(len(msg.content or "") * 1.5)
            if msg.role == 'user':
                input_tokens[idx] += token_count
            elif msg.role == 'ai':
                output_tokens[idx] += token_count

    # 按照市面常规模型价格估算账单 ($0.002 / 1K tokens)
    total_tokens = sum(input_tokens) + sum(output_tokens)
    estimated_cost = round((total_tokens / 1000) * 0.002, 4)

    # 4. 真实高频用户 Top 5
    top_users = db.query(
        User.username, func.count(ChatMessage.id).label("msg_count")
    ).join(ChatSession, User.id == ChatSession.user_id) \
        .join(ChatMessage, ChatSession.id == ChatMessage.session_id) \
        .filter(ChatMessage.role == 'user') \
        .group_by(User.username).order_by(func.count(ChatMessage.id).desc()).limit(5).all()

    return {
        "metrics": {
            "total_users": total_users,
            "total_chunks": total_chunks,
            "cache_hit_rate": f"{cache_hit_rate}%",
            "estimated_cost": f"${estimated_cost}"
        },
        "chart": {
            "days": days,
            "input_tokens": input_tokens,
            "output_tokens": output_tokens
        },
        "top_users": [{"username": u[0], "count": u[1]} for u in top_users]
    }


@router.get("/configs")
def get_ai_configs(db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """获取所有 AI 引擎配置"""
    configs = db.query(AIConfig).all()
    return configs


@router.patch("/configs/{config_id}/activate")
def activate_ai_config(config_id: int, db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """热切换 AI 模型"""
    target_config = db.query(AIConfig).filter(AIConfig.id == config_id).first()
    if not target_config:
        raise HTTPException(status_code=404, detail="配置不存在")

    # 将同类型的其他配置设为不活跃
    db.query(AIConfig).filter(AIConfig.config_type == target_config.config_type).update({"is_active": False})
    # 激活目标配置
    target_config.is_active = True
    db.commit()
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
    """系统链路探针"""
    status = {"mysql": "error", "redis": "error"}
    # 测 MySQL
    try:
        db.execute("SELECT 1")
        status["mysql"] = "ok"
    except:
        pass
    # 测 Redis
    try:
        import redis
        r = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, socket_timeout=1)
        if r.ping(): status["redis"] = "ok"
    except:
        pass
    return status


# ==========================================
# 1. AI 模型管理增强
# ==========================================

class AIConfigCreate(BaseModel):
    config_type: str
    provider_name: str
    model_name: str
    base_url: str
    api_key: str


@router.post("/configs")
def add_ai_config(cfg: AIConfigCreate, db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """新增 AI 模型节点"""
    new_cfg = AIConfig(**cfg.dict(), is_active=False)  # 新增的默认不激活
    db.add(new_cfg)
    db.commit()
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
def update_user_role(user_id: int, role: str, db: Session = Depends(get_db),
                     current_user: User = Depends(verify_admin)):
    """修改用户角色 (admin / user)"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能修改自己的权限")
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="用户不存在")

    user.role = role
    db.commit()
    return {"status": "success", "message": f"角色已更新为 {role}"}


@router.post("/users/{user_id}/reset_password")
def reset_user_password(user_id: int, db: Session = Depends(get_db), _: User = Depends(verify_admin)):
    """强制重置密码为 123456"""
    user = db.query(User).filter(User.id == user_id).first()
    from app.core.security import get_password_hash
    user.hashed_password = get_password_hash("123456")
    db.commit()
    return {"status": "success", "message": "密码已重置为 123456"}


@router.delete("/users/{user_id}")
def delete_user(user_id: int, db: Session = Depends(get_db), current_user: User = Depends(verify_admin)):
    """物理删除用户"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="超级管理员不能删除自己")
    user = db.query(User).filter(User.id == user_id).first()
    db.delete(user)
    db.commit()
    return {"status": "success", "message": "用户已彻底删除"}


@router.patch("/users/{user_id}/status")
def toggle_user_status(user_id: int, is_active: bool, db: Session = Depends(get_db),
                       current_user: User = Depends(verify_admin)):
    """封禁 / 解封用户"""
    if user_id == current_user.id:
        raise HTTPException(status_code=400, detail="不能封禁自己")
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="用户不存在")

    user.is_active = is_active
    db.commit()
    status_str = "解封" if is_active else "封禁"
    return {"status": "success", "message": f"用户已被{status_str}"}