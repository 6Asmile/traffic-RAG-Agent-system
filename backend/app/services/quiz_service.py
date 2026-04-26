# app/services/quiz_service.py

import os
import random
import json
import re
import logging
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func
from langchain_community.document_loaders import PyPDFLoader, TextLoader, Docx2txtLoader
from langchain_text_splitters import RecursiveCharacterTextSplitter
from app.models.knowledge import KnowledgeDoc
from app.models.quiz import Question, UserQuizRecord
from app.core.config import settings
from app.core.prompts import QUIZ_GENERATION_PROMPT

logger = logging.getLogger(__name__)


class QuizService:
    def __init__(self, llm):
        self.llm = llm

    def _get_loader(self, file_path, ext):
        if ext == 'pdf': return PyPDFLoader(file_path)
        if ext == 'txt': return TextLoader(file_path, encoding='utf-8')
        if ext == 'docx': return Docx2txtLoader(file_path)
        return None

    async def generate_daily_quiz(self, db: Session, count=5):
        docs = db.query(KnowledgeDoc).order_by(func.random()).limit(3).all()
        if not docs:
            logger.warning("知识库为空，无法生成题目")
            return []

        all_valid_splits = []
        upload_dir = os.path.join(settings.BASE_DIR, "data", "uploads")

        for doc in docs:
            file_path = os.path.join(upload_dir, doc.filename)
            if not os.path.exists(file_path):
                continue

            ext = doc.filename.split('.')[-1].lower()
            loader = self._get_loader(file_path, ext)
            if not loader:
                continue

            try:
                pages = loader.load()
                text_splitter = RecursiveCharacterTextSplitter(chunk_size=600, chunk_overlap=100)
                splits = text_splitter.split_documents(pages)
                for s in splits:
                    if len(s.page_content) > 100:
                        all_valid_splits.append((doc.id, s.page_content))
            except Exception as e:
                logger.error(f"读取文档切片失败 {doc.filename}: {e}")
                continue

        if not all_valid_splits:
            return []

        sample_size = min(count, len(all_valid_splits))
        selected_splits = random.sample(all_valid_splits, sample_size)
        new_questions = []

        for doc_id, chunk_text in selected_splits:
            prompt = QUIZ_GENERATION_PROMPT.format(context=chunk_text)

            try:
                res = self.llm.invoke(prompt)
                raw_content = res.content.strip()

                match = re.search(r'\{[\s\S]*\}', raw_content)
                if not match:
                    continue

                q_data = json.loads(match.group())

                # ==========================================
                # ✨ 核心科技：Python 终极防作弊洗牌算法 ✨
                # ==========================================
                original_options = q_data['options']
                ai_correct_letter = q_data['correct_answer'].strip().upper()  # AI给的字母

                # 1. 找到 AI 说的那个正确答案的纯文本
                correct_text_raw = ""
                for opt in original_options:
                    if opt.startswith(ai_correct_letter):
                        # 扒掉 "A. " 或 "A、" 前缀
                        correct_text_raw = re.sub(r'^[A-D][.、:：\s]+', '', opt)
                        break

                # 2. 扒掉所有选项的前缀，提取纯文本
                pure_texts = [re.sub(r'^[A-D][.、:：\s]+', '', opt) for opt in original_options]

                # 3. 强行打乱这四个文本的顺序
                random.shuffle(pure_texts)

                # 4. 重新贴上 A, B, C, D，并锁定新的正确答案字母
                shuffled_options = []
                final_correct_letter = "A"
                letters = ["A", "B", "C", "D"]

                for i, text in enumerate(pure_texts):
                    shuffled_options.append(f"{letters[i]}. {text}")
                    # 如果打乱后的这段文本跟当初的正确文本吻合，那这个字母就是新的正确答案
                    if text == correct_text_raw:
                        final_correct_letter = letters[i]
                # ==========================================

                question = Question(
                    source_doc_id=doc_id,
                    content=q_data['content'],
                    options=shuffled_options,  # 存入洗牌后的选项
                    correct_answer=final_correct_letter,  # 存入洗牌后的正确字母
                    explanation=q_data['explanation'],
                    difficulty=random.randint(3, 5)
                )
                db.add(question)
                new_questions.append(question)

            except Exception as e:
                logger.error(f"单道题目生成解析失败: {e}")
                continue

        db.commit()
        return new_questions

    def get_user_stats(self, db: Session, user_id: int):
        total = db.query(UserQuizRecord).filter_by(user_id=user_id).count()
        correct = db.query(UserQuizRecord).filter_by(user_id=user_id, is_correct=True).count()
        return {
            "total": total,
            "correct_count": correct,
            "correct_rate": round((correct / total * 100), 1) if total > 0 else 0
        }