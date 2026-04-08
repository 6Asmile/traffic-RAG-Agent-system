# app/api/endpoints/agentic.py

from fastapi import APIRouter, Depends
from starlette.responses import StreamingResponse
from sqlalchemy.orm import Session
import logging

from app.db.session import SessionLocal
from app.models import User
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
        service: AgenticRAGService = Depends(get_agentic_service)
):
    """
    Agentic RAG 专家模式流式入口
    """

    async def event_generator():
        full_content = ""
        # 注意：Agentic 模式由于是多步动态检索，暂不固定 sources 格式
        # 可视化依据会直接由模型输出在文中，这里设为空
        sources_json = "[]"

        async for line in service.agentic_chat_stream(request.question, request.session_id):
            if not line: continue

            import json
            try:
                data = json.loads(line)
                if data["type"] == "content":
                    full_content += data.get("data", "")
                elif data["type"] == "done":
                    # 持久化到 MySQL
                    ai_msg_id = save_chat_to_db(request.session_id, full_content, sources_json)
                    data["message_id"] = ai_msg_id
                    line = json.dumps(data)
            except Exception as e:
                logger.error(f"Event parser error: {e}")

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