# app/api/endpoints/quiz.py

import logging
from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func
from app.db.session import SessionLocal
from app.api.endpoints.chat import get_current_user, get_rag_service
from app.services.quiz_service import QuizService
from app.models import User, Question, UserQuizRecord
from app.models.config import AIConfig
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# 提交答案的请求体
class AnswerRequest(BaseModel):
    question_id: int
    selected_option: str


# ==========================================
# ✨ 核心：后台异步出题任务 ✨
# ==========================================
async def run_quiz_generation(count: int = 10):
    """独立的后台任务：动态读取配置并调用 LLM 出题"""
    db = SessionLocal()
    try:
        # 1. 动态获取活跃的 LLM 配置
        llm_cfg = db.query(AIConfig).filter(AIConfig.config_type == "llm", AIConfig.is_active == True).first()
        if not llm_cfg:
            logger.error("后台任务无法获取 LLM 配置")
            return

        # 2. 初始化大模型
        from langchain_openai import ChatOpenAI
        llm = ChatOpenAI(
            model=llm_cfg.model_name,
            openai_api_key=llm_cfg.api_key,
            openai_api_base=llm_cfg.base_url,
            temperature=0
        )

        # 3. 调用出题服务
        quiz_service = QuizService(llm)
        await quiz_service.generate_daily_quiz(db, count=count)
        logger.info(f"🎉 后台成功生成了 {count} 道全新的交通法规题目！")

    except Exception as e:
        logger.error(f"后台出题任务异常: {e}")
    finally:
        db.close()


# ==========================================
# 接口：获取每日一练
# ==========================================
@router.get("/daily")
async def get_daily_quiz(
        background_tasks: BackgroundTasks,  # 引入后台任务
        db: Session = Depends(get_db),
        service=Depends(get_rag_service),
        current_user: User = Depends(get_current_user)
):
    """获取题目：优先给没做过的新题，不够则后台补题"""
    # 1. 获取已做题目 ID
    answered_records = db.query(UserQuizRecord.question_id).filter(
        UserQuizRecord.user_id == current_user.id
    ).all()
    answered_ids = [r[0] for r in answered_records]

    # 2. 查询没做过的新题
    query = db.query(Question)
    if answered_ids:
        query = query.filter(Question.id.not_in(answered_ids))
    unanswered_questions = query.order_by(func.random()).limit(10).all()

    # 3. ✨ 优雅降级：如果没做过的题目不足 5 道，后台自动补题，前端不阻塞！
    if len(unanswered_questions) < 10:
        logger.info(f"用户 {current_user.username} 的新题库不足，后台自动紧急补题...")
        # 抛给后台去出题，接口继续往下走
        background_tasks.add_task(run_quiz_generation, 10)

        # 为了不让前端报错，拿几道旧题凑齐 5 道当做“错题复习”
        shortage = 10 - len(unanswered_questions)
        if answered_ids:
            old_questions = db.query(Question).filter(Question.id.in_(answered_ids)).order_by(func.random()).limit(
                shortage).all()
            unanswered_questions.extend(old_questions)

    # 如果连旧题都没有（知识库刚建好的极端情况），就兜底随便取5道
    if not unanswered_questions:
        unanswered_questions = db.query(Question).order_by(func.random()).limit(10).all()

    return unanswered_questions


# ==========================================
# 接口：提交答案
# ==========================================
@router.post("/submit")
def submit_answer(
        req: AnswerRequest,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    q = db.query(Question).filter(Question.id == req.question_id).first()
    if not q: raise HTTPException(404, "题目不存在")

    is_correct = (req.selected_option.upper() == q.correct_answer.upper())

    record = UserQuizRecord(
        user_id=current_user.id,
        question_id=q.id,
        user_answer=req.selected_option,
        is_correct=is_correct
    )
    db.add(record)
    db.commit()

    return {
        "is_correct": is_correct,
        "correct_answer": q.correct_answer,
        "explanation": q.explanation
    }


# ==========================================
# 接口：获取用户做题统计
# ==========================================
@router.get("/my_stats")
def get_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = QuizService(None)
    return service.get_user_stats(db, user.id)


# ==========================================
# 接口：管理员强制触发生成新题目
# ==========================================
@router.post("/admin_generate")
async def admin_generate_quiz(
        background_tasks: BackgroundTasks,  # 引入后台任务
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    """管理员强制触发生成新题目（异步任务）"""
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    # 丢给后台执行，立即响应前端
    background_tasks.add_task(run_quiz_generation, 10)
    return {"status": "processing", "message": "题库生成任务已在后台启动，处理约需1-2分钟。"}