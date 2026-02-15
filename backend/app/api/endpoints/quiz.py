from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from app.db.session import SessionLocal
from app.api.endpoints.chat import get_current_user, get_rag_service  # 复用依赖
from app.services.quiz_service import QuizService
from app.models import User, Question, UserQuizRecord
from pydantic import BaseModel

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


@router.get("/daily")
async def get_daily_quiz(
        db: Session = Depends(get_db),
        service=Depends(get_rag_service),  # 获取 RAGService 以拿到 LLM
        current_user: User = Depends(get_current_user)
):
    """获取题目（如果题库不够，现场生成）"""
    # 1. 检查题库数量
    count = db.query(Question).count()
    quiz_service = QuizService(service.llm)

    if count < 5:
        # 题库没题，现场生成
        await quiz_service.generate_daily_quiz(db)

    # 2. 随机返回 5 道题给用户
    # 进阶：排除用户做过的题
    questions = db.query(Question).limit(5).all()
    return questions


@router.post("/submit")
def submit_answer(
        req: AnswerRequest,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    q = db.query(Question).filter(Question.id == req.question_id).first()
    if not q: raise HTTPException(404, "题目不存在")

    is_correct = (req.selected_option.upper() == q.correct_answer.upper())

    # 记录
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


@router.get("/my_stats")
def get_stats(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    service = QuizService(None)
    return service.get_user_stats(db, user.id)