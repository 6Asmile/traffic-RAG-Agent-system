# app/services/rag_service.py

import json
import html
import gc
import logging
import os
import re
import shutil
import tempfile
import time
from typing import List

import httpx
import jieba

from langchain_community.vectorstores import FAISS
from langchain_core.embeddings import Embeddings
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, SystemMessage
from langchain_openai import ChatOpenAI
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from pypdf import PdfReader, PdfWriter
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session
from docling.document_converter import DocumentConverter

from app.core.config import settings
from app.core.prompts import RAG_SYSTEM_PROMPT, QUERY_REWRITE_PROMPT, AGENT_SYSTEM_PROMPT
from app.core.constants import SystemConfig, AIModelConstants, RedisKeys  # 🌟 引入常量
from app.db.session import SessionLocal
from app.models import User
from app.models.knowledge import KnowledgeDoc
from app.services.cache_service import CacheManager
from app.services.chat_history_utils import (
    append_history_entries,
    build_scoped_redis_key,
    build_langchain_messages,
    dump_history_entries,
    load_history_entries,
    load_history_summary,
    maybe_compact_history_entries,
    merge_history_summary,
    render_history_context,
)
from app.services.config_service import ConfigService
from app.services.hybrid_search import (
    HybridRetrievalConfig,
    parallel_hybrid_retrieve,
    rerank_with_dynamic_threshold,
)
from app.services.retrieval_metrics_service import RetrievalMetricsService
from app.services.tool_service import agent_get_route, agent_search_nearby, agent_get_weather
from app.services.knowledge_parse_service import KnowledgeParseService
from app.services.standard_pdf_parser import StandardPdfParser

os.environ["ANONYMIZED_TELEMETRY"] = "False"
logger = logging.getLogger("RAGService")


class AliyunEmbeddingWrapper(Embeddings):
    def __init__(self, model, api_key, base_url):
        self.model, self.api_key = model, api_key
        # 使用常量替换硬编码
        self.url = base_url.replace(AIModelConstants.COMPATIBLE_MODE_PATH, AIModelConstants.ALIYUN_EMBEDDING_PATH)

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        results = []
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        batch_size = max(1, min(int(getattr(AIModelConstants, "EMBEDDING_BATCH_SIZE", 10)), 10))
        with httpx.Client(timeout=120.0, limits=limits) as client:
            for i in range(0, len(texts), batch_size):
                batch = [str(t) for t in texts[i: i + batch_size]]
                payload = {"model": self.model, "input": batch}
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        resp = client.post(self.url, json=payload, headers=headers)
                        if resp.status_code != 200:
                            raise Exception(f"Embedding API Error: {resp.status_code} - {resp.text}")
                        data = resp.json()
                        results.extend([item["embedding"] for item in data["data"]])
                        break
                    except Exception as e:
                        logger.warning(f"⚠️ 阿里云 Embedding 请求断开 (尝试 {attempt + 1}/{max_retries}): {e}")
                        if attempt == max_retries - 1:
                            raise Exception(f"Embedding 最终失败: {e}")
                        time.sleep(2)

                time.sleep(0.5)

        return results

    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


class AliyunReranker:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        # 使用常量替换硬编码
        self.url = AIModelConstants.ALIYUN_RERANK_URL

    def rerank(self, query: str, documents: List[str], top_n: int = 10) -> List[dict]:
        payload = {
            "model": AIModelConstants.DEFAULT_RERANK_MODEL,
            "input": {"query": query, "documents": documents},
            "parameters": {"return_documents": False, "top_n": top_n}
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.url, json=payload, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                if "output" in data and "results" in data["output"]:
                    return data["output"]["results"]
                return []
            else:
                print(f"Rerank API Error: {resp.text}")
                return []
        except Exception as e:
            print(f"Rerank Exception: {e}")
            return []


class RAGService:
    def __init__(self, db: Session, current_user: User = None):
        self.db = db
        self.current_user_id = getattr(current_user, "id", None)
        emb_cfg = ConfigService.get_active_config(db, "embedding")
        llm_cfg = ConfigService.get_active_config(db, "llm")
        if not emb_cfg or not llm_cfg:
            raise Exception("AI 配置缺失")

        import redis
        try:
            self.redis_client = redis.Redis(host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0,
                                            decode_responses=True)
        except:
            self.redis_client = None

        self.cache = CacheManager(self.redis_client)

        user_prefs = current_user.ai_preferences if (current_user and current_user.ai_preferences) else {}
        final_llm_model = user_prefs.get("llm_model") or llm_cfg.model_name
        final_llm_key = user_prefs.get("llm_key") or llm_cfg.api_key
        final_emb_model = user_prefs.get("embed_model") or emb_cfg.model_name
        final_emb_key = user_prefs.get("embed_key") or emb_cfg.api_key

        # 使用常量替换硬编码 DeepSeek 网址
        llm_base_url = AIModelConstants.DEEPSEEK_BASE_URL if "deepseek" in final_llm_model else llm_cfg.base_url

        self.custom_embeddings = AliyunEmbeddingWrapper(final_emb_model, final_emb_key, emb_cfg.base_url)
        self.reranker = AliyunReranker(final_emb_key, emb_cfg.base_url)

        self.llm = ChatOpenAI(model=final_llm_model, openai_api_key=final_llm_key, openai_api_base=llm_base_url,
                              temperature=0, streaming=True)
        self.rewriter_llm = ChatOpenAI(model=final_llm_model, openai_api_key=final_llm_key,
                                       openai_api_base=llm_base_url, temperature=0.5)

        # 使用常量替换硬编码索引路径
        self.index_path = os.path.abspath(os.path.join(settings.BASE_DIR, SystemConfig.FAISS_INDEX_DIR))
        self.vector_db = None
        if os.path.exists(os.path.join(self.index_path, "index.faiss")):
            self.vector_db = FAISS.load_local(self.index_path, self.custom_embeddings,
                                              allow_dangerous_deserialization=True)

        self.bm25_instance = None
        self.bm25_corpus = []
        self._init_bm25()
        self.retrieval_metrics = RetrievalMetricsService(self.db)

    def _build_memory_keys(self, session_id: str) -> tuple[str, str]:
        history_key = build_scoped_redis_key(RedisKeys.CHAT_HISTORY, session_id, self.current_user_id)
        summary_key = build_scoped_redis_key(RedisKeys.CHAT_SUMMARY, session_id, self.current_user_id)
        return history_key, summary_key

    def _persist_history_with_summary(
        self,
        history_key: str,
        summary_key: str,
        history_entries: list[dict],
        query: str,
        full_answer: str,
    ):
        if not self.redis_client:
            return

        history_list = append_history_entries(
            history_entries,
            query,
            full_answer,
            RedisKeys.MAX_HISTORY_LENGTH,
        )
        compacted_entries, archived_entries = maybe_compact_history_entries(
            history_list,
            RedisKeys.SUMMARY_TRIGGER_TURNS,
            RedisKeys.SUMMARY_KEEP_TURNS,
        )

        if archived_entries:
            summary_text = load_history_summary(self.redis_client, summary_key)
            merged_summary = merge_history_summary(summary_text, archived_entries)
            self.redis_client.setex(summary_key, RedisKeys.HISTORY_EXPIRE_SECONDS, merged_summary)

        self.redis_client.setex(
            history_key,
            RedisKeys.HISTORY_EXPIRE_SECONDS,
            dump_history_entries(compacted_entries),
        )

    def strict_clean(self, text: str) -> str:
        if not text: return ""
        text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'(?<=\d)\s+(?=[a-zA-Z])', '', text)
        text = re.sub(r'(?<=[a-zA-Z])\s+(?=/)', '', text)
        text = re.sub(r'(?<=/)\s+(?=[a-zA-Z])', '', text)
        return text.strip()

    def _init_bm25(self):
        try:
            docs = self.db.query(KnowledgeDoc).all()
            all_texts = []
            for doc in docs:
                if doc.parsed_content and isinstance(doc.parsed_content, list):
                    all_texts.extend(doc.parsed_content)
                else:
                    logger.warning(f"文档 {doc.filename} 没有 parsed_content 缓存，已跳过。")

            if all_texts:
                self.bm25_corpus = all_texts
                tokenized_corpus = [list(jieba.cut(text)) for text in all_texts]
                self.bm25_instance = BM25Okapi(tokenized_corpus)
                logger.info(f"⚡ BM25 极速构建完成，共挂载 {len(all_texts)} 条片段")
            else:
                logger.warning("BM25 初始化为空，知识库没有可用文本")
        except Exception as e:
            logger.error(f"BM25 初始化失败: {e}")

    def _build_hybrid_config(self, mode: str = "fast") -> HybridRetrievalConfig:
        normalized_mode = str(mode or "fast").strip().lower()
        if normalized_mode == "expert":
            return HybridRetrievalConfig(
                faiss_top_k=60,
                bm25_top_n=40,
                fusion_top_n=80,
                rerank_top_n=15,
                rrf_k=60,
                weight_faiss=0.6,
                weight_bm25=0.4,
                score_threshold=0.12,
                dynamic_margin=0.18,
                min_keep=5,
            )
        return HybridRetrievalConfig(
            faiss_top_k=AIModelConstants.FAST_FAISS_TOP_K,
            bm25_top_n=AIModelConstants.FAST_BM25_TOP_N,
            fusion_top_n=AIModelConstants.FAST_FUSION_TOP_N,
            rerank_top_n=AIModelConstants.FAST_RERANK_TOP_N,
            rrf_k=AIModelConstants.FAST_RRF_K,
            weight_faiss=AIModelConstants.FAST_RRF_WEIGHT_FAISS,
            weight_bm25=AIModelConstants.FAST_RRF_WEIGHT_BM25,
            score_threshold=AIModelConstants.DEFAULT_SCORE_THRESHOLD,
            dynamic_margin=AIModelConstants.FAST_DYNAMIC_MARGIN,
            min_keep=AIModelConstants.FAST_MIN_KEEP,
        )

    async def retrieve_hybrid_docs(self, search_query: str, mode: str = "fast") -> dict:
        config = self._build_hybrid_config(mode=mode)
        retrieval = await parallel_hybrid_retrieve(
            query=search_query,
            vector_db=self.vector_db,
            bm25_instance=self.bm25_instance,
            bm25_corpus=self.bm25_corpus,
            config=config,
        )

        candidate_list = retrieval.get("fused_docs", [])
        rerank = rerank_with_dynamic_threshold(
            query=search_query,
            candidates=candidate_list,
            reranker=self.reranker,
            config=config,
        )
        return {
            "config": config,
            "faiss_docs": retrieval.get("faiss_docs", []),
            "bm25_docs": retrieval.get("bm25_docs", []),
            "fused_docs": candidate_list,
            "fused_scores": retrieval.get("fused_scores", {}),
            "final_docs": rerank.get("final_docs", []),
            "rerank_scores": rerank.get("rerank_scores", []),
            "threshold_used": float(rerank.get("threshold_used", config.score_threshold)),
            "top_score": float(rerank.get("top_score", 0.0)),
            "rerank_fallback": bool(rerank.get("fallback", False)),
        }

    @staticmethod
    def _clean_common_noise(text: str) -> str:
        cleaned = html.unescape(str(text or ""))
        cleaned = re.sub(r"[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]", "", cleaned)
        cleaned = re.sub(r"&lt;unknown&gt;", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"<!--\s*image\s*-->", "", cleaned, flags=re.IGNORECASE)
        # 修复 PDF 中常见的数字-单位、字母-斜杠断裂
        cleaned = re.sub(r"(?<=\d)\s+(?=[A-Za-z])", "", cleaned)
        cleaned = re.sub(r"(?<=[A-Za-z])\s+(?=/)", "", cleaned)
        cleaned = re.sub(r"(?<=/)\s+(?=[A-Za-z])", "", cleaned)
        cleaned = re.sub(r"(?<=[A-Za-z])\s+(?=[A-Za-z]/)", "", cleaned)
        cleaned = re.sub(r"[ \t]+", " ", cleaned)
        return cleaned.strip()

    @staticmethod
    def _stitch_cross_page_lines(text: str) -> str:
        if not text:
            return ""
        normalized = str(text)
        # 删除常见页眉页脚痕迹
        normalized = re.sub(r"^\s*第?\s*\d+\s*页\s*$", "", normalized, flags=re.MULTILINE)
        normalized = re.sub(r"\n{3,}", "\n\n", normalized)
        # 跨页断句拼接：前一行非句末标点，后一行非标题/列表时拼接
        normalized = re.sub(r"([^。！？；：:;!?>\]\)])\n+([^\n#\-\*\|])", r"\1\2", normalized)
        return normalized.strip()

    @staticmethod
    def _score_raw_text_candidate(text: str) -> dict:
        raw = str(text or "")
        compact = re.sub(r"\s+", "", raw)
        total = len(compact)
        if total <= 0:
            return {
                "score": 0.0,
                "total_chars": 0,
                "keyword_hits": 0,
                "glyph_code_hits": 0,
                "garbled_ratio": 1.0,
            }

        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", compact))
        cjk_ratio = cjk_chars / max(total, 1)
        glyph_code_hits = len(re.findall(r"/G\d{1,4}", raw))
        cmap_glyph_hits = len(re.findall(r"(?:CID\+\d{2,6}|uni[0-9A-Fa-f]{4,6})", raw))
        unknown_hits = len(re.findall(r"(?:<unknown>|&lt;unknown&gt;|�)", raw, flags=re.IGNORECASE))
        split_digits_hits = len(re.findall(r"(?:\d\s+){5,}\d", raw))
        noisy_symbol_hits = len(re.findall(r"[|/_\-]{5,}", raw))

        keywords = [
            "道路", "交通", "标志", "标线", "速度", "限速", "规定", "标准", "应当", "不得",
            "机动车", "车道", "驾驶", "处罚", "条", "款", "章", "附录", "GB", "km/h",
        ]
        keyword_hits = sum(1 for kw in keywords if kw in raw)

        score = 100.0
        if total < 120:
            score -= 35.0
        score -= min(glyph_code_hits * 1.8, 55.0)
        score -= min(cmap_glyph_hits * 1.5, 35.0)
        score -= min(unknown_hits * 3.0, 30.0)
        score -= min(split_digits_hits * 12.0, 24.0)
        score -= min(noisy_symbol_hits * 4.0, 20.0)
        if total > 500 and keyword_hits < 2:
            score -= 25.0
        if cjk_ratio < 0.08 and keyword_hits < 1:
            score -= 20.0

        garbled_signal = (
            glyph_code_hits * 4
            + cmap_glyph_hits * 3
            + unknown_hits * 6
            + split_digits_hits * 10
            + noisy_symbol_hits * 3
        )
        garbled_ratio = min(float(garbled_signal) / max(total, 1), 1.0)
        score = max(min(score, 100.0), 0.0)

        return {
            "score": round(score, 2),
            "total_chars": total,
            "cjk_ratio": round(cjk_ratio, 4),
            "keyword_hits": int(keyword_hits),
            "glyph_code_hits": int(glyph_code_hits),
            "cmap_glyph_hits": int(cmap_glyph_hits),
            "unknown_hits": int(unknown_hits),
            "split_digits_hits": int(split_digits_hits),
            "noisy_symbol_hits": int(noisy_symbol_hits),
            "garbled_ratio": round(garbled_ratio, 4),
        }

    def _extract_pdf_docling_markdown_batched(
        self,
        file_path: str,
        batch_size: int = 5,
        return_parts: bool = False,
    ) -> tuple[object, int]:
        converter = DocumentConverter()
        reader = PdfReader(file_path)
        total_pages = len(reader.pages)

        print(f"📄 PDF 共 {total_pages} 页，Docling 分批解析中...")
        markdown_parts = []
        for i in range(0, total_pages, batch_size):
            writer = PdfWriter()
            for j in range(i, min(i + batch_size, total_pages)):
                writer.add_page(reader.pages[j])

            with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                writer.write(tmp.name)
                tmp_path = tmp.name

            try:
                result = converter.convert(tmp_path)
                markdown_parts.append(result.document.export_to_markdown())
                print(f"   ⏳ 进度：已解析 {min(i + batch_size, total_pages)} / {total_pages} 页")
                del result
                gc.collect()
            finally:
                if os.path.exists(tmp_path):
                    os.remove(tmp_path)

        if return_parts:
            return markdown_parts, total_pages
        return "\n\n".join(markdown_parts), total_pages

    def _split_semantic_units(self, text: str, metadata: dict | None = None) -> list[tuple[str, dict]]:
        raw = str(text or "").strip()
        if not raw:
            return []

        safe_metadata = dict(metadata or {})
        if len(raw) <= AIModelConstants.CHUNK_SIZE:
            return [(raw, safe_metadata)]

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=AIModelConstants.CHUNK_SIZE,
            chunk_overlap=AIModelConstants.CHUNK_OVERLAP,
            separators=["\n\n", "\n", "。", "；", "，", " "],
            length_function=len,
        )
        pieces = []
        for piece in splitter.split_text(raw):
            normalized = str(piece or "").strip()
            if normalized:
                pieces.append((normalized, dict(safe_metadata)))
        return pieces

    @staticmethod
    def _is_table_chunk(text: str) -> bool:
        raw = str(text or "")
        if re.search(r"\|.+\|", raw) and re.search(r"\|\s*-{2,}\s*\|", raw):
            return True
        lines = [line for line in raw.splitlines() if "|" in line]
        return len(lines) >= 3

    @staticmethod
    def _classify_chunk_role(text: str) -> str:
        raw = str(text or "").strip()
        if not raw:
            return "empty"
        if re.match(r"^第[一二三四五六七八九十百千万0-9]+章\b", raw):
            return "chapter_heading"
        if re.match(r"^第[一二三四五六七八九十百千万0-9]+节\b", raw):
            return "section_heading"
        if re.match(r"^第[一二三四五六七八九十百千万0-9]+条\b", raw):
            return "article_heading"
        if re.match(r"^附录\s*[A-ZＡ-Ｚ一二三四五六七八九十]?\b", raw):
            return "appendix_heading"
        if re.match(r"^表\s*[0-9A-Za-z一二三四五六七八九十\.\-]+", raw):
            return "table_caption"
        if re.match(r"^图\s*[0-9A-Za-z一二三四五六七八九十\.\-]+", raw):
            return "figure_caption"
        return "body"

    @staticmethod
    def _should_keep_short_chunk(text: str, role: str) -> bool:
        raw = str(text or "").strip()
        if not raw:
            return False
        if role in {
            "chapter_heading",
            "section_heading",
            "article_heading",
            "appendix_heading",
            "table_caption",
            "figure_caption",
        }:
            return True
        return False

    @staticmethod
    def _is_garbled_chunk(text: str) -> bool:
        raw = str(text or "")
        if not raw.strip():
            return True
        if "�" in raw or re.search(r"(?:<unknown>|&lt;unknown&gt;)", raw, flags=re.IGNORECASE):
            return True
        if re.search(r"/G\d{1,4}", raw):
            return True
        if re.search(r"(?:\d\s+){5,}\d", raw):
            return True
        if re.search(r"(?:CID\+\d{2,6}|uni[0-9A-Fa-f]{4,6})", raw):
            return True
        compact = re.sub(r"\s+", "", raw)
        if not compact:
            return True
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", compact))
        cjk_ratio = cjk_chars / max(len(compact), 1)
        if len(compact) >= 120 and cjk_ratio < 0.03:
            return True
        noisy = len(re.findall(r"[|/_\-]", compact))
        noisy_ratio = noisy / max(len(compact), 1)
        if noisy_ratio > 0.18:
            return True
        return False

    def _compute_parse_quality(
        self,
        chunks: list[str],
        chunk_types: list[str],
        raw_text: str,
        chunk_roles: list[str] | None = None,
    ) -> dict:
        total = len(chunks)
        if total <= 0:
            return {
                "quality_score": 0.0,
                "empty_chunk_rate": 1.0,
                "garbled_chunk_rate": 1.0,
                "short_chunk_rate": 1.0,
                "table_chunk_rate": 0.0,
                "total_chunks": 0,
            }

        empty_count = 0
        garbled_count = 0
        short_count = 0
        table_count = 0

        for idx, chunk in enumerate(chunks):
            text = str(chunk or "").strip()
            if not text:
                empty_count += 1
                continue
            role = str(chunk_roles[idx]) if chunk_roles and idx < len(chunk_roles) else "body"
            if len(text) < 80 and not self._should_keep_short_chunk(text, role):
                short_count += 1
            if self._is_garbled_chunk(text):
                garbled_count += 1
            if idx < len(chunk_types) and str(chunk_types[idx]) == "table":
                table_count += 1

        empty_rate = round(empty_count / total, 6)
        garbled_rate = round(garbled_count / total, 6)
        short_rate = round(short_count / total, 6)
        table_rate = round(table_count / total, 6)

        # 质量总分：惩罚空块/乱码/超短块，表格识别略加分
        score = 100.0
        score -= empty_rate * 55.0
        score -= garbled_rate * 30.0
        score -= short_rate * 20.0
        score += min(table_rate * 8.0, 5.0)
        score = max(min(score, 100.0), 0.0)

        return {
            "quality_score": round(score, 2),
            "empty_chunk_rate": empty_rate,
            "garbled_chunk_rate": garbled_rate,
            "short_chunk_rate": short_rate,
            "table_chunk_rate": table_rate,
            "total_chunks": total,
            "table_chunk_count": table_count,
            "raw_text_chars": len(str(raw_text or "")),
        }

    def _route_and_extract_document(self, file_path: str, ext: str) -> tuple[str, dict]:
        safe_ext = str(ext or "").strip().lower()
        parse_meta = {
            "ext": safe_ext,
            "route": "unknown",
            "is_scanned_pdf": False,
            "ocr_used": False,
            "ocr_reason": "",
            "total_pages": 0,
        }

        if safe_ext == "txt":
            print("📄 检测到 TXT 文件，采用纯文本解析...")
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
            except UnicodeDecodeError:
                with open(file_path, "r", encoding="gbk") as f:
                    text = f.read()
            parse_meta["route"] = "txt_plain"
            return text, parse_meta

        if safe_ext == "pdf":
            if StandardPdfParser.looks_like_standard_pdf(file_path):
                batch_size = max(int(getattr(AIModelConstants, "PDF_PARSE_BATCH_SIZE", 4)), 1)
                normalized_markdown, standard_meta = StandardPdfParser.parse_docling_batched(
                    file_path=file_path,
                    batch_size=batch_size,
                )
                parse_meta.update(standard_meta)
                return normalized_markdown, parse_meta

            batch_size = max(int(getattr(AIModelConstants, "PDF_PARSE_BATCH_SIZE", 4)), 1)
            try:
                docling_markdown, total_pages = self._extract_pdf_docling_markdown_batched(
                    file_path,
                    batch_size=batch_size,
                    return_parts=False,
                )
            except Exception as e:
                raise ValueError(f"Docling 分页解析失败: {str(e)}") from e

            parse_meta["total_pages"] = int(total_pages or 0)
            parse_meta["batch_size"] = int(batch_size)
            parse_meta["route"] = "pdf_docling_batched_markdown"
            parse_meta["ocr_reason"] = "docling_builtin"
            parse_meta["ocr_used"] = False

            merged_text = str(docling_markdown or "").strip()
            if not merged_text:
                raise ValueError("Docling 解析成功但未产出可用 markdown 内容")

            return merged_text, parse_meta

        try:
            converter = DocumentConverter()
            result = converter.convert(file_path)
            md_text = result.document.export_to_markdown()
            parse_meta["route"] = f"{safe_ext}_docling"
            return md_text, parse_meta
        except Exception as e:
            parse_meta["docling_error"] = str(e)[:300]
            if safe_ext in {"md", "markdown"}:
                with open(file_path, "r", encoding="utf-8") as f:
                    text = f.read()
                parse_meta["route"] = "markdown_plain_fallback"
                return text, parse_meta
            raise

    def _process_document_advanced(self, file_path: str, ext: str) -> dict:
        print(f"👁️ [解析路由] 启动文档解析: {file_path} (格式: {ext})")
        raw_text, parse_meta = self._route_and_extract_document(file_path, ext)

        md_text = self._clean_common_noise(raw_text)
        md_text = self._stitch_cross_page_lines(md_text)

        headers_to_split_on = [("#", "章"), ("##", "节"), ("###", "条"), ("####", "款")]
        md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on, strip_headers=False)
        try:
            md_splits = md_splitter.split_text(md_text)
        except Exception:
            md_splits = []

        split_units = []
        if md_splits:
            for split in md_splits:
                split_units.append((str(split.page_content or ""), dict(split.metadata or {})))
        else:
            split_units.append((md_text, {}))

        valid_texts: list[str] = []
        chunk_types: list[str] = []
        chunk_roles: list[str] = []
        dropped_garbled_chunks = 0

        for page_content, metadata in split_units:
            hierarchy = [metadata[h] for h in ["章", "节", "条", "款"] if h in metadata and str(metadata[h]).strip()]
            path_str = " > ".join(hierarchy) if hierarchy else "通用正文"
            if "目 次" in path_str:
                continue

            semantic_units = self._split_semantic_units(page_content, metadata)
            for content, _ in semantic_units:
                normalized = self._clean_common_noise(content)
                chunk_role = self._classify_chunk_role(normalized)
                if len(normalized) < 15 and not self._should_keep_short_chunk(normalized, chunk_role):
                    continue
                if self._is_garbled_chunk(normalized):
                    dropped_garbled_chunks += 1
                    continue

                clause_match = re.search(r"第[\u4e00-\u9fa5\d]+条", normalized)
                clause_num = clause_match.group() if clause_match else "明细条款"
                chunk_type = "table" if self._is_table_chunk(normalized) else "text"

                enriched_text = f"【章节】: {path_str} | 【{clause_num}】\n{normalized}"
                valid_texts.append(enriched_text)
                chunk_types.append(chunk_type)
                chunk_roles.append(chunk_role)

        quality_metrics = self._compute_parse_quality(valid_texts, chunk_types, md_text, chunk_roles)
        parse_meta = {
            **(parse_meta or {}),
            "pipeline": "modern_rag_parse_v2",
            "dropped_garbled_chunks": int(dropped_garbled_chunks),
            "chunk_type_counts": {
                "text": int(sum(1 for x in chunk_types if x == "text")),
                "table": int(sum(1 for x in chunk_types if x == "table")),
            },
            "chunk_role_counts": {
                "chapter_heading": int(sum(1 for x in chunk_roles if x == "chapter_heading")),
                "section_heading": int(sum(1 for x in chunk_roles if x == "section_heading")),
                "article_heading": int(sum(1 for x in chunk_roles if x == "article_heading")),
                "appendix_heading": int(sum(1 for x in chunk_roles if x == "appendix_heading")),
                "table_caption": int(sum(1 for x in chunk_roles if x == "table_caption")),
                "figure_caption": int(sum(1 for x in chunk_roles if x == "figure_caption")),
                "body": int(sum(1 for x in chunk_roles if x == "body")),
            },
        }

        min_quality = float(getattr(AIModelConstants, "PARSE_MIN_QUALITY_SCORE", 55))
        if str(parse_meta.get("ext", "")).lower() == "pdf":
            min_quality = max(
                min_quality,
                float(getattr(AIModelConstants, "PDF_PARSE_MIN_QUALITY_SCORE", min_quality)),
            )
        if (
            int(quality_metrics.get("total_chunks", 0) or 0) >= 5
            and float(quality_metrics.get("quality_score", 0.0) or 0.0) < min_quality
        ):
            raise ValueError(
                f"解析质量过低（score={quality_metrics.get('quality_score')} < {min_quality}），已拒绝入库以避免污染检索"
            )
        if (
            str(parse_meta.get("ext", "")).lower() == "pdf"
            and int(quality_metrics.get("total_chunks", 0) or 0) >= 5
            and float(quality_metrics.get("garbled_chunk_rate", 0.0) or 0.0) > 0.25
        ):
            raise ValueError(
                f"PDF 乱码块占比过高（garbled_rate={quality_metrics.get('garbled_chunk_rate')}），已拒绝入库"
            )

        print(f"✅[解析完成] route={parse_meta.get('route')} | chunks={len(valid_texts)}")
        return {
            "chunks": valid_texts,
            "quality_metrics": quality_metrics,
            "parse_meta": parse_meta,
        }

    def async_process_and_store(self, file_path: str, ext: str, doc_id: int):
        db = SessionLocal()
        parse_service = KnowledgeParseService(db)
        try:
            parse_service.mark_processing(
                doc_id=doc_id,
                parse_meta={"ext": ext, "pipeline": "modern_rag_parse_v2", "async": True},
            )

            parse_result = self._process_document_advanced(file_path, ext)
            valid_texts = list(parse_result.get("chunks", []) or [])
            quality_metrics = dict(parse_result.get("quality_metrics", {}) or {})
            parse_meta = dict(parse_result.get("parse_meta", {}) or {})
            if not valid_texts:
                logger.warning(f"文档 {doc_id} 未提取到有效文本")
                parse_service.mark_failed(doc_id=doc_id, error="未提取到有效文本", parse_meta=parse_meta)
                return

            if self.vector_db is None:
                self.vector_db = FAISS.from_texts(valid_texts, self.custom_embeddings)
            else:
                self.vector_db.add_texts(valid_texts)
            self.vector_db.save_local(self.index_path)

            self.bm25_corpus.extend(valid_texts)
            tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
            self.bm25_instance = BM25Okapi(tokenized_corpus)

            doc = db.query(KnowledgeDoc).filter_by(id=doc_id).first()
            if doc:
                doc.chunk_count = len(valid_texts)
                doc.parsed_content = valid_texts
                db.commit()
                parse_service.mark_ready(doc_id=doc_id, quality_metrics=quality_metrics, parse_meta=parse_meta)
                logger.info(f"🎉 异步任务完成！文档ID:{doc_id} 已成功存入 {len(valid_texts)} 个切片。")
        except Exception as e:
            logger.error(f"❌ 异步解析任务崩溃: {e}")
            parse_service.mark_failed(doc_id=doc_id, error=str(e), parse_meta={"ext": ext, "async": True})
            try:
                failed_doc = db.query(KnowledgeDoc).filter_by(id=doc_id).first()
                if failed_doc:
                    failed_doc.chunk_count = 0
                    failed_doc.parsed_content = []
                    db.commit()
            except Exception:
                db.rollback()
        finally:
            db.close()

    def ingest_knowledge(self, file_upload_object, filename: str) -> list:
        print(f"\n🚀 [知识入库] 接收文件: {filename}")
        # 使用常量替换硬编码路径
        upload_path = os.path.join(settings.BASE_DIR, SystemConfig.UPLOAD_DIR)
        os.makedirs(upload_path, exist_ok=True)
        file_save_path = os.path.join(upload_path, filename)

        with open(file_save_path, "wb") as buffer:
            shutil.copyfileobj(file_upload_object.file, buffer)

        try:
            ext = filename.split('.')[-1].lower()
            parse_result = self._process_document_advanced(file_save_path, ext)
            valid_texts = list(parse_result.get("chunks", []) or [])
        except Exception as e:
            logger.error(f"文档解析失败: {e}")
            raise Exception(f"文档解析失败: {str(e)}")

        if not valid_texts:
            raise Exception("文档解析后没有提取到有效语义文本")

        if self.vector_db is None:
            self.vector_db = FAISS.from_texts(valid_texts, self.custom_embeddings)
        else:
            self.vector_db.add_texts(valid_texts)
        self.vector_db.save_local(self.index_path)

        self.bm25_corpus.extend(valid_texts)
        tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
        self.bm25_instance = BM25Okapi(tokenized_corpus)

        return valid_texts

    async def chat_stream(self, query: str, session_id: str = "default"):
        print(f"\n{'=' * 10} [提问] {query} {'=' * 10}")

        if self.redis_client:
            try:
                self.redis_client.incr(RedisKeys.METRICS_TOTAL_QUERIES)
            except:
                pass

        query = self.strict_clean(query)
        q_vec = self.custom_embeddings.embed_query(query)

        # 1. 语义缓存检查 (开发调试期间可注释)
        cached_ans, cached_src = self.cache.get_semantic_cache(q_vec)
        if cached_ans:
            print("⚡ [缓存命中] 直接返回历史答案")
            if self.redis_client:
                try:
                    self.redis_client.incr(RedisKeys.METRICS_CACHE_HITS)
                except:
                    pass
            yield json.dumps({"type": "sources", "data": cached_src})
            yield json.dumps({"type": "content", "data": cached_ans})
            yield json.dumps({"type": "done", "full_answer": cached_ans})
            return

        if not self.vector_db:
            yield json.dumps({"type": "error", "data": "知识库为空"})
            return

        # 2. 获取历史记录并构建 Context
        # 使用常量替换历史记录键名
        history_key, summary_key = self._build_memory_keys(session_id)
        history_entries = load_history_entries(self.redis_client, history_key, RedisKeys.MAX_HISTORY_LENGTH)
        history_summary = load_history_summary(self.redis_client, summary_key)
        chat_history_objs = build_langchain_messages(history_entries)
        history_context_text = render_history_context(history_entries, history_summary)
        if history_summary:
            chat_history_objs = [SystemMessage(content=f"历史摘要：\n{history_summary}")] + chat_history_objs

        search_query = query
        if history_context_text:
            try:
                rewrite_res = self.rewriter_llm.invoke(
                    QUERY_REWRITE_PROMPT.format(history=history_context_text, query=query))
                search_query = rewrite_res.content.strip().replace('"', '')
                print(f"🔍 [改写后] {search_query}")
            except:
                pass

        # 3. 真・Agent 智能体
        try:
            print("🤖 [Agent] 正在思考...")
            agent_system_prompt = SystemMessage(content=AGENT_SYSTEM_PROMPT)
            tools = [agent_get_route, agent_search_nearby, agent_get_weather]
            llm_with_tools = self.rewriter_llm.bind_tools(tools)
            messages_for_agent = [agent_system_prompt] + chat_history_objs + [HumanMessage(content=search_query)]

            agent_msg = llm_with_tools.invoke(messages_for_agent)

            if agent_msg.tool_calls:
                print(f"🛠️ [Agent] 命中工具: {[t['name'] for t in agent_msg.tool_calls]}")
                messages_for_agent.append(agent_msg)

                for tool_call in agent_msg.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]

                    status_text = ""
                    if "route" in tool_name:
                        status_text = "🔄 **正在规划出行方案...**\n\n"
                    elif "nearby" in tool_name:
                        status_text = "🔄 **正在搜索周边设施...**\n\n"
                    elif "weather" in tool_name:
                        status_text = "🔄 **正在查询实时天气...**\n\n"

                    yield json.dumps({"type": "content", "data": status_text})

                    selected_tool = next(t for t in tools if t.name == tool_name)
                    try:
                        tool_result_raw = await selected_tool.ainvoke(tool_args)
                        tool_res_str = str(tool_result_raw)

                        try:
                            res_dict = json.loads(tool_res_str)
                            if "html_widget" in res_dict and "text_data" in res_dict:
                                yield json.dumps({"type": "content", "data": res_dict["html_widget"] + "\n\n"},
                                                 ensure_ascii=False)
                                messages_for_agent.append(
                                    ToolMessage(content=res_dict["text_data"], tool_call_id=tool_call["id"]))
                                continue
                        except json.JSONDecodeError:
                            pass

                        messages_for_agent.append(ToolMessage(content=tool_res_str, tool_call_id=tool_call["id"]))

                    except Exception as tool_err:
                        messages_for_agent.append(
                            ToolMessage(content=f"工具调用失败: {str(tool_err)}", tool_call_id=tool_call["id"]))

                print("🤖 [Agent] 生成综合建议...")
                full_answer = ""
                async for chunk in self.llm.astream(messages_for_agent):
                    content = chunk.content
                    if content:
                        clean_content = re.sub(r'<\|?DSML\|?.*?>', '', content, flags=re.IGNORECASE)
                        clean_content = re.sub(r'</\|?DSML\|?.*?>', '', clean_content, flags=re.IGNORECASE)
                        if not clean_content.strip(): continue
                        full_answer += clean_content
                        yield json.dumps({"type": "content", "data": clean_content}, ensure_ascii=False)

                if self.redis_client:
                    self._persist_history_with_summary(
                        history_key,
                        summary_key,
                        history_entries,
                        query,
                        full_answer,
                    )

                yield json.dumps({"type": "done", "full_answer": full_answer})
                return
            else:
                print("🧠 [Agent] 未命中工具，转入法规库检索...")
        except Exception as e:
            print(f"⚠️ Agent 调度异常: {e}")

        # 4. 混合检索
        print("📡 [混合检索] 执行中...")
        retrieval_started = time.perf_counter()
        hybrid_result = await self.retrieve_hybrid_docs(search_query, mode="fast")
        retrieval_latency_ms = int((time.perf_counter() - retrieval_started) * 1000)
        faiss_docs = hybrid_result.get("faiss_docs", [])
        bm25_docs = hybrid_result.get("bm25_docs", [])
        candidate_list = hybrid_result.get("fused_docs", [])
        final_docs = hybrid_result.get("final_docs", [])
        threshold_used = float(hybrid_result.get("threshold_used", AIModelConstants.DEFAULT_SCORE_THRESHOLD))
        top_score = float(hybrid_result.get("top_score", 0.0))
        rerank_fallback = bool(hybrid_result.get("rerank_fallback", False))

        print(
            f"[普通检索指标] query='{search_query[:30]}' | "
            f"faiss={len(faiss_docs)} | bm25={len(bm25_docs)} | "
            f"fusion={len(candidate_list)} | final={len(final_docs)} | "
            f"threshold={threshold_used:.4f} | top_rerank={top_score:.4f} | "
            f"rerank_fallback={rerank_fallback} | latency_ms={retrieval_latency_ms}"
        )
        self.retrieval_metrics.record(
            query=search_query,
            mode="fast",
            source="chat_fast_retrieval",
            faiss_count=len(faiss_docs),
            bm25_count=len(bm25_docs),
            fusion_count=len(candidate_list),
            final_count=len(final_docs),
            threshold_used=threshold_used,
            top_score=top_score,
            rerank_fallback=rerank_fallback,
            latency_ms=retrieval_latency_ms,
            session_id=session_id,
            user_id=self.current_user_id,
            run_id="",
        )

        if not final_docs:
            print("❌ [防幻觉] 所有片段得分均低于阈值，判定为知识库无相关内容。")
            fallback_msg = "抱歉，根据目前的知识库，未找到与您描述完全匹配的交通法规条款。建议您提供更详细的关键词，或咨询当地交管部门。"
            yield json.dumps({"type": "content", "data": fallback_msg})
            yield json.dumps({"type": "done", "full_answer": fallback_msg})
            return

        yield json.dumps({"type": "sources", "data": final_docs})

        # 7. 知识图谱增强
        print("🕸️ [知识图谱] 正在提取逻辑链...")
        try:
            from app.services.graph_service import GraphService
            graph_data = GraphService(self.llm).get_full_graph(self.db)
            relevant_triples = [
                f"{link['source']} -{link['value']}-> {link['target']}"
                for link in graph_data['links']
                if any(k in link['source'] or k in link['target'] for k in jieba.cut(search_query))
            ]
            graph_context = "\n".join(relevant_triples) if relevant_triples else "暂无图谱逻辑关联"
        except Exception as e:
            print(f"知识图谱提取异常: {e}")
            graph_context = "暂无图谱逻辑关联"

        # 8. RAG 回答生成
        context = "\n".join([f"[资料{i + 1}]: {d}" for i, d in enumerate(final_docs)])
        final_prompt = RAG_SYSTEM_PROMPT.format(
            context=context, graph_context=graph_context, history=history_context_text, query=query
        )

        full_answer = ""
        try:
            print("🤖 [AI生成] 开始流式输出...")
            async for chunk in self.llm.astream(final_prompt):
                if chunk.content:
                    full_answer += chunk.content
                    yield json.dumps({"type": "content", "data": chunk.content}, ensure_ascii=False)

            self.cache.set_semantic_cache(q_vec, full_answer, final_docs)
            if self.redis_client:
                self._persist_history_with_summary(
                    history_key,
                    summary_key,
                    history_entries,
                    query,
                    full_answer,
                )

            yield json.dumps({"type": "done", "full_answer": full_answer})
        except Exception as e:
            print(f"❌ 生成异常: {e}")
            yield json.dumps({"type": "error", "data": str(e)})

