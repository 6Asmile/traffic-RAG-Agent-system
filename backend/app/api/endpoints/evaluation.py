import logging
import uuid

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from sqlalchemy.orm import Session
from typing import Optional, List

from app.db.session import SessionLocal
from app.api.endpoints.chat import get_current_user
from app.models import User
from app.models.evaluation import EvalDataset, EvalResult, EvalRun
from app.services.evaluation_service import EvaluationService

logger = logging.getLogger(__name__)
router = APIRouter()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class EvalDatasetItem(BaseModel):
    question: str
    reference_answer: str
    category: Optional[str] = None


class EvalDatasetBatch(BaseModel):
    items: List[EvalDatasetItem]


def _run_eval_background(run_id: str):
    db = SessionLocal()
    try:
        EvaluationService.run_evaluation(db, run_id)
    except Exception as e:
        logger.error(f"后台评估任务异常: {e}")
    finally:
        db.close()


@router.post("/run")
async def run_evaluation(
        background_tasks: BackgroundTasks,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    dataset_count = db.query(EvalDataset).count()
    if dataset_count == 0:
        raise HTTPException(400, "评估数据集为空，请先添加评估数据")

    run_id = uuid.uuid4().hex[:12]
    background_tasks.add_task(_run_eval_background, run_id)

    return {
        "status": "processing",
        "run_id": run_id,
        "message": f"评估任务已启动 (批次: {run_id})，共 {dataset_count} 个问题，预计需要几分钟。"
    }


@router.get("/results")
def get_results(
        run_id: Optional[str] = None,
        limit: int = 50,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    query = db.query(EvalResult)
    if run_id:
        query = query.filter(EvalResult.run_id == run_id)
    results = query.order_by(EvalResult.created_at.desc()).limit(limit).all()

    return [
        {
            "id": r.id,
            "run_id": r.run_id,
            "dataset_id": r.dataset_id,
            "question": r.question,
            "generated_answer": r.generated_answer,
            "retrieved_contexts": r.retrieved_contexts,
            "reference_answer": r.reference_answer,
            "faithfulness": r.faithfulness,
            "answer_accuracy": r.answer_accuracy,
            "answer_relevancy": r.answer_relevancy,
            "context_recall": r.context_recall,
            "avg_score": r.avg_score,
            "status": r.status,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""
        }
        for r in results
    ]


@router.get("/latest")
def get_latest_scores(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    return EvaluationService.get_latest_scores(db)


@router.get("/history")
def get_evaluation_history(
        limit: int = 10,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    return EvaluationService.get_evaluation_history(db, limit)


@router.get("/datasets")
def list_datasets(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    datasets = db.query(EvalDataset).order_by(EvalDataset.created_at.desc()).all()
    return [
        {
            "id": ds.id,
            "question": ds.question,
            "reference_answer": ds.reference_answer,
            "category": ds.category,
            "created_at": ds.created_at.strftime("%Y-%m-%d %H:%M:%S") if ds.created_at else ""
        }
        for ds in datasets
    ]


@router.post("/datasets")
def add_dataset_item(
        item: EvalDatasetItem,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    ds = EvalDataset(
        question=item.question,
        reference_answer=item.reference_answer,
        category=item.category
    )
    db.add(ds)
    db.commit()
    db.refresh(ds)
    return {"status": "success", "id": ds.id}


@router.post("/datasets/batch")
def add_dataset_batch(
        batch: EvalDatasetBatch,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    count = 0
    for item in batch.items:
        ds = EvalDataset(
            question=item.question,
            reference_answer=item.reference_answer,
            category=item.category
        )
        db.add(ds)
        count += 1
    db.commit()
    return {"status": "success", "count": count}


@router.post("/datasets/init_default")
def init_default_dataset(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    from app.services.eval_dataset_default import DEFAULT_EVAL_DATASET

    db.query(EvalResult).delete()
    db.query(EvalRun).delete()
    db.query(EvalDataset).delete()
    db.commit()

    count = 0
    for item in DEFAULT_EVAL_DATASET:
        ds = EvalDataset(
            question=item["question"],
            reference_answer=item["reference_answer"],
            category=item.get("category")
        )
        db.add(ds)
        count += 1
    db.commit()
    return {"status": "success", "count": count, "message": f"已导入 {count} 条默认评估数据"}


@router.delete("/datasets/{dataset_id}")
def delete_dataset_item(
        dataset_id: int,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    ds = db.query(EvalDataset).filter(EvalDataset.id == dataset_id).first()
    if not ds:
        raise HTTPException(404, "数据集条目不存在")
    db.delete(ds)
    db.commit()
    return {"status": "success"}


@router.get("/status/{run_id}")
def get_run_status(
        run_id: str,
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    return EvaluationService.get_run_status(db, run_id)


@router.get("/active_run")
def get_active_run(
        db: Session = Depends(get_db),
        current_user: User = Depends(get_current_user)
):
    if current_user.role != "admin":
        raise HTTPException(403, "权限不足")

    active = db.query(EvalRun).filter(EvalRun.status == "running").first()
    if not active:
        return {"status": "idle", "message": "当前无正在运行的评估任务"}
    return {
        "status": "running",
        "run_id": active.run_id,
        "total": active.total,
        "completed_count": active.completed_count,
        "failed_count": active.failed_count,
        "progress": round(active.completed_count / active.total * 100, 1) if active.total > 0 else 0
    }
