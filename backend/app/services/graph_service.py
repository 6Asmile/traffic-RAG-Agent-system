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

    async def build_from_texts(self, db: Session, text_list: list):
        """批量提取并构建图谱"""
        for text in text_list:
            # 使用常量替换硬编码的最小长度
            if not text or len(text) < GraphConstants.MIN_TEXT_LENGTH:
                continue

            prompt = GRAPH_EXTRACTION_PROMPT.format(text=text)
            try:
                res = self.llm.invoke(prompt)
                content = res.content.replace("```json", "").replace("```", "").strip()

                match = re.search(r'\[.*\]', content, re.DOTALL)
                if not match: continue

                triples = json.loads(match.group())
                for item in triples:
                    if not all(k in item for k in['s', 'cat_s', 'r', 't', 'cat_t']):
                        continue

                    s_node = self._get_or_create(db, item['s'], item['cat_s'])
                    t_node = self._get_or_create(db, item['t'], item['cat_t'])

                    exists = db.query(GraphEdge).filter_by(
                        source_id=s_node.id,
                        target_id=t_node.id,
                        relation=item['r']
                    ).first()

                    if not exists:
                        db.add(GraphEdge(source_id=s_node.id, target_id=t_node.id, relation=item['r']))

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