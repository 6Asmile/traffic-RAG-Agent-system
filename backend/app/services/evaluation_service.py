import json
import logging
import math
import re
from datetime import datetime
from typing import List, Optional

from langchain_openai import ChatOpenAI
from sqlalchemy.orm import Session

from app.models.evaluation import EvalDataset, EvalResult, EvalRun
from app.services.config_service import ConfigService

logger = logging.getLogger("EvaluationService")

RETRIEVAL_EVAL_PROMPT = """你是一个专业的RAG系统评估专家，专注于评估检索质量。请对以下问答结果进行评估。

【问题】
{question}

【系统生成的回答】
{generated_answer}

【检索到的上下文】
{retrieved_contexts}

【参考答案】
{reference_answer}

请从以下两个维度评分（0-10分，整数）：

1. 忠实度(Faithfulness)：系统回答是否完全基于检索到的上下文，有无编造或幻觉？
   - 10分：完全基于上下文，无任何编造
   - 5分：部分内容基于上下文，有少量编造
   - 0分：回答内容完全与上下文无关或大量编造

2. 上下文召回率(Context Recall)：检索到的上下文是否包含了参考答案所需的关键信息？
   - 10分：上下文包含参考答案所有关键信息
   - 5分：上下文包含部分关键信息
   - 0分：上下文完全不包含参考答案的任何信息

请严格按照以下JSON格式返回，不要添加任何其他内容：
{{"faithfulness": X, "context_recall": X}}"""

ANSWER_EVAL_PROMPT = """你是一个专业的RAG系统评估专家，专注于评估答案质量。请对以下问答结果进行评估。

【问题】
{question}

【系统生成的回答】
{generated_answer}

【参考答案】
{reference_answer}

请从以下两个维度评分（0-10分，整数）：

1. 答案准确性(Answer Accuracy)：系统回答与参考答案在事实层面是否一致？
   - 10分：与参考答案完全一致
   - 5分：部分事实一致，有少量偏差
   - 0分：与参考答案完全矛盾

2. 回答相关性(Answer Relevancy)：系统回答是否直接针对问题，是否切题？
   - 10分：完全切题，直接回答了问题
   - 5分：部分切题，但包含无关内容
   - 0分：完全跑题

请严格按照以下JSON格式返回，不要添加任何其他内容：
{{"answer_accuracy": X, "answer_relevancy": X}}"""


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


def _parse_llm_json(response_text: str, score_keys: list) -> dict:
    json_match = re.search(r'\{[^{}]*\}', response_text, re.DOTALL)
    if not json_match:
        raise ValueError(f"LLM 回复中未找到 JSON: {response_text[:200]}")

    raw = json.loads(json_match.group())

    scores = {}
    for key in score_keys:
        val = raw.get(key)
        if val is not None:
            val = float(val)
            val = max(0.0, min(1.0, val / 10.0))
        scores[key] = val

    return scores


