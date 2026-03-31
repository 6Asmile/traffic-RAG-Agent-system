# app/api/endpoints/chat.py 完整替换
import hashlib
import json
import logging
import os
import random
import shutil
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, UploadFile, File, Depends, HTTPException, BackgroundTasks
from fastapi.security import OAuth2PasswordBearer
from jose import jwt
from sqlalchemy.orm import Session
from starlette.responses import StreamingResponse

from app.core.config import settings
from app.core.constants import GraphConstants
from app.db.session import SessionLocal
from app.models import ChatSession, ChatMessage, User, HotTopic, AIConfig
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


def get_rag_service(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return RAGService(db, current_user) # 注意 RAGService 接收了 user



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


@router.post("/upload_avatar")
async def upload_avatar(
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """处理用户头像上传"""
    allowed_exts = ['jpg', 'jpeg', 'png', 'gif', 'webp']
    ext = file.filename.split('.')[-1].lower()

    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"仅支持 {allowed_exts} 图片格式")

    # 保存目录: data/uploads/avatars
    avatar_dir = os.path.join(settings.BASE_DIR, "data", "uploads", "avatars")
    os.makedirs(avatar_dir, exist_ok=True)

    # 命名文件 (用 user_id 防止重名覆盖)
    new_filename = f"user_{current_user.id}.{ext}"
    file_save_path = os.path.join(avatar_dir, new_filename)

    try:
        # 保存物理文件
        with open(file_save_path, "wb") as buffer:
            shutil.copyfileobj(file.file, buffer)

        # 更新数据库中的头像路径
        # 注意：因为 main.py 中静态目录挂载在 /static 下
        # app.mount("/static", StaticFiles(directory="data/uploads"))
        # 所以前端访问路径应该是 /static/avatars/xxx.jpg
        db_avatar_path = f"/static/avatars/{new_filename}"
        current_user.avatar = db_avatar_path
        db.commit()

        return {"status": "success", "avatar_url": db_avatar_path}

    except Exception as e:
        logger.error(f"头像上传失败: {e}")
        raise HTTPException(status_code=500, detail="头像保存失败")

@router.get("/sessions")
def list_sessions(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(ChatSession).filter(ChatSession.user_id == current_user.id).order_by(
        ChatSession.updated_at.desc()).all()


@router.get("/history/{session_id}")
def get_history(session_id: str, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return db.query(ChatMessage).filter(ChatMessage.session_id == session_id).order_by(
        ChatMessage.created_at.asc()).all()


@router.post("/upload")
async def upload_knowledge_file(
        background_tasks: BackgroundTasks,  # 引入后台任务队列
        file: UploadFile = File(...),
        db: Session = Depends(get_db),
        service: RAGService = Depends(get_rag_service),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(status_code=403, detail="仅管理员可上传知识库")
        # === 文件 MD5 防重复校验 ===
    file_content = await file.read()
    file_hash = hashlib.md5(file_content).hexdigest()

    # 检查数据库是否已有同名或同 Hash 文件
    exist_doc = db.query(KnowledgeDoc).filter(
        (KnowledgeDoc.filename == file.filename)  # 这里如果你数据库里加了 file_hash 字段更好，没有的话先判断文件名
    ).first()
    if exist_doc:
        raise HTTPException(status_code=400, detail="该文件已存在于知识库中，请勿重复上传")

    await file.seek(0)  # 读完哈希后，要把指针拨回开头，否则后面存文件是空的！

    allowed_exts = ['pdf', 'txt', 'docx', 'md']
    ext = file.filename.split('.')[-1].lower()
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"仅支持 {allowed_exts} 格式")

    try:
        # 1. 快速保存物理文件到本地
        upload_path = os.path.join(settings.BASE_DIR, "data", "uploads")
        os.makedirs(upload_path, exist_ok=True)
        file_save_path = os.path.join(upload_path, file.filename)

        with open(file_save_path, "wb") as buffer:
            import shutil
            shutil.copyfileobj(file.file, buffer)

        # 2. 先在数据库里创建一个“处理中”的记录 (chunk_count = 0)
        new_doc = KnowledgeDoc(
            filename=file.filename,
            chunk_count=0,  # 0 表示正在后台解析中
            upload_time=datetime.now()
        )
        db.add(new_doc)
        db.commit()
        db.refresh(new_doc)

        # 3. 将解析任务推入后台队列！
        background_tasks.add_task(service.async_process_and_store, file_save_path, ext, new_doc.id)

        # 4. 瞬间响应前端
        return {
            "status": "processing",
            "message": f"文件 [{file.filename}] 已提交后台排队解析！此过程可能需要几分钟，请稍后刷新列表查看进度。"
        }

    except Exception as e:
        logger.error(f"上传分发失败: {e}")
        raise HTTPException(status_code=500, detail=str(e))



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

# 在 build_graph 内部定义一个后台任务处理函数
async def run_graph_build(texts: list):
    db = SessionLocal()  # 在后台任务内重新创建 Session
    try:
        # 1. 从数据库读取活跃配置
        llm_cfg = db.query(AIConfig).filter(AIConfig.config_type == "llm", AIConfig.is_active == True).first()
        if not llm_cfg:
            logger.error("后台任务无法获取 LLM 配置")
            return

        # 2. 初始化 LLM
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=llm_cfg.model_name,
            openai_api_key=llm_cfg.api_key,
            openai_api_base=llm_cfg.base_url,
            temperature=0
        )

        # 3. 执行图谱构建
        from app.services.graph_service import GraphService
        gs = GraphService(llm)
        await gs.build_from_texts(db, texts)
        logger.info("后台图谱构建任务完成")
    except Exception as e:
        logger.error(f"后台图谱任务异常: {e}")
    finally:
        db.close()


@router.post("/build_graph")
async def build_graph(
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    # ============================================================
    # 🌟 核心重构：从知识库 (KnowledgeDoc) 的解析缓存中提取样本
    # ============================================================
    docs = db.query(KnowledgeDoc).all()
    all_chunks = []

    # 收集所有的知识片段
    for doc in docs:
        if doc.parsed_content and isinstance(doc.parsed_content, list):
            all_chunks.extend(doc.parsed_content)

    if not all_chunks:
        return {"status": "error", "message": "知识库为空，暂无可用数据构建图谱"}

    # 为防止大模型 API 耗时过长或 Token 爆炸，采用增量抽取策略
    # 使用常量限制每次抽取的样本数 (例如 15 个切片)
    sample_size = min(len(all_chunks), GraphConstants.EXTRACT_CHUNK_LIMIT)
    texts = random.sample(all_chunks, sample_size)

    # 丢给后台任务慢慢处理
    background_tasks.add_task(run_graph_build, texts)

    return {
        "status": "processing",
        "message": f"图谱更新任务已在后台启动，本次随机抽取了 {sample_size} 个知识切片进行深度分析"
    }


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

# 增加获取和保存个人 AI 设置的接口
@router.get("/ai_settings")
def get_ai_settings(current_user: User = Depends(get_current_user)):
    """获取用户的 AI 偏好设置"""
    # 默认配置结构
    default_prefs = {
        "llm_model": "deepseek-chat",
        "llm_key": "",
        "embed_model": "text-embedding-v4",
        "embed_key": "",
        "vision_model": "qwen-vl-max",
        "vision_key": ""
    }
    user_prefs = current_user.ai_preferences or {}
    # 合并默认值和用户值
    return {**default_prefs, **user_prefs}

@router.post("/ai_settings")
def update_ai_settings(
    prefs: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user)
):
    """保存用户的 AI 偏好设置"""
    current_user.ai_preferences = prefs
    db.commit()
    return {"status": "success", "message": "AI 设置已保存生效"}


@router.post("/warmup_cache")
async def warmup_cache(
    db: Session = Depends(get_db),
    service: RAGService = Depends(get_rag_service)
):
    """
    预热：分析热门话题并把答案存入缓存
    """
    topics = db.query(HotTopic).order_by(HotTopic.hit_count.desc()).limit(5).all()
    count = 0
    for t in topics:
        for q in t.representative_queries:
            # 执行一次检索并生成过程，触发缓存机制
            # 这里调用 service.chat_stream 或者手动触发一次流程
            await service.chat_stream(q)
            count += 1
    return {"status": "success", "warmed_up": count}