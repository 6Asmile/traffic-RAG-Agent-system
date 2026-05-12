import logging
import math
from datetime import datetime

from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session

from app.models.evaluation import EvalDataset, EvalResult, EvalRun
from app.services.config_service import ConfigService

logger = logging.getLogger("EvaluationService")


def _safe_float(val):
    if val is None:
        return None
    try:
        f = float(val)
        if math.isnan(f) or math.isinf(f):
            return None
        return f
    except (TypeError, ValueError):
        return None


class EvaluationService:

    @staticmethod
    def _build_evaluator_llm(db: Session):
        llm_cfg = ConfigService.get_active_config(db, "llm")
        if not llm_cfg:
            raise Exception("LLM 配置缺失，无法执行评估")

        from ragas.llms import LangchainLLMWrapper

        chat_llm = ChatOpenAI(
            model=llm_cfg.model_name,
            openai_api_key=llm_cfg.api_key,
            openai_api_base=llm_cfg.base_url,
            temperature=0,
            request_timeout=300
        )
        return LangchainLLMWrapper(chat_llm)

    @staticmethod
    def _build_evaluator_embeddings(db: Session):
        emb_cfg = ConfigService.get_active_config(db, "embedding")
        if not emb_cfg:
            raise Exception("Embedding 配置缺失，无法执行评估")

        from ragas.embeddings import LangchainEmbeddingsWrapper
        from app.services.rag_service import AliyunEmbeddingWrapper

        emb = AliyunEmbeddingWrapper(
            model=emb_cfg.model_name,
            api_key=emb_cfg.api_key,
            base_url=emb_cfg.base_url
        )
        return LangchainEmbeddingsWrapper(emb)

    @staticmethod
    def _build_ragas_metrics(evaluator_llm, evaluator_embeddings):
        from ragas.metrics import LLMContextRecall, Faithfulness, FactualCorrectness, ResponseRelevancy

        return [
            Faithfulness(llm=evaluator_llm),
            LLMContextRecall(llm=evaluator_llm),
            FactualCorrectness(llm=evaluator_llm,mode="recall"),
            ResponseRelevancy(llm=evaluator_llm, embeddings=evaluator_embeddings),
        ]

    @staticmethod
    def _collect_rag_outputs(datasets, db: Session):
        from app.services.rag_service import RAGService
        from app.db.session import SessionLocal

        rag_db = SessionLocal()
        try:
            logger.info("🧪 [评估] 初始化 RAGService...")
            rag = RAGService(rag_db)
        except Exception as e:
            rag_db.close()
            raise e

        eval_data = []
        try:
            for ds in datasets:
                logger.info(f"  📝 收集RAG输出: {ds.question[:30]}...")
                try:
                    outputs = rag.collect_eval_output(ds.question)
                    eval_data.append({
                        "dataset_id": ds.id,
                        "user_input": ds.question,
                        "retrieved_contexts": outputs["contexts"],
                        "response": outputs["answer"],
                        "reference": ds.reference_answer
                    })
                except Exception as e:
                    logger.error(f"  ❌ 收集 RAG 输出失败: {e}")
                    eval_data.append({
                        "dataset_id": ds.id,
                        "user_input": ds.question,
                        "retrieved_contexts": [],
                        "response": "",
                        "reference": ds.reference_answer,
                        "_failed": True
                    })
        finally:
            rag_db.close()

        return eval_data

    @staticmethod
    def _build_ragas_dataset(eval_data):
        from ragas import EvaluationDataset
        from ragas.dataset_schema import SingleTurnSample

        samples = []
        for item in eval_data:
            if item.get("_failed"):
                continue
            samples.append(SingleTurnSample(
                user_input=item["user_input"],
                retrieved_contexts=item["retrieved_contexts"],
                response=item["response"],
                reference=item["reference"]
            ))

        return EvaluationDataset(samples=samples)

    @staticmethod
    def _run_ragas_evaluate(eval_dataset, metrics):
        from ragas import evaluate
        from ragas import RunConfig

        run_config = RunConfig(timeout=180, max_retries=3, max_wait=10)

        logger.info(f"🧪 [评估] 调用 RAGAS evaluate()，共 {len(eval_dataset.samples)} 条数据...")
        results = evaluate(
            dataset=eval_dataset,
            metrics=metrics,
            show_progress=True,
            raise_exceptions=False,
            run_config=run_config
        )
        return results

    @staticmethod
    def run_evaluation(db: Session, run_id: str):
        logger.info(f"🧪 [评估] 开始执行评估批次: {run_id}")

        datasets = db.query(EvalDataset).all()
        if not datasets:
            logger.warning("评估数据集为空，跳过评估")
            return

        eval_run = EvalRun(
            run_id=run_id,
            status="running",
            total=len(datasets),
            completed_count=0,
            failed_count=0
        )
        db.add(eval_run)
        db.commit()

        eval_data = []
        try:
            eval_data = EvaluationService._collect_rag_outputs(datasets, db)
        except Exception as e:
            logger.error(f"❌ RAGService 初始化失败: {e}")
            eval_run.status = "failed"
            eval_run.updated_at = datetime.now()
            db.commit()
            return

        failed_items = [item for item in eval_data if item.get("_failed")]
        valid_items = [item for item in eval_data if not item.get("_failed")]

        for item in failed_items:
            eval_result = EvalResult(
                dataset_id=item["dataset_id"],
                question=item["user_input"],
                generated_answer="",
                retrieved_contexts=[],
                reference_answer=item["reference"],
                status="failed",
                run_id=run_id
            )
            db.add(eval_result)
            db.commit()
            eval_run.failed_count += 1
            eval_run.updated_at = datetime.now()
            db.commit()

        if not valid_items:
            logger.error("所有问题的 RAG 输出收集失败，评估终止")
            eval_run.status = "failed"
            eval_run.updated_at = datetime.now()
            db.commit()
            return

        try:
            evaluator_llm = EvaluationService._build_evaluator_llm(db)
            evaluator_embeddings = EvaluationService._build_evaluator_embeddings(db)
            metrics = EvaluationService._build_ragas_metrics(evaluator_llm, evaluator_embeddings)

            eval_dataset = EvaluationService._build_ragas_dataset(eval_data)
            ragas_results = EvaluationService._run_ragas_evaluate(eval_dataset, metrics)

            result_df = ragas_results.to_pandas()
            logger.info(f"🧪 [评估] RAGAS 评估完成，结果列: {list(result_df.columns)}")
            logger.info(f"🧪 [评估] RAGAS 原始结果:\n{result_df.to_string()}")

        except Exception as e:
            logger.error(f"❌ RAGAS 评估执行失败: {e}")
            for item in valid_items:
                eval_result = EvalResult(
                    dataset_id=item["dataset_id"],
                    question=item["user_input"],
                    generated_answer=item["response"],
                    retrieved_contexts=item["retrieved_contexts"],
                    reference_answer=item["reference"],
                    status="failed",
                    run_id=run_id
                )
                db.add(eval_result)
                db.commit()
                eval_run.failed_count += 1
                eval_run.updated_at = datetime.now()
                db.commit()

            eval_run.status = "completed"
            eval_run.updated_at = datetime.now()
            db.commit()
            return

        metric_column_map = {}
        for col in result_df.columns:
            col_lower = col.lower()
            if "faithfulness" in col_lower:
                metric_column_map["faithfulness"] = col
            elif "context_recall" in col_lower or "llm_context_recall" in col_lower:
                metric_column_map["context_recall"] = col
            elif "factual_correctness" in col_lower:
                metric_column_map["factual_correctness"] = col
            elif "answer_relevancy" in col_lower:
                metric_column_map["response_relevancy"] = col

        logger.info(f"🧪 [评估] 指标列映射: {metric_column_map}")

        for idx, item in enumerate(valid_items):
            if idx >= len(result_df):
                break

            row = result_df.iloc[idx]
            score_vals_map = {}
            nan_fields = []
            for db_field, col_name in metric_column_map.items():
                val = row.get(col_name)
                float_val = _safe_float(val)
                score_vals_map[db_field] = float_val
                if float_val is None and val is not None:
                    nan_fields.append(db_field)

            score_vals = [v for v in score_vals_map.values() if v is not None]
            avg = round(sum(score_vals) / len(score_vals), 4) if score_vals else None

            has_any_score = any(v is not None for v in score_vals_map.values())

            analysis_data = {}
            for db_field, col_name in metric_column_map.items():
                val = row.get(col_name)
                if val is not None:
                    analysis_data[db_field] = f"RAGAS {col_name}: {val}"
            if nan_fields:
                analysis_data["nan_fields"] = f"指标计算失败: {', '.join(nan_fields)}"

            if has_any_score:
                result_status = "completed"
            else:
                result_status = "failed"

            eval_result = EvalResult(
                dataset_id=item["dataset_id"],
                question=item["user_input"],
                generated_answer=item["response"],
                retrieved_contexts=item["retrieved_contexts"],
                reference_answer=item["reference"],
                faithfulness=score_vals_map.get("faithfulness"),
                factual_correctness=score_vals_map.get("factual_correctness"),
                response_relevancy=score_vals_map.get("response_relevancy"),
                context_recall=score_vals_map.get("context_recall"),
                avg_score=avg,
                analysis=analysis_data if analysis_data else None,
                status=result_status,
                run_id=run_id
            )
            db.add(eval_result)
            db.commit()

            if result_status == "completed":
                eval_run.completed_count += 1
            else:
                eval_run.failed_count += 1
            eval_run.updated_at = datetime.now()
            db.commit()

        eval_run.status = "completed"
        eval_run.updated_at = datetime.now()
        db.commit()
        logger.info(f"🎉 [评估] 评估结果已存入数据库，批次: {run_id}")

    @staticmethod
    def get_run_status(db: Session, run_id: str) -> dict:
        eval_run = db.query(EvalRun).filter(EvalRun.run_id == run_id).first()
        if not eval_run:
            return {"status": "not_found", "run_id": run_id}
        return {
            "run_id": eval_run.run_id,
            "status": eval_run.status,
            "total": eval_run.total,
            "completed_count": eval_run.completed_count,
            "failed_count": eval_run.failed_count,
            "created_at": eval_run.created_at.strftime("%Y-%m-%d %H:%M:%S") if eval_run.created_at else "",
            "updated_at": eval_run.updated_at.strftime("%Y-%m-%d %H:%M:%S") if eval_run.updated_at else ""
        }

    @staticmethod
    def get_latest_scores(db: Session) -> dict:
        latest_run = db.query(EvalRun).order_by(EvalRun.created_at.desc()).first()
        if not latest_run:
            return {"status": "no_data", "message": "暂无评估记录"}

        run_id = latest_run.run_id
        results = db.query(EvalResult).filter(EvalResult.run_id == run_id).all()

        if not results:
            return {"status": "no_data"}

        faithfulness_vals = [_safe_float(r.faithfulness) for r in results if _safe_float(r.faithfulness) is not None]
        factual_correctness_vals = [_safe_float(r.factual_correctness) for r in results if _safe_float(r.factual_correctness) is not None]
        response_relevancy_vals = [_safe_float(r.response_relevancy) for r in results if _safe_float(r.response_relevancy) is not None]
        context_recall_vals = [_safe_float(r.context_recall) for r in results if _safe_float(r.context_recall) is not None]

        def avg(vals):
            return round(sum(vals) / len(vals), 4) if vals else None

        return {
            "status": "success",
            "run_id": run_id,
            "run_status": latest_run.status,
            "total_questions": latest_run.total,
            "completed_count": latest_run.completed_count,
            "failed_count": latest_run.failed_count,
            "avg_scores": {
                "faithfulness": avg(faithfulness_vals),
                "factual_correctness": avg(factual_correctness_vals),
                "response_relevancy": avg(response_relevancy_vals),
                "context_recall": avg(context_recall_vals),
            },
            "details": [
                {
                    "id": r.id,
                    "question": r.question[:50] + "..." if len(r.question) > 50 else r.question,
                    "faithfulness": _safe_float(r.faithfulness),
                    "factual_correctness": _safe_float(r.factual_correctness),
                    "response_relevancy": _safe_float(r.response_relevancy),
                    "context_recall": _safe_float(r.context_recall),
                    "avg_score": _safe_float(r.avg_score),
                    "analysis": r.analysis if r.analysis else {},
                    "result_status": r.status,
                }
                for r in results
            ]
        }

    @staticmethod
    def get_evaluation_history(db: Session, limit: int = 10) -> list:
        from sqlalchemy import func
        runs = db.query(EvalRun).order_by(EvalRun.created_at.desc()).limit(limit).all()

        return [
            {
                "run_id": r.run_id,
                "status": r.status,
                "total_questions": r.total,
                "completed_count": r.completed_count,
                "failed_count": r.failed_count,
                "avg_score": _safe_float(
                    round(
                        db.query(func.avg(EvalResult.avg_score)).filter(
                            EvalResult.run_id == r.run_id,
                            EvalResult.avg_score.isnot(None)
                        ).scalar() or 0, 4
                    )
                ),
                "run_time": r.created_at.strftime("%Y-%m-%d %H:%M:%S") if r.created_at else ""
            }
            for r in runs
        ]
