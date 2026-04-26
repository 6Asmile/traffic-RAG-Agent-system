# app/services/graph_service.py

import json
import re
import logging
from sqlalchemy.orm import Session
from app.models.graph import GraphNode, GraphEdge
from app.core.prompts import GRAPH_EXTRACTION_PROMPT

logger = logging.getLogger("GraphService")


class GraphService:
    def __init__(self, llm):
        self.llm = llm

    async def build_from_texts(self, db: Session, text_list: list):
        """批量提取并构建图谱"""
        for text in text_list:
            if not text or len(text) < 20: continue

            prompt = GRAPH_EXTRACTION_PROMPT.format(text=text)
            try:
                res = self.llm.invoke(prompt)
                # 兼容处理：去掉 AI 可能返回的 ```json 代码块标记
                content = res.content.replace("```json", "").replace("```", "").strip()

                match = re.search(r'\[.*\]', content, re.DOTALL)
                if not match: continue

                triples = json.loads(match.group())
                for item in triples:
                    # 校验字段是否存在，防止 AI 漏写
                    if not all(k in item for k in ['s', 'cat_s', 'r', 't', 'cat_t']):
                        continue

                    # 获取或创建节点
                    s_node = self._get_or_create(db, item['s'], item['cat_s'])
                    t_node = self._get_or_create(db, item['t'], item['cat_t'])

                    # 建立关系（去重）
                    exists = db.query(GraphEdge).filter_by(
                        source_id=s_node.id,
                        target_id=t_node.id,
                        relation=item['r']
                    ).first()

                    if not exists:
                        db.add(GraphEdge(source_id=s_node.id, target_id=t_node.id, relation=item['r']))

                db.commit()  # 每一段文本提交一次，防止整体失败
                print(f"成功从文本提取并存入三元组")
            except Exception as e:
                db.rollback()
                logger.error(f"处理图谱三元组失败: {e}")

    def _get_or_create(self, db, name, cat):
        node = db.query(GraphNode).filter_by(name=name).first()
        if not node:
            node = GraphNode(name=name, category=cat)
            db.add(node)
            db.flush()  # 立即获取 ID
        return node

    def get_full_graph(self, db: Session):
        nodes = db.query(GraphNode).all()
        edges = db.query(GraphEdge).all()

        # 建立 ID 到节点的快速映射
        node_map = {n.id: n for n in nodes}

        return {
            "nodes": [{"name": n.name, "category": n.category} for n in nodes],
            "links": [{"source": node_map[e.source_id].name,
                       "target": node_map[e.target_id].name,
                       "value": e.relation} for e in edges if e.source_id in node_map and e.target_id in node_map]
        }