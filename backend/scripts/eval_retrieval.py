import argparse
import asyncio
import json
import math
import os
import statistics
import time
from typing import Any

from app.db.session import SessionLocal
from app.services.rag_service import RAGService


def load_dataset(path: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with open(path, "r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            raw = line.strip()
            if not raw:
                continue
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise ValueError(f"第 {line_no} 行不是 JSON object")
            query = str(payload.get("query", "")).strip()
            relevant_texts = payload.get("relevant_texts", [])
            if not query:
                raise ValueError(f"第 {line_no} 行缺少 query")
            if not isinstance(relevant_texts, list) or not relevant_texts:
                raise ValueError(f"第 {line_no} 行缺少 non-empty relevant_texts")
            payload["query"] = query
            payload["relevant_texts"] = [str(x).strip() for x in relevant_texts if str(x).strip()]
            rows.append(payload)
    return rows


def _is_relevant(doc: str, gold_texts: list[str]) -> bool:
    text = str(doc or "").strip()
    if not text:
        return False
    for gold in gold_texts:
        target = str(gold or "").strip()
        if not target:
            continue
        if target in text or text in target:
            return True
    return False


def recall_at_k(docs: list[str], gold_texts: list[str], k: int) -> float:
    if not gold_texts:
        return 0.0
    top_docs = docs[:k]
    hit_count = sum(1 for target in gold_texts if any(_is_relevant(doc, [target]) for doc in top_docs))
    return hit_count / len(gold_texts)


def mrr_at_k(docs: list[str], gold_texts: list[str], k: int) -> float:
    for rank, doc in enumerate(docs[:k], start=1):
        if _is_relevant(doc, gold_texts):
            return 1.0 / rank
    return 0.0


def ndcg_at_k(docs: list[str], gold_texts: list[str], k: int) -> float:
    gains = [1.0 if _is_relevant(doc, gold_texts) else 0.0 for doc in docs[:k]]
    dcg = 0.0
    for i, gain in enumerate(gains, start=1):
        dcg += gain / math.log2(i + 1)

    ideal_hits = min(k, len(gold_texts))
    if ideal_hits <= 0:
        return 0.0
    ideal_dcg = 0.0
    for i in range(1, ideal_hits + 1):
        ideal_dcg += 1.0 / math.log2(i + 1)
    if ideal_dcg <= 0:
        return 0.0
    return dcg / ideal_dcg


async def evaluate(dataset_path: str, mode: str, ks: list[int], output_path: str | None):
    db = SessionLocal()
    service = RAGService(db=db, current_user=None)

    dataset = load_dataset(dataset_path)
    summary: dict[str, Any] = {
        "dataset": os.path.abspath(dataset_path),
        "mode": mode,
        "query_count": len(dataset),
        "metrics": {},
        "avg_latency_ms": 0.0,
        "details": [],
    }

    latencies = []
    per_k_recall: dict[int, list[float]] = {k: [] for k in ks}
    per_k_mrr: dict[int, list[float]] = {k: [] for k in ks}
    per_k_ndcg: dict[int, list[float]] = {k: [] for k in ks}

    for item in dataset:
        query = item["query"]
        gold_texts = item["relevant_texts"]
        started = time.perf_counter()
        result = await service.retrieve_hybrid_docs(query, mode=mode)
        latency_ms = int((time.perf_counter() - started) * 1000)
        latencies.append(latency_ms)

        docs = result.get("final_docs", []) or []
        query_metrics = {}
        for k in ks:
            r = recall_at_k(docs, gold_texts, k)
            m = mrr_at_k(docs, gold_texts, k)
            n = ndcg_at_k(docs, gold_texts, k)
            per_k_recall[k].append(r)
            per_k_mrr[k].append(m)
            per_k_ndcg[k].append(n)
            query_metrics[f"recall@{k}"] = round(r, 4)
            query_metrics[f"mrr@{k}"] = round(m, 4)
            query_metrics[f"ndcg@{k}"] = round(n, 4)

        summary["details"].append(
            {
                "query": query,
                "latency_ms": latency_ms,
                "candidate_count": len(result.get("fused_docs", []) or []),
                "final_count": len(docs),
                "threshold_used": result.get("threshold_used"),
                "top_score": result.get("top_score"),
                "metrics": query_metrics,
            }
        )

    summary["avg_latency_ms"] = round(statistics.mean(latencies), 2) if latencies else 0.0
    for k in ks:
        summary["metrics"][f"recall@{k}"] = round(statistics.mean(per_k_recall[k]), 4) if per_k_recall[k] else 0.0
        summary["metrics"][f"mrr@{k}"] = round(statistics.mean(per_k_mrr[k]), 4) if per_k_mrr[k] else 0.0
        summary["metrics"][f"ndcg@{k}"] = round(statistics.mean(per_k_ndcg[k]), 4) if per_k_ndcg[k] else 0.0

    print(json.dumps(summary["metrics"], ensure_ascii=False, indent=2))
    print(f"avg_latency_ms={summary['avg_latency_ms']}")

    if output_path:
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(summary, f, ensure_ascii=False, indent=2)
        print(f"saved={os.path.abspath(output_path)}")

    db.close()


def parse_ks(raw: str) -> list[int]:
    values = []
    for part in str(raw or "").split(","):
        item = part.strip()
        if not item:
            continue
        values.append(max(int(item), 1))
    return values or [3, 5, 10]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="检索评估脚本（Recall/MRR/nDCG）")
    parser.add_argument("--dataset", required=True, help="JSONL 路径，每行包含 query 与 relevant_texts")
    parser.add_argument("--mode", default="fast", choices=["fast", "expert"], help="检索模式")
    parser.add_argument("--ks", default="3,5,10", help="指标 K 值，逗号分隔")
    parser.add_argument("--output", default="", help="评估结果输出路径(JSON)")
    args = parser.parse_args()

    asyncio.run(
        evaluate(
            dataset_path=args.dataset,
            mode=args.mode,
            ks=parse_ks(args.ks),
            output_path=str(args.output or "").strip() or None,
        )
    )
