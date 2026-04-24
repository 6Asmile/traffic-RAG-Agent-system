# app/api/endpoints/agentic.py

import json
from fastapi import APIRouter, Depends, HTTPException
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session
import logging

from app.db.session import SessionLocal
from app.models import User, ChatSession, ChatMessage
from app.api.endpoints.chat import get_current_user, ChatRequest, save_chat_to_db
from app.services.agentic.agent_service import AgenticRAGService

router = APIRouter()
logger = logging.getLogger(__name__)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_agentic_service(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return AgenticRAGService(db, current_user)


@router.post("/expert_stream")
async def expert_chat_stream(
        request: ChatRequest,
        service: AgenticRAGService = Depends(get_agentic_service),
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user),
):
    """
    Agentic RAG 专家模式流式入口
    """
    session_id = request.session_id or "default"
    session = db.query(ChatSession).filter(ChatSession.id == session_id).first()
    if not session:
        session = ChatSession(id=session_id, title=request.question[:15], user_id=current_user.id)
        db.add(session)
    elif session.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="无权访问该会话")

    db.add(ChatMessage(session_id=session_id, role="user", content=request.question))
    db.commit()

    async def event_generator():
        full_content = ""
        sources_json = "[]"

        try:
            async for line in service.agentic_chat_stream(request.question, session_id):
                if not line: continue

                try:
                    data = json.loads(line)
                    if data["type"] == "content":
                        full_content += data.get("data", "")
                    elif data["type"] == "sources":
                        sources_json = json.dumps(data.get("data", []), ensure_ascii=False)
                    elif data["type"] == "done":
                        full_content = data.get("full_answer", full_content)
                        # 持久化到 MySQL
                        ai_msg_id = save_chat_to_db(session_id, full_content, sources_json)
                        data["message_id"] = ai_msg_id
                        line = json.dumps(data)
                except Exception as e:
                    logger.error(f"Event parser error: {e}")

                yield f"data: {line.strip()}\n\n"

        except Exception as e:
            logger.error(f"Agentic stream error: {e}")
            # 异常时确保前端收到 done 事件，解除 Loading 状态
            yield f"data: {json.dumps({'type': 'done', 'full_answer': full_content}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive"
        }
    )
