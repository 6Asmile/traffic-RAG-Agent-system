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
from app.models import ChatSession, ChatMessage, User, HotTopic
from app.models.knowledge import KnowledgeDoc
from app.services.analytics_service import AnalyticsService
from app.services.graph_service import GraphService
from app.services.rag_service import RAGService
from app.core.security import SECRET_KEY, ALGORITHM, verify_password, get_password_hash
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


def save_chat_to_db(session_id: str, content: str, sources: str) -> int:
    """同步保存聊天记录并返回消息ID"""
    with SessionLocal() as db:
        try:
            # 1. 创建消息对象
            msg = ChatMessage(
                session_id=session_id,
                role="ai",
                content=content,
                sources=sources
            )
            db.add(msg)

            # 2. 更新会话时间
            sess = db.query(ChatSession).filter(ChatSession.id == session_id).first()
            if sess:
                sess.updated_at = datetime.now()

            # 3. 提交并刷新以获取 ID
            db.commit()
            db.refresh(msg)  # 关键：刷新后 msg.id 才有值
            return msg.id
        except Exception as e:
            logger.error(f"Save failed: {e}")
            return 0


@router.post("/ask_stream")
async def ask_question_stream(
        request: ChatRequest,
        # background_tasks: BackgroundTasks, # 这里不再需要 BackgroundTasks 来存 AI 消息了
        db: Session = Depends(get_db),
        service: RAGService = Depends(get_rag_service),
        current_user: User = Depends(get_current_user)
):
    # 1. 会话持久化 (保持不变)
    session = db.query(ChatSession).filter(ChatSession.id == request.session_id).first()
    if not session:
        session = ChatSession(id=request.session_id, title=request.question[:15], user_id=current_user.id)
        db.add(session)

    # 用户提问依然可以先存，或者你也想拿到用户消息ID的话，也可以改成同步
    db.add(ChatMessage(session_id=request.session_id, role="user", content=request.question))
    db.commit()

    async def event_generator():
        full_content = ""
        sources_json = "[]"

        # 2. 调用 RAG 生成器
        async for line in service.chat_stream(request.question, request.session_id):
            if not line: continue

            try:
                data = json.loads(line)

                # 收集内容
                if data["type"] == "content":
                    full_content += data.get("data", "")

                # 收集来源
                elif data["type"] == "sources":
                    sources_json = json.dumps(data.get("data", []))

                # --- 核心修改：处理完成信号 ---
                elif data["type"] == "done":
                    # 1. 同步保存到数据库，获取 ID
                    ai_msg_id = save_chat_to_db(request.session_id, full_content, sources_json)

                    # 2. 将 ID 注入到返回数据中
                    data["message_id"] = ai_msg_id

                    # 3. 重新序列化为字符串
                    line = json.dumps(data)

            except Exception as e:
                logger.error(f"Stream processing error: {e}")

            # 发送数据 (符合 SSE 格式)
            yield f"data: {line.strip()}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )


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
    """获取已存在的分析结果"""
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    # 直接从数据库查询分析好的结果
    return db.query(HotTopic).order_by(HotTopic.hit_count.desc()).all()


@router.post("/perform_analysis")
async def perform_analysis(
        db: Session = Depends(get_db),
        service: RAGService = Depends(get_rag_service),
        current_user: User = Depends(get_current_user)
):
    """手动触发 AI 聚类分析引擎"""
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    try:
        # --- 关键修复：传入 service.custom_embeddings 和 service.llm ---
        analyzer = AnalyticsService(service.custom_embeddings, service.llm)

        # 执行深度分析
        result_msg = await analyzer.perform_deep_analysis(db)

        return {"status": "success", "message": result_msg}
    except Exception as e:
        logger.error(f"分析引擎运行失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/knowledge_graph")
def get_graph(db: Session = Depends(get_db)):
    """获取图谱数据"""
    gs = GraphService(None) # 仅查询不需要LLM
    return gs.get_full_graph(db)


@router.post("/build_graph")
async def build_graph(db: Session = Depends(get_db), service: RAGService = Depends(get_rag_service)):
    """从数据库已有的 AI 回答中提取知识图谱"""
    # 提取最近的 10 条 AI 生成的专业回答作为图谱来源
    msgs = db.query(ChatMessage).filter(ChatMessage.role == "ai").order_by(ChatMessage.created_at.desc()).limit(
        10).all()

    if not msgs:
        # 如果没聊天记录，就给一段测试文本，确保图谱不是空的
        texts = ["饮酒后驾驶机动车的，处暂扣六个月机动车驾驶证，并处一千元以上二千元以下罚款。"]
    else:
        texts = [m.content for m in msgs]

    gs = GraphService(service.llm)
    await gs.build_from_texts(db, texts)
    return {"status": "success", "message": f"已从 {len(texts)} 条文本中提取并更新图谱"}


@router.get("/stats")
def get_user_stats(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """获取用户真实统计数据"""
    # 1. 计算加入天数
    delta = datetime.utcnow() - current_user.created_at
    join_days = max(delta.days, 1)  # 至少显示1天

    # 2. 计算咨询次数 (该用户所有会话中的用户提问总数)
    # 先找到该用户的所有 session_id
    user_sessions = db.query(ChatSession.id).filter(ChatSession.user_id == current_user.id).all()
    session_ids = [s[0] for s in user_sessions]

    query_count = db.query(ChatMessage).filter(
        ChatMessage.session_id.in_(session_ids),
        ChatMessage.role == "user"
    ).count()

    return {"join_days": join_days, "query_count": query_count}


@router.put("/update_me")
def update_user_info(
        new_name: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """修改用户名"""
    # 检查重名
    existing = db.query(User).filter(User.username == new_name).first()
    if existing and existing.id != current_user.id:
        raise HTTPException(400, "用户名已存在")

    current_user.username = new_name
    db.commit()
    return {"status": "success", "new_name": new_name}


@router.post("/change_password")
def change_password(
        old_pwd: str,
        new_pwd: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """修改密码"""
    if not verify_password(old_pwd, current_user.hashed_password):
        raise HTTPException(400, "旧密码错误")

    current_user.hashed_password = get_password_hash(new_pwd)
    db.commit()
    return {"status": "success"}


class FeedbackRequest(BaseModel):
    message_id: int
    is_helpful: bool
    note: Optional[str] = None


@router.post("/feedback")
def give_feedback(
        request: FeedbackRequest,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """用户对回答质量进行评价"""
    # 查找该条消息
    msg = db.query(ChatMessage).filter(ChatMessage.id == request.message_id).first()

    if not msg:
        raise HTTPException(status_code=404, detail="消息不存在")

    # 校验权限：该消息必须属于当前用户的某个会话
    session = db.query(ChatSession).filter(
        ChatSession.id == msg.session_id,
        ChatSession.user_id == current_user.id
    ).first()

    if not session:
        raise HTTPException(status_code=403, detail="无权操作此消息")

    # 更新反馈信息
    msg.is_helpful = request.is_helpful
    msg.feedback_note = request.note
    db.commit()

    return {"status": "success", "message": "感谢您的反馈！"}