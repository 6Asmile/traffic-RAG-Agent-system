# app/api/endpoints/chat.py 完整替换

import json
import logging
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.db.session import SessionLocal
from app.models import ChatSession, ChatMessage, User
from app.models.knowledge import KnowledgeDoc
from app.services.analytics_service import AnalyticsService
from app.services.rag_service import RAGService
from app.core.security import SECRET_KEY, ALGORITHM
from pydantic import BaseModel

reusable_oauth2 = OAuth2PasswordBearer(tokenUrl="/api/v1/auth/login")
router = APIRouter()
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_current_user(db: Session = Depends(get_db), token: str = Depends(reusable_oauth2)):
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("sub")
    except:
        raise HTTPException(status_code=401, detail="凭证无效")
    user = db.query(User).filter(User.id == user_id).first()
    if not user: raise HTTPException(status_code=404, detail="用户不存在")
    return user


class ChatRequest(BaseModel):
    question: str
    session_id: Optional[str] = "default"


def get_rag_service(db: Session = Depends(get_db)):
    return RAGService(db)


def save_chat_to_db(session_id: str, content: str, sources: str):
    """异步后台保存聊天记录"""
    with SessionLocal() as db:
        try:
            db.add(ChatMessage(session_id=session_id, role="ai", content=content, sources=sources))
            sess = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if sess: sess.updated_at = datetime.now()
            db.commit()
        except Exception as e:
            logger.error(f"Save failed: {e}")


@router.post("/ask_stream")
async def ask_question_stream(
        request: ChatRequest,
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        service: RAGService = Depends(get_rag_service),
        current_user: User = Depends(get_current_user)
):
    # 1. 会话持久化
    session = db.query(ChatSession).filter(ChatSession.id == request.session_id).first()
    if not session:
        session = ChatSession(id=request.session_id, title=request.question[:15], user_id=current_user.id)
        db.add(session)
    db.add(ChatMessage(session_id=request.session_id, role="user", content=request.question))
    db.commit()

    async def event_generator():
        full_content = ""
        sources_json = "[]"

        # 2. 调用 RAG 生成器
        async for line in service.chat_stream(request.question, request.session_id):
            if not line: continue

            # 解析内容用于保存
            try:
                data = json.loads(line)
                if data["type"] == "content":
                    full_content += data["data"]
                elif data["type"] == "sources":
                    sources_json = json.dumps(data["data"])
                elif data["type"] == "done":
                    # 流结束，提交保存任务
                    background_tasks.add_task(save_chat_to_db, request.session_id, full_content, sources_json)
            except:
                pass

            # 【核心修复】严格遵守 SSE 协议格式：data: <JSON>\n\n
            # 最后的两个换行符是前端 split("\n\n") 的关键
            yield f"data: {line.strip()}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # 彻底禁用代理缓存
            "Connection": "keep-alive"
        }
    )


# --- 其余接口（me, sessions, history, upload...）保持原样 ---
@router.get("/me")
def get_me(current_user: User = Depends(get_current_user)):
    return {"username": current_user.username, "avatar": current_user.avatar, "role": current_user.role,
            "created_at": current_user.created_at.strftime("%Y-%m-%d") if current_user.created_at else "2025-01-01"}


@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(ChatSession).filter(ChatSession.user_id == current_user.id).order_by(
        ChatSession.updated_at.desc()).all()


@router.get("/history/{session_id}")
def get_history(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(
        ChatMessage.created_at.asc()).all()


@router.post("/upload")
async def upload_knowledge_file(file: UploadFile = File(...), db: Session = Depends(get_db),
                                service: RAGService = Depends(get_rag_service),
                                current_user: User = Depends(get_current_user)):
    count = service.ingest_pdf(file, file.filename)
    db.add(KnowledgeDoc(filename=file.filename, chunk_count=count, upload_time=datetime.now()))
    db.commit()
    return {"status": "success"}


@router.get("/knowledge_list")
def list_knowledge(db: Session = Depends(get_db)):
    return db.query(KnowledgeDoc).all()


@router.delete("/knowledge/{doc_id}")
def delete_knowledge(doc_id: int, db: Session = Depends(get_db)):
    doc = db.query(KnowledgeDoc).filter(KnowledgeDoc.id == doc_id).first()
    if doc:
        db.delete(doc)
        db.commit()
    return {"status": "success"}


@router.delete("/session/{session_id}")
def delete_session(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
    db.query(ChatSession).filter(ChatSession.id == session_id, ChatSession.user_id == current_user.id).delete()
    db.commit()
    return {"status": "success"}


@router.get("/analytics")
def get_analytics(
        db: Session = Depends(get_db),
        service: RAGService = Depends(get_rag_service),
        current_user: User = Depends(get_current_user)
):
    """管理员获取数据分析结果"""
    if current_user.role != "admin":
        raise HTTPException(403, "无权访问分析数据")

    # 利用 RAGService 里的 embedding 对象进行分析
    analyzer = AnalyticsService(service.custom_embeddings)
    data = analyzer.analyze_hot_topics(db)
    return data