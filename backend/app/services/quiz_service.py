# app/services/quiz_service.py

import random
import json
import re
import logging
from sqlalchemy.orm import Session
from sqlalchemy.sql.expression import func

from app.models.knowledge import KnowledgeDoc
from app.models.quiz import Question, UserQuizRecord
from app.core.prompts import QUIZ_GENERATION_PROMPT
from app.core.constants import QuizConstants  # 🌟 引入出题常量

logger = logging.getLogger(__name__)

class QuizService:
    def __init__(self, llm):
        self.llm = llm

    async def generate_daily_quiz(self, db: Session, count=QuizConstants.DEFAULT_GENERATE_COUNT):
        """
        核心优化：直接从数据库读取 Docling 预处理好的文本，速度极快且 0 报错！
        """
        # 使用常量替换硬编码的文档抽取数量
        docs = db.query(KnowledgeDoc).order_by(func.random()).limit(QuizConstants.DOC_SAMPLE_LIMIT).all()
        if not docs:
            logger.warning("知识库为空，无法生成题目")
            return[]

        all_valid_splits =[]

        for doc in docs:
            # 🌟 降维打击：直接读取刚才存入数据库的优质 Markdown 文本，彻底告别 PyPDFLoader！
            if doc.parsed_content and isinstance(doc.parsed_content, list):
                for chunk_text in doc.parsed_content:
                    # 使用常量替换硬编码长度
                    if len(chunk_text) > QuizConstants.MIN_CHUNK_LENGTH:
                        all_valid_splits.append((doc.id, chunk_text))
            else:
                logger.warning(f"文档 {doc.filename} 没有可用的解析缓存，跳过出题。")

        if not all_valid_splits:
            return[]

        sample_size = min(count, len(all_valid_splits))
        selected_splits = random.sample(all_valid_splits, sample_size)
        new_questions =[]

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
                # ✨ Python 终极防作弊洗牌算法 (使用常量)
                # ==========================================
                original_options = q_data['options']
                ai_correct_letter = q_data['correct_answer'].strip().upper()

                correct_text_raw = ""
                for opt in original_options:
                    if opt.startswith(ai_correct_letter):
                        correct_text_raw = re.sub(r'^[A-D][.、:：\s]+', '', opt)
                        break

                pure_texts = [re.sub(r'^[A-D][.、:：\s]+', '', opt) for opt in original_options]
                random.shuffle(pure_texts)

                shuffled_options =[]
                final_correct_letter = "A"
                # 使用常量替换硬编码的字母表
                letters = QuizConstants.OPTIONS_LETTERS

                for i, text in enumerate(pure_texts):
                    shuffled_options.append(f"{letters[i]}. {text}")
                    if text == correct_text_raw:
                        final_correct_letter = letters[i]
                # ==========================================

                question = Question(
                    source_doc_id=doc_id,
                    content=q_data['content'],
                    options=shuffled_options,
                    correct_answer=final_correct_letter,
                    explanation=q_data['explanation'],
                    # 使用常量替换硬难度星级
                    difficulty=random.randint(QuizConstants.MIN_DIFFICULTY, QuizConstants.MAX_DIFFICULTY)
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