class EvaluationService:

    @staticmethod
    def _get_llm(db: Session):
        llm_cfg = ConfigService.get_active_config(db, "llm")
        if not llm_cfg:
            raise Exception("LLM 配置缺失，无法执行评估")

        llm = ChatOpenAI(
            model=llm_cfg.model_name,
            openai_api_key=llm_cfg.api_key,
            openai_api_base=llm_cfg.base_url,
            temperature=0,
            request_timeout=300
        )
        return llm

    @staticmethod
    def _evaluate_retrieval(llm, item: dict) -> dict:
        contexts_str = "\n".join(
            [f"[资料{i + 1}]: {c}" for i, c in enumerate(item.get("retrieved_contexts", []))]
        ) if item.get("retrieved_contexts") else "（无检索上下文）"

        prompt = RETRIEVAL_EVAL_PROMPT.format(
            question=item["user_input"],
            generated_answer=item["response"],
            retrieved_contexts=contexts_str,
            reference_answer=item["reference"]
        )

        response = llm.invoke(prompt).content
        return _parse_llm_json(response, ["faithfulness", "context_recall"])

    @staticmethod
    def _evaluate_answer(llm, item: dict) -> dict:
        prompt = ANSWER_EVAL_PROMPT.format(
            question=item["user_input"],
            generated_answer=item["response"],
            reference_answer=item["reference"]
        )

        response = llm.invoke(prompt).content
        return _parse_llm_json(response, ["answer_accuracy", "answer_relevancy"])

    @staticmethod
    def _evaluate_single(llm, item: dict) -> dict:
        retrieval_scores = EvaluationService._evaluate_retrieval(llm, item)
        answer_scores = EvaluationService._evaluate_answer(llm, item)
        return {**retrieval_scores, **answer_scores}

    @staticmethod
    def _collect_rag_outputs_with_rag(query: str, rag) -> dict:
        search_query = rag.strict_clean(query)

        if not rag.vector_db:
            return {"answer": "知识库为空", "contexts": []}

        faiss_docs = rag.vector_db.similarity_search(search_query, k=40)

        bm25_docs = []
        if rag.bm25_instance:
            import jieba
            tokenized_query = list(jieba.cut(search_query))
            scores = rag.bm25_instance.get_scores(tokenized_query)
            top_n = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:20]
            from langchain_core.documents import Document
            bm25_docs = [Document(page_content=rag.bm25_corpus[i]) for i in top_n if scores[i] > 0]

        candidates = {}
        for d in faiss_docs + bm25_docs:
            if d.page_content not in candidates:
                candidates[d.page_content] = d.page_content
        candidate_list = list(candidates.values())

        final_docs = []
        try:
            rerank_results = rag.reranker.rerank(search_query, candidate_list, top_n=10)
            for res in rerank_results:
                score = res.get('relevance_score', -100)
                idx = res.get('index')
                if score < 0.05:
                    continue
                final_docs.append(candidate_list[idx])
        except Exception as e:
            logger.warning(f"Rerank 异常，降级截取: {e}")
            final_docs = candidate_list[:5]

        from app.core.prompts import RAG_SYSTEM_PROMPT
        context = "\n".join([f"[资料{i + 1}]: {d}" for i, d in enumerate(final_docs)])
        final_prompt = RAG_SYSTEM_PROMPT.format(
            context=context,
            graph_context="暂无图谱逻辑关联",
            history="",
            query=query
        )

        answer = rag.llm.invoke(final_prompt).content

        return {
            "answer": answer,
            "contexts": final_docs
        }

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

        llm = EvaluationService._get_llm(db)

        from app.services.rag_service import RAGService
        from app.db.session import SessionLocal

        rag_db = SessionLocal()
        try:
            logger.info("🧪 [评估] 初始化 RAGService (仅一次)...")
            rag = RAGService(rag_db)
        except Exception as e:
            logger.error(f"❌ RAGService 初始化失败: {e}")
            rag_db.close()
            eval_run.status = "failed"
            eval_run.updated_at = datetime.now()
            db.commit()
            return

        eval_data = []
        try:
            for ds in datasets:
                logger.info(f"  📝 收集RAG输出: {ds.question[:30]}...")
                try:
                    outputs = EvaluationService._collect_rag_outputs_with_rag(ds.question, rag)
                    eval_data.append({
                        "dataset_id": ds.id,
                        "user_input": ds.question,
                        "retrieved_contexts": outputs["contexts"],
                        "response": outputs["answer"],
                        "reference": ds.reference_answer
                    })
                except Exception as e:
                    logger.error(f"  ❌ 收集 RAG 输出失败: {e}")
                    eval_result = EvalResult(
                        dataset_id=ds.id,
                        question=ds.question,
                        generated_answer="",
                        retrieved_contexts=[],
                        reference_answer=ds.reference_answer,
                        status="failed",
                        run_id=run_id
                    )
                    db.add(eval_result)
                    db.commit()
                    eval_run.failed_count += 1
                    eval_run.updated_at = datetime.now()
                    db.commit()
                    continue
        finally:
            rag_db.close()

        if not eval_data:
            logger.error("所有问题的 RAG 输出收集失败，评估终止")
            eval_run.status = "failed"
            eval_run.updated_at = datetime.now()
            db.commit()
            return

        for i, data_item in enumerate(eval_data):
            logger.info(f"  📊 评估第 {i + 1}/{len(eval_data)} 条: {data_item['user_input'][:30]}...")
            try:
                scores = EvaluationService._evaluate_single(llm, data_item)

                faithfulness_val = _safe_float(scores.get("faithfulness"))
                answer_accuracy_val = _safe_float(scores.get("answer_accuracy"))
                answer_relevancy_val = _safe_float(scores.get("answer_relevancy"))
                context_recall_val = _safe_float(scores.get("context_recall"))

                score_vals = [v for v in [faithfulness_val, answer_accuracy_val, answer_relevancy_val, context_recall_val] if v is not None]
                avg = round(sum(score_vals) / len(score_vals), 4) if score_vals else None

                has_any_score = any(v is not None for v in [faithfulness_val, answer_accuracy_val, answer_relevancy_val, context_recall_val])

                eval_result = EvalResult(
                    dataset_id=data_item["dataset_id"],
                    question=data_item["user_input"],
                    generated_answer=data_item["response"],
                    retrieved_contexts=data_item["retrieved_contexts"],
                    reference_answer=data_item["reference"],
                    faithfulness=faithfulness_val,
                    answer_accuracy=answer_accuracy_val,
                    answer_relevancy=answer_relevancy_val,
                    context_recall=context_recall_val,
                    avg_score=avg,
                    status="completed" if has_any_score else "failed",
                    run_id=run_id
                )
                db.add(eval_result)
                db.commit()

                if has_any_score:
                    eval_run.completed_count += 1
                else:
                    eval_run.failed_count += 1
                eval_run.updated_at = datetime.now()
                db.commit()

            except Exception as e:
                logger.error(f"  ❌ 评估失败: {e}")
                eval_result = EvalResult(
                    dataset_id=data_item["dataset_id"],
                    question=data_item["user_input"],
                    generated_answer=data_item["response"],
                    retrieved_contexts=data_item["retrieved_contexts"],
                    reference_answer=data_item["reference"],
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
        answer_accuracy_vals = [_safe_float(r.answer_accuracy) for r in results if _safe_float(r.answer_accuracy) is not None]
        answer_relevancy_vals = [_safe_float(r.answer_relevancy) for r in results if _safe_float(r.answer_relevancy) is not None]
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
                "answer_accuracy": avg(answer_accuracy_vals),
                "answer_relevancy": avg(answer_relevancy_vals),
                "context_recall": avg(context_recall_vals),
            },
            "details": [
                {
                    "id": r.id,
                    "question": r.question[:50] + "..." if len(r.question) > 50 else r.question,
                    "faithfulness": _safe_float(r.faithfulness),
                    "answer_accuracy": _safe_float(r.answer_accuracy),
                    "answer_relevancy": _safe_float(r.answer_relevancy),
                    "context_recall": _safe_float(r.context_recall),
                    "avg_score": _safe_float(r.avg_score),
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
