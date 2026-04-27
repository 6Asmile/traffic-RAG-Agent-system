# app/services/graph_service.py

import json
import re
import logging
from sqlalchemy.orm import Session

from app.models.graph import GraphNode, GraphEdge
from app.core.prompts import GRAPH_EXTRACTION_PROMPT
from app.core.constants import GraphConstants  # 🌟 引入图谱常量

logger = logging.getLogger("GraphService")

class GraphService:
    def __init__(self, llm):
        self.llm = llm

    @staticmethod
    def _normalize_triple(item: dict) -> dict:
        if not isinstance(item, dict):
            return {}
        subject = str(item.get("s", "")).strip()
        relation = str(item.get("r", "")).strip()
        target = str(item.get("t", "")).strip()
        cat_s = str(item.get("cat_s", "")).strip() or "ENTITY"
        cat_t = str(item.get("cat_t", "")).strip() or "ENTITY"
        if not subject or not relation or not target:
            return {}
        return {"s": subject, "r": relation, "t": target, "cat_s": cat_s, "cat_t": cat_t}

    @staticmethod
    def _fallback_extract_triples(text: str) -> list[dict]:
        raw = str(text or "")
        if not raw:
            return []

        laws = list(dict.fromkeys(re.findall(r"《[^》]{2,80}》", raw)))
        articles = list(dict.fromkeys(re.findall(r"第[一二三四五六七八九十百千万0-9]{1,12}条", raw)))
        triples = []

        # 法条 -> 法规
        for law in laws[:3]:
            for article in articles[:8]:
                triples.append(
                    {
                        "s": article,
                        "cat_s": "ARTICLE",
                        "r": "隶属于",
                        "t": law,
                        "cat_t": "LAW",
                    }
                )

        # 关键处罚词兜底
        penalty_keywords = ["罚款", "拘留", "扣分", "吊销", "暂扣"]
        hit_penalties = [word for word in penalty_keywords if word in raw]
        if laws and hit_penalties:
            law = laws[0]
            for penalty in hit_penalties:
                triples.append(
                    {
                        "s": law,
                        "cat_s": "LAW",
                        "r": "规定",
                        "t": penalty,
                        "cat_t": "PENALTY",
                    }
                )

        return triples

    async def build_from_texts(self, db: Session, text_list: list):
        """批量提取并构建图谱"""
        for text in text_list:
            # 使用常量替换硬编码的最小长度
            if not text or len(text) < GraphConstants.MIN_TEXT_LENGTH:
                continue

            prompt = GRAPH_EXTRACTION_PROMPT.format(text=text)
            try:
                triples = []
                if self.llm is not None:
                    res = self.llm.invoke(prompt)
                    content = str(getattr(res, "content", "") or "").replace("```json", "").replace("```", "").strip()
                    match = re.search(r"\[.*\]", content, re.DOTALL)
                    if match:
                        parsed = json.loads(match.group())
                        if isinstance(parsed, list):
                            triples = parsed

                if not triples:
                    triples = self._fallback_extract_triples(text)

                for raw_item in triples:
                    item = self._normalize_triple(raw_item)
                    if not item:
                        continue

                    s_node = self._get_or_create(db, item["s"], item["cat_s"])
                    t_node = self._get_or_create(db, item["t"], item["cat_t"])

                    exists = db.query(GraphEdge).filter_by(
                        source_id=s_node.id,
                        target_id=t_node.id,
                        relation=item["r"]
                    ).first()
                    if not exists:
                        db.add(GraphEdge(source_id=s_node.id, target_id=t_node.id, relation=item["r"]))

                db.commit()
                print(f"成功从文本提取并存入三元组")
            except Exception as e:
                db.rollback()
                logger.error(f"处理图谱三元组失败: {e}")

    def _get_or_create(self, db, name, cat):
        node = db.query(GraphNode).filter_by(name=name).first()
        if not node:
            node = GraphNode(name=name, category=cat)
            db.add(node)
            db.flush()
        return node

    def get_full_graph(self, db: Session):
        nodes = db.query(GraphNode).all()
        edges = db.query(GraphEdge).all()

        node_map = {n.id: n for n in nodes}

        return {
            "nodes":[{"name": n.name, "category": n.category} for n in nodes],
            "links": [{"source": node_map[e.source_id].name,
                       "target": node_map[e.target_id].name,
                       "value": e.relation} for e in edges if e.source_id in node_map and e.target_id in node_map]
        }
