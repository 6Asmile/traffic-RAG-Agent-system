# app/services/quiz_service.py 完整替换

import json
import re
import os
import random
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func
from langchain_community.document_loaders import PyPDFLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.models.knowledge import KnowledgeDoc
from app.models.quiz import Question, UserQuizRecord
from app.core.config import settings
from app.core.prompts import QUIZ_GENERATION_PROMPT


class QuizService:
    def __init__(self, llm):
        self.llm = llm

    async def generate_daily_quiz(self, db: Session, count=3):
        """
        优化后的出题逻辑：
        1. 随机选文档 -> 2. 文档切片 -> 3. 随机选切片 -> 4. AI 基于切片出题
        """
        # 1. 随机获取知识库中的 PDF 记录
        docs = db.query(KnowledgeDoc).order_by(func.random()).limit(count).all()

        if not docs:
            return []

        new_questions = []
        upload_dir = os.path.join(settings.BASE_DIR, "data", "uploads")

        for doc in docs:
            file_path = os.path.join(upload_dir, doc.filename)
            if not os.path.exists(file_path):
                continue

            try:
                # 2. 加载并切分文档 (模拟 RAG 的切分逻辑)
                loader = PyPDFLoader(file_path)
                pages = loader.load()
                text_splitter = RecursiveCharacterTextSplitter(
                    chunk_size=600,  # 适中的长度，包含完整法条
                    chunk_overlap=100
                )
                splits = text_splitter.split_documents(pages)

                if not splits: continue

                # 3. 【核心优化】随机抽取一个切片片段，确保题目多样性
                # 过滤掉太短的片段（比如目录或页眉）
                valid_splits = [s for s in splits if len(s.page_content) > 100]
                if not valid_splits: continue

                random_split = random.choice(valid_splits)

                # 4. 调用 AI 出题
                prompt = QUIZ_GENERATION_PROMPT.format(context=random_split.page_content)
                res = self.llm.invoke(prompt)

                # 5. 清洗 JSON
                content = res.content.replace("```json", "").replace("```", "").strip()
                q_data = json.loads(content)

                # 6. 存入数据库
                question = Question(
                    source_doc_id=doc.id,
                    content=q_data['content'],
                    options=q_data['options'],
                    correct_answer=q_data['correct_answer'],  # 注意字段名对齐
                    explanation=q_data['explanation'],
                    difficulty=random.randint(1, 3)  # 随机难度
                )
                db.add(question)
                new_questions.append(question)

            except Exception as e:
                print(f"出题生成失败: {e}")
                continue

        db.commit()
        return new_questions

    def get_user_stats(self, db: Session, user_id: int):
        """获取用户做题统计"""
        total = db.query(UserQuizRecord).filter_by(user_id=user_id).count()
        correct = db.query(UserQuizRecord).filter_by(user_id=user_id, is_correct=True).count()
        return {
            "total": total,
            "correct_count": correct,
            "correct_rate": round((correct / total * 100), 1) if total > 0 else 0
        }