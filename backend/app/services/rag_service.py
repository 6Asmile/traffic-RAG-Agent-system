import sys
import io
import json
import logging
import os
import re
import shutil
import tempfile
import time
from typing import List, Optional, Tuple

import httpx
import jieba
from langchain_community.vectorstores import FAISS
from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_openai import ChatOpenAI
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
from pypdf import PdfReader, PdfWriter
from rank_bm25 import BM25Okapi
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.prompts import RAG_SYSTEM_PROMPT, QUERY_REWRITE_PROMPT, AGENT_SYSTEM_PROMPT
from app.db.session import SessionLocal
from app.models import User
from app.models.knowledge import KnowledgeDoc
from app.services.cache_service import CacheManager
from app.services.config_service import ConfigService
from app.services.tool_service import agent_get_route, agent_search_nearby, agent_get_weather

# 设置标准输出编码为 UTF-8
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8")
# 禁用 Mem0 遥测
os.environ["ANONYMIZED_TELEMETRY"] = "False"

logger = logging.getLogger("RAGService")


# =============================================================================
# 嵌入模型包装器（阿里云兼容接口）
# =============================================================================
class AliyunEmbeddingWrapper(Embeddings):
    """
    封装阿里云 DashScope Embedding 调用，符合 LangChain Embeddings 接口。
    """

    def __init__(self, model: str, api_key: str, base_url: str):
        self.model = model
        self.api_key = api_key
        # 拼接完整的 embedding 接口地址
        self.url = base_url.replace("/compatible-mode/v1", "/compatible-mode/v1/embeddings")

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        """
        批量获取文本向量，每次发送 10 条，带重试机制。
        """
        results = []
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)

        with httpx.Client(timeout=120.0, limits=limits) as client:
            # 分批处理
            for i in range(0, len(texts), 10):
                batch = [str(t) for t in texts[i : i + 10]]
                payload = {"model": self.model, "input": batch}
                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                }

                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        resp = client.post(self.url, json=payload, headers=headers)
                        if resp.status_code != 200:
                            raise Exception(
                                f"Embedding API Error: {resp.status_code} - {resp.text}"
                            )
                        data = resp.json()
                        results.extend([item["embedding"] for item in data["data"]])
                        break
                    except Exception as e:
                        logger.warning(
                            f"⚠️ 阿里云 Embedding 请求断开 (尝试 {attempt + 1}/{max_retries}): {e}"
                        )
                        if attempt == max_retries - 1:
                            raise Exception(f"Embedding 最终失败: {e}")
                        time.sleep(2)
                time.sleep(0.5)  # 批次间等待，避免限流

        return results

    def embed_query(self, text: str) -> List[float]:
        """单条查询向量获取"""
        return self.embed_documents([text])[0]


# =============================================================================
# 重排序模型（阿里云 DashScope Rerank）
# =============================================================================
class AliyunReranker:
    """
    调用阿里云兼容的 Rerank API，对搜索结果进行重排序。
    """

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.url = "https://dashscope.aliyuncs.com/compatible-api/v1/reranks"

    def rerank(self, query: str, documents: List[str], top_n: int = 10) -> List[dict]:
        """
        对候选文档重排序，返回相关性分数及排序结果。
        """
        payload = {
            "model": "qwen3-rerank",
            "query": query,
            "documents": documents,
            "top_n": top_n,
            "instruct": "Given a web search query, retrieve relevant passages that answer the query.",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

        try:
            with httpx.Client(timeout=10.0) as client:
                resp = client.post(self.url, json=payload, headers=headers)

            if resp.status_code == 200:
                data = resp.json()
                if "results" in data:
                    return data["results"]
                logger.warning(f"Rerank 响应格式异常: {list(data.keys())}")
                return []
            else:
                logger.error(f"Rerank API Error [{resp.status_code}]: {resp.text}")
                return []
        except Exception as e:
            logger.error(f"Rerank Exception: {e}")
            return []


# =============================================================================
# 文本辅助工具
# =============================================================================
class TextHelper:
    """
    提供文本清洗、历史消息解析、数字证据检测等静态方法。
    """

    @staticmethod
    def strict_clean(text: str) -> str:
        """深度清洗文本：去除控制字符、压缩空白、修复数字与字母间的空格等。"""
        if not text:
            return ""
        text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'(?<=\d)\s+(?=[a-zA-Z])', '', text)
        text = re.sub(r'(?<=[a-zA-Z])\s+(?=/)', '', text)
        text = re.sub(r'(?<=/)\s+(?=[a-zA-Z])', '', text)
        return text.strip()

    @staticmethod
    def strip_role_prefix(msg: str) -> str:
        """去除消息开头的角色标记（如 "用户: ", "assistant: "）"""
        if not isinstance(msg, str):
            msg = str(msg)
        return re.sub(
            r"^\s*(?:user|assistant|human|ai|用户|助手)\s*[:：]\s*",
            "",
            msg,
            flags=re.IGNORECASE,
        ).strip()

    @staticmethod
    def parse_history_turns(raw_messages: List[str]) -> List[Tuple[str, str]]:
        """
        将原始历史消息列表解析为 (角色, 内容) 交替对话轮次。
        默认奇数为人类，偶数为 AI；若存在显式角色前缀则据此判定。
        """
        turns: List[Tuple[str, str]] = []
        for i, raw_msg in enumerate(raw_messages):
            msg = str(raw_msg) if raw_msg is not None else ""
            role = "human" if i % 2 == 0 else "ai"  # 默认第一条是人类
            explicit = re.match(
                r"^\s*(user|assistant|human|ai|用户|助手)\s*[:：]",
                msg,
                flags=re.IGNORECASE,
            )
            if explicit:
                role_name = explicit.group(1).lower()
                role = "ai" if role_name in ("assistant", "ai", "助手") else "human"
            content = TextHelper.strip_role_prefix(msg)
            if content:
                turns.append((role, content))
        return turns

    @staticmethod
    def history_turns_to_text(turns: List[Tuple[str, str]]) -> str:
        """将对话轮次转为可读的文本格式。"""
        return "\n".join(
            f"{'用户' if role == 'human' else '助手'}: {content}"
            for role, content in turns
        )

    @staticmethod
    def message_content_to_text(message_obj) -> str:
        """从 LangChain 消息对象中提取纯文本内容。"""
        content = getattr(message_obj, "content", message_obj)
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            texts: List[str] = []
            for part in content:
                if isinstance(part, dict) and part.get("type") == "text":
                    texts.append(str(part.get("text", "")))
                else:
                    texts.append(str(part))
            return "".join(texts).strip()
        return str(content or "").strip()

    @staticmethod
    def has_strong_numeric_evidence(query: str, docs: List[str]) -> bool:
        """
        判断查询是否具有强数字型答案需求，且检索文档中包含表格和数字。
        """
        if not docs:
            return False
        query_has_numeric_intent = bool(
            re.search(r"(多少|最小|最大|数值|值为|限值|标准|样本量|限速|罚款|扣分)", query)
        )
        docs_blob = "\n".join(docs[:5])
        has_digits = bool(re.search(r"\d", docs_blob))
        has_table = (
            "【表格原文】" in docs_blob
            or "【表格结构化信息】" in docs_blob
            or docs_blob.count("|") >= 4
        )
        return query_has_numeric_intent and has_digits and has_table

    @staticmethod
    def build_evidence_guard(query: str, docs: List[str]) -> str:
        """
        当存在强数字证据时，生成一条追加提示，强制模型给出确切数值并引用来源。
        """
        if not TextHelper.has_strong_numeric_evidence(query, docs):
            return ""
        return (
            "\n[Consistency Guard]\n"
            "- If reference materials include relevant table or numeric mappings, you MUST answer with the exact value and unit.\n"
            "- You MUST cite source tags like [资料1].\n"
            "- You MUST NOT output '未找到' style refusal when a matching value exists in references.\n"
            "- Put the conclusion in the first sentence."
        )

    @staticmethod
    def looks_like_refusal(text: str) -> bool:
        """检查文本是否包含典型的拒绝回答用语。"""
        if not text:
            return False
        return bool(re.search(r"(未找到|抱歉|无明确规定|没有相关依据)", text))


# =============================================================================
# 表格解析器
# =============================================================================
class TableParser:
    """
    识别并提取 Markdown/ASCII 表格，支持分块和结构化转换。
    """

    @staticmethod
    def is_markdown_table_separator(line: str) -> bool:
        """判断是否为 Markdown 表格分隔行（如 |---|:---|---|）"""
        cells = [cell.strip() for cell in (line or "").strip().strip("|").split("|") if cell.strip()]
        return bool(cells) and "|" in (line or "") and all(
            re.fullmatch(r":?-{3,}:?", cell) for cell in cells
        )

    @staticmethod
    def is_ascii_grid_border(line: str) -> bool:
        """判断是否为 ASCII 网格表格边框（如 +---+---+）"""
        stripped = line.strip()
        return bool(stripped) and bool(re.fullmatch(r"\+-[-+=:]+(?:\+-[-+=:]+)*\+?", stripped))

    @staticmethod
    def is_pipe_like_line(line: str) -> bool:
        """判断是否含有管道符，可能是表格行"""
        stripped = line.strip()
        return bool(stripped) and (
            stripped.startswith("|") or stripped.endswith("|") or stripped.count("|") >= 2
        )

    @staticmethod
    def split_space_columns(line: str) -> List[str]:
        """按连续空白（至少两个空格或制表符）拆分表格列"""
        stripped = line.strip()
        if not stripped:
            return []
        return [cell.strip() for cell in re.split(r"\s{2,}|\t+", stripped) if cell.strip()]

    def is_whitespace_table_line(self, line: str) -> bool:
        """判断是否为空白分隔的表格数据行（至少3列，且包含数字）"""
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            return False
        cols = self.split_space_columns(stripped)
        return (
            len(cols) >= 3
            and any(re.search(r"\d", c) for c in cols)
            and not (len(cols) == 3 and all(len(c) > 12 for c in cols))
        )

    @staticmethod
    def looks_like_table_title(line: str) -> bool:
        """判断是否为表格标题（如 "表1 限速对照"）"""
        stripped = line.strip()
        return bool(stripped) and (
            re.search(r"(?:^|[\s（(])(?:表|附表|Table)\s*[A-Za-z]?\s*[\d一二三四五六七八九十\.]+", stripped)
            or (len(stripped) <= 80 and ("表" in stripped or "Table" in stripped))
        )

    @staticmethod
    def looks_like_table_note(line: str) -> bool:
        """判断是否为表格注释（如 "注：..."）"""
        stripped = line.strip()
        if not stripped:
            return False
        return bool(re.match(r"(?i)^(?:注|备注|说明|note)\s*(?:[:：\.、]|$)", stripped))

    @staticmethod
    def parse_markdown_row(line: str) -> List[str]:
        """解析单个 Markdown 表格行，返回单元格列表"""
        return [cell.strip() for cell in line.strip().strip("|").split("|")] if "|" in line else []

    def detect_table_mode(self, lines: List[str], idx: int) -> Optional[str]:
        """
        检测当前行开始的表格类型：
        - "md"：Markdown 分隔行格式
        - "grid"：ASCII 网格
        - "pipe"：纯管道分隔（无显式分隔行）
        - "space"：空白对齐
        """
        line = lines[idx]
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        next2_line = lines[idx + 2] if idx + 2 < len(lines) else ""

        if self.is_pipe_like_line(line) and self.is_markdown_table_separator(next_line):
            return "md"
        if self.is_ascii_grid_border(line) and self.is_pipe_like_line(next_line):
            return "grid"
        if self.is_pipe_like_line(line) and (
            self.is_pipe_like_line(next_line)
            or (not next_line.strip() and self.is_pipe_like_line(next2_line))
        ):
            return "pipe"
        if self.is_whitespace_table_line(line) and (
            self.is_whitespace_table_line(next_line)
            or (not next_line.strip() and self.is_whitespace_table_line(next2_line))
        ):
            return "space"
        return None

    def extract_markdown_blocks(self, text: str) -> List[dict]:
        """
        将文本分割为普通文本块和表格块，表格块会尽量吸收其标题和注释。
        """
        lines = text.splitlines()
        blocks: List[dict] = []
        text_buffer: List[str] = []   # 暂存连续的普通文本
        i = 0

        while i < len(lines):
            line = lines[i]
            mode = self.detect_table_mode(lines, i)

            if mode:
                # 检查前方是否有表格标题，将其纳入表格块
                table_prefix: List[str] = []
                non_empty_taken = 0
                while text_buffer:
                    candidate = text_buffer[-1]
                    if not candidate.strip():  # 空行
                        table_prefix.insert(0, text_buffer.pop())
                        continue
                    if self.looks_like_table_title(candidate):
                        table_prefix.insert(0, text_buffer.pop())
                        non_empty_taken += 1
                        if non_empty_taken >= 2:
                            break
                        continue
                    if non_empty_taken == 0 and len(candidate.strip()) <= 80 and (
                        "见表" in candidate or "如下表" in candidate or "表" in candidate
                    ):
                        table_prefix.insert(0, text_buffer.pop())
                        non_empty_taken += 1
                        break
                    if non_empty_taken == 0:
                        c = candidate.strip()
                        if c and len(c) <= 60 and not re.search(r"[。！？；;]$", c):
                            table_prefix.insert(0, text_buffer.pop())
                            non_empty_taken += 1
                            break
                    break

                # 保存之前缓存的纯文本
                text_part = "\n".join(text_buffer).strip()
                if text_part:
                    blocks.append({"type": "text", "content": text_part})
                text_buffer = []

                # 收集表格行（当前行及后续行）
                table_lines = table_prefix + [line]
                i += 1
                while i < len(lines):
                    current = lines[i]
                    if mode == "space" and self.is_whitespace_table_line(current):
                        table_lines.append(current)
                        i += 1
                        continue
                    if mode == "space" and not current.strip():
                        next_after_blank = lines[i + 1] if i + 1 < len(lines) else ""
                        if self.is_whitespace_table_line(next_after_blank):
                            table_lines.append(current)
                            i += 1
                            continue
                    if self.is_pipe_like_line(current) or self.is_ascii_grid_border(current):
                        table_lines.append(current)
                        i += 1
                        continue
                    if mode == "pipe" and not current.strip():
                        next_after_blank = lines[i + 1] if i + 1 < len(lines) else ""
                        if self.is_pipe_like_line(next_after_blank):
                            table_lines.append(current)
                            i += 1
                            continue
                    break

                # 处理表格后的注释行
                while i < len(lines):
                    current = lines[i]
                    if self.looks_like_table_note(current):
                        table_lines.append(current)
                        i += 1
                        while i < len(lines):
                            cont = lines[i]
                            cont_s = cont.strip()
                            if not cont_s:
                                table_lines.append(cont)
                                i += 1
                                break
                            if cont_s.startswith("#"):
                                break
                            if self.detect_table_mode(lines, i):
                                break
                            table_lines.append(cont)
                            i += 1
                        continue
                    break

                table_part = "\n".join(table_lines).strip()
                if table_part:
                    blocks.append({"type": "table", "content": table_part})
                continue

            text_buffer.append(line)
            i += 1

        # 处理末尾文本
        text_part = "\n".join(text_buffer).strip()
        if text_part:
            blocks.append({"type": "text", "content": text_part})

        return blocks

    def split_markdown_table(
        self, table_markdown: str, max_chars: int = 1200, row_overlap: int = 1
    ) -> List[str]:
        """
        将大型 Markdown/ASCII 表格按最大字符数拆分成多个块，保留表头和重叠行。
        """
        lines = [line.rstrip() for line in table_markdown.splitlines() if line.strip()]
        if len(lines) < 2:
            stripped = table_markdown.strip()
            return [stripped] if stripped else []

        # 分离前置/后置上下文
        leading_context: List[str] = []
        trailing_context: List[str] = []
        core = lines[:]
        while core and not (self.is_pipe_like_line(core[0]) or self.is_ascii_grid_border(core[0])):
            leading_context.append(core.pop(0))
        while core and not (self.is_pipe_like_line(core[-1]) or self.is_ascii_grid_border(core[-1])):
            trailing_context.insert(0, core.pop())

        if not core:
            core = lines[:]
            leading_context = []
            trailing_context = []

        # 标准 Markdown 表格（有表头+分隔行）
        if len(core) >= 3 and self.is_pipe_like_line(core[0]) and self.is_markdown_table_separator(core[1]):
            header, separator = core[0], core[1]
            data_rows = core[2:]
            base_len = len(header) + len(separator) + 2 + sum(len(x) + 1 for x in leading_context)
            chunks: List[str] = []
            current_rows: List[str] = []
            current_len = base_len

            for row in data_rows:
                row_len = len(row) + 1
                if current_rows and current_len + row_len > max_chars:
                    chunk_lines = leading_context + [header, separator] + current_rows
                    chunks.append("\n".join(chunk_lines))
                    # 保留末尾 overlap 行作为下一块的上下文
                    overlap_rows = current_rows[-row_overlap:] if row_overlap > 0 else []
                    current_rows = overlap_rows.copy()
                    current_len = base_len + sum(len(r) + 1 for r in current_rows)
                current_rows.append(row)
                current_len += row_len

            if current_rows:
                chunk_lines = leading_context + [header, separator] + current_rows + trailing_context
                chunks.append("\n".join(chunk_lines))
            return chunks

        # 非标准表格（如管道符、空格对齐、网格线）
        prefix_rows = leading_context[:]
        data_rows = core[:]

        # 尝试将第一行识别为表头（若数字含量低）
        if len(data_rows) >= 2 and self.is_pipe_like_line(data_rows[0]):
            first_cells = [c for c in self.parse_markdown_row(data_rows[0]) if c]
            numeric_like = sum(1 for c in first_cells if re.search(r"\d", c))
            if first_cells and numeric_like <= max(1, len(first_cells) // 2):
                prefix_rows.append(data_rows[0])
                data_rows = data_rows[1:]
        elif (
            len(data_rows) >= 3
            and self.is_ascii_grid_border(data_rows[0])
            and self.is_pipe_like_line(data_rows[1])
            and self.is_ascii_grid_border(data_rows[2])
        ):
            prefix_rows.extend(data_rows[:3])
            data_rows = [r for r in data_rows[3:] if not self.is_ascii_grid_border(r)]
        elif len(data_rows) >= 2 and self.is_whitespace_table_line(data_rows[0]):
            first_cells = self.split_space_columns(data_rows[0])
            numeric_like = sum(1 for c in first_cells if re.search(r"\d", c))
            if first_cells and numeric_like <= max(1, len(first_cells) // 2):
                prefix_rows.append(data_rows[0])
                data_rows = data_rows[1:]

        if not data_rows:
            data_rows = core[:]

        prefix_len = sum(len(x) + 1 for x in prefix_rows)
        chunks: List[str] = []
        current_rows: List[str] = []
        current_len = prefix_len

        for row in data_rows:
            row_len = len(row) + 1
            if current_rows and current_len + row_len > max_chars:
                chunk_lines = prefix_rows + current_rows
                chunks.append("\n".join(chunk_lines))
                overlap_rows = current_rows[-row_overlap:] if row_overlap > 0 else []
                current_rows = overlap_rows.copy()
                current_len = prefix_len + sum(len(r) + 1 for r in current_rows)
            current_rows.append(row)
            current_len += row_len

        if current_rows:
            chunk_lines = prefix_rows + current_rows + trailing_context
            chunks.append("\n".join(chunk_lines))

        return chunks

    def table_to_structured_text(self, table_markdown: str, max_rows: int = 40) -> str:
        """
        将表格前 max_rows 行转换为 "第N行: 列名=值" 的结构化文本。
        """
        rows: List[List[str]] = []
        for line in table_markdown.splitlines():
            if "|" in line:
                cells = self.parse_markdown_row(line)
            elif self.is_whitespace_table_line(line):
                cells = self.split_space_columns(line)
            else:
                continue
            if not cells:
                continue
            # 跳过仅分隔符的行
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
                continue
            rows.append(cells)

        if len(rows) < 2:
            return ""

        headers = rows[0]
        structured_lines: List[str] = []
        for idx, row in enumerate(rows[1 : max_rows + 1], start=1):
            width = min(len(headers), len(row))
            pairs = []
            for col_idx in range(width):
                key = headers[col_idx].strip()
                value = row[col_idx].strip()
                if not key or not value:
                    continue
                pairs.append(f"{key}={value}")
            if pairs:
                structured_lines.append(f"第{idx}行: " + "；".join(pairs))

        return "\n".join(structured_lines)


# =============================================================================
# 文档处理器
# =============================================================================
class DocumentProcessor:
    """
    负责文档解析、分块、构建向量索引及 BM25 索引。
    """

    def __init__(self, db: Session, table_parser: TableParser, embeddings, index_path: str):
        self.db = db
        self.table_parser = table_parser
        self.embeddings = embeddings
        self.index_path = index_path
        self.bm25_instance = None          # BM25 模型实例
        self.bm25_corpus: List[str] = []   # 用于 BM25 检索的文本语料
        self._init_bm25()                  # 初始化时加载数据库中已有文本

    def split_markdown_with_table_awareness(self, md_text: str) -> List[Document]:
        """
        感知表格的 Markdown 分块：
        1. 按标题层级粗分
        2. 对每个块进一步拆分为文本/表格
        3. 表格单独分块，文本继续递归分块
        """
        headers_to_split_on = [("#", "章"), ("##", "节"), ("###", "条"), ("####", "款")]
        md_splitter = MarkdownHeaderTextSplitter(
            headers_to_split_on=headers_to_split_on, strip_headers=False
        )
        md_splits = md_splitter.split_text(md_text)  # 按标题层次粗分

        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)
        final_splits: List[Document] = []

        for section in md_splits:
            metadata = dict(section.metadata or {})
            blocks = self.table_parser.extract_markdown_blocks(section.page_content or "")
            for block in blocks:
                block_type = block.get("type", "text")
                content = (block.get("content") or "").strip()
                if not content:
                    continue
                if block_type == "table":
                    # 表格按大小拆分
                    table_chunks = self.table_parser.split_markdown_table(
                        content, max_chars=1200, row_overlap=1
                    )
                    for table_chunk in table_chunks:
                        final_splits.append(
                            Document(
                                page_content=table_chunk,
                                metadata={**metadata, "block_type": "table"},
                            )
                        )
                else:
                    for piece in text_splitter.split_text(content):
                        piece = piece.strip()
                        if piece:
                            final_splits.append(
                                Document(
                                    page_content=piece,
                                    metadata={**metadata, "block_type": "text"},
                                )
                            )

        return final_splits

    def _init_bm25(self):
        """从数据库加载已有文本并构建 BM25 索引。"""
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
                logger.info(f"⚡ BM25 极速构建完成，共挂载 {len(all_texts)} 条高质量语义片段")
            else:
                logger.warning("BM25 初始化为空，知识库没有可用文本")
        except Exception as e:
            logger.error(f"BM25 初始化失败: {e}")

    def update_bm25(self, new_texts: List[str]):
        """增量更新 BM25 索引。"""
        self.bm25_corpus.extend(new_texts)
        tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
        self.bm25_instance = BM25Okapi(tokenized_corpus)

    def process_document_advanced(self, file_path: str, ext: str) -> list:
        """
        解析单个文件（PDF/TXT/其他），返回带章节和表格语义的文本块列表。
        """
        print(f"👁️ [解析路由] 启动文档解析: {file_path} (格式: {ext})")
        md_text = ""

        if ext == "pdf":
            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            reader = PdfReader(file_path)
            total_pages = len(reader.pages)
            batch_size = 10  # 每批处理 10 页
            print(f"📄 PDF 共 {total_pages} 页，将分 {(total_pages // batch_size) + 1} 批进行解析...")

            for i in range(0, total_pages, batch_size):
                writer = PdfWriter()
                for j in range(i, min(i + batch_size, total_pages)):
                    writer.add_page(reader.pages[j])

                with tempfile.NamedTemporaryFile(delete=False, suffix=".pdf") as tmp:
                    writer.write(tmp.name)
                    tmp_path = tmp.name

                try:
                    result = converter.convert(tmp_path)
                    md_text += result.document.export_to_markdown() + "\n\n"
                    print(f"   ⏳ 进度：已解析 {min(i + batch_size, total_pages)} / {total_pages} 页")
                finally:
                    if os.path.exists(tmp_path):
                        os.remove(tmp_path)

        elif ext == "txt":
            print("📄 检测到 TXT 文件，采用极速纯文本读取...")
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    md_text = f.read()
            except UnicodeDecodeError:
                with open(file_path, "r", encoding="gbk") as f:
                    md_text = f.read()

        else:
            from docling.document_converter import DocumentConverter

            converter = DocumentConverter()
            result = converter.convert(file_path)
            md_text = result.document.export_to_markdown()

        # 分块并清洗
        final_splits = self.split_markdown_with_table_awareness(md_text)
        valid_texts = []

        for split in final_splits:
            hierarchy = [split.metadata[h] for h in ["章", "节", "条", "款"] if h in split.metadata]
            path_str = " > ".join(hierarchy) if hierarchy else "通用正文"
            block_type = split.metadata.get("block_type", "text")
            content = split.page_content.strip()

            # 跳过目录和无意义内容
            if "目 次" in path_str or "........" in content:
                continue
            content = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', content)
            if len(content) < 15:
                continue

            # 丰富表格块内容
            if block_type == "table":
                structured_table = self.table_parser.table_to_structured_text(content)
                if structured_table:
                    content = f"【表格原文】\n{content}\n\n【表格结构化信息】\n{structured_table}"
                else:
                    content = f"【表格原文】\n{content}"
                clause_num = "表格条目"
            else:
                clause_match = re.search(r'第[\u4e00-\u9fa5\d]+条', content)
                clause_num = clause_match.group() if clause_match else "明细条款"

            enriched_text = f"【章节】: {path_str} | 【{clause_num}】\n{content}"
            valid_texts.append(enriched_text)

        print(f"✅[解析完成] 共提取 {len(valid_texts)} 个语义富化块。")
        return valid_texts

    def store_to_vector_db(self, texts: List[str], vector_db) -> object:
        """
        将文本存入 FAISS 向量库，若向量库不存在则新建。
        """
        if vector_db is None:
            vector_db = FAISS.from_texts(texts, self.embeddings)
        else:
            vector_db.add_texts(texts)
        vector_db.save_local(self.index_path)
        return vector_db


# =============================================================================
# 混合检索器
# =============================================================================
class Retriever:
    """
    使用 FAISS + BM25 混合召回，再通过 Reranker 精排。
    """

    def __init__(self, reranker: AliyunReranker, doc_processor: DocumentProcessor):
        self.reranker = reranker
        self.doc_processor = doc_processor

    def hybrid_search(self, query: str, vector_db, faiss_k: int = 40, bm25_k: int = 20) -> List[str]:
        """
        混合向量检索与 BM25 关键词检索，返回去重后的候选列表。
        """
        # 语义检索
        faiss_docs = vector_db.similarity_search(query, k=faiss_k)

        # BM25 检索
        bm25_docs = []
        if self.doc_processor.bm25_instance:
            tokenized_query = list(jieba.cut(query))
            scores = self.doc_processor.bm25_instance.get_scores(tokenized_query)
            top_n = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:bm25_k]
            bm25_docs = [
                Document(page_content=self.doc_processor.bm25_corpus[i])
                for i in top_n
                if scores[i] > 0
            ]

        # 去重
        candidates = {}
        for d in faiss_docs + bm25_docs:
            if d.page_content not in candidates:
                candidates[d.page_content] = d.page_content

        candidate_list = list(candidates.values())
        print(f"🔍[检索] FAISS 召回 {len(faiss_docs)} 条，BM25 召回 {len(bm25_docs)} 条，去重后 {len(candidate_list)} 条")
        print(f"🎯[Rerank] 正在对 {len(candidate_list)} 个片段进行精排...")
        return candidate_list

    def rerank(
        self, query: str, candidates: List[str], top_n: int = 10, threshold: float = 0.05
    ) -> Tuple[List[str], List[Tuple[float, str]]]:
        """
        调用 Reranker 精排，过滤低相关分数；失败时降级为朴素截取。
        """
        final_docs = []
        rerank_scored_docs: List[Tuple[float, str]] = []

        try:
            rerank_results = self.reranker.rerank(query, candidates, top_n=top_n)
            for res in rerank_results:
                score = res.get("relevance_score", -100)
                idx = res.get("index")
                if idx is None or idx < 0 or idx >= len(candidates):
                    continue
                rerank_scored_docs.append((score, candidates[idx]))
                if score < threshold:
                    continue
                final_docs.append(candidates[idx])
        except Exception as e:
            print(f"Rerank 异常 (降级为普通截取): {e}")
            final_docs = candidates[:5]

        # 如果精排后结果为空，取分数最高的几个兜底
        if not final_docs and rerank_scored_docs:
            rerank_scored_docs.sort(key=lambda x: x[0], reverse=True)
            for _, doc in rerank_scored_docs[:3]:
                if doc not in final_docs:
                    final_docs.append(doc)

        print(f"🎯[Rerank] 精排完成，{len(final_docs)} 条通过阈值（阈值={threshold}）")
        return final_docs, rerank_scored_docs


# =============================================================================
# RAG 核心服务
# =============================================================================
LLM_BASE_URL_MAP = {
    "deepseek": "https://api.deepseek.com",
    "mimo": "https://token-plan-cn.xiaomimimo.com/v1",
}

class RAGService:
    """核心检索增强生成服务，整合文档解析、检索、Agent 工具和流式对话。"""

    HISTORY_TTL_SECONDS = 3600
    MAX_HISTORY_TURNS = 8
    MAX_HISTORY_LINES = MAX_HISTORY_TURNS * 2
    EMPTY_KNOWLEDGE_MSG = "知识库为空，请先上传并解析知识文档。"
    FALLBACK_MSG = "抱歉，当前知识库里没有检索到足够相关的交通法规内容。可以换个更具体的关键词再试一次。"
    TOOL_STATUS_MESSAGES = {
        "route": "正在规划出行方案...\n\n",
        "nearby": "正在搜索周边设施...\n\n",
        "weather": "正在查询实时天气...\n\n",
    }

    def __init__(self, db: Session, current_user: User = None):
        import redis

        self.db = db

        # 获取用户配置或系统配置
        emb_cfg = ConfigService.get_active_config(db, "embedding")
        llm_cfg = ConfigService.get_active_config(db, "llm")
        if not emb_cfg or not llm_cfg:
            raise Exception("AI 配置缺失")

        user_prefs = current_user.ai_preferences if (current_user and current_user.ai_preferences) else {}
        llm_model = user_prefs.get("llm_model") or llm_cfg.model_name

        # Redis 缓存（可选）
        try:
            self.redis_client = redis.Redis(
                host=settings.REDIS_HOST, port=settings.REDIS_PORT, db=0, decode_responses=True
            )
        except Exception:
            self.redis_client = None

        self.cache = CacheManager(self.redis_client)

        # 初始化嵌入模型
        self.custom_embeddings = AliyunEmbeddingWrapper(
            user_prefs.get("embed_model") or emb_cfg.model_name,
            user_prefs.get("embed_key") or emb_cfg.api_key,
            emb_cfg.base_url,
        )

        # 初始化重排序器
        self.reranker = AliyunReranker(user_prefs.get("embed_key") or emb_cfg.api_key)

        # 构建 LLM（对话用）
        self.llm = self._build_llm(
            llm_model,
            user_prefs.get("llm_key") or llm_cfg.api_key,
            next((url for key, url in LLM_BASE_URL_MAP.items() if key in llm_model), llm_cfg.base_url),
            temperature=0,
            streaming=True,
        )

        self.rewriter_llm = self._build_llm(
            llm_model,
            user_prefs.get("llm_key") or llm_cfg.api_key,
            next((url for key, url in LLM_BASE_URL_MAP.items() if key in llm_model), llm_cfg.base_url),
            temperature=0.5,
        )

        # 向量索引路径
        self.index_path = os.path.abspath(os.path.join(settings.BASE_DIR, "data", "faiss_index"))
        index_file = os.path.join(self.index_path, "index.faiss")
        self.vector_db = (
            None
            if not os.path.exists(index_file)
            else FAISS.load_local(
                self.index_path,
                self.custom_embeddings,
                allow_dangerous_deserialization=True,
            )
        )

        # 文档处理与检索组件
        self.table_parser = TableParser()
        self.doc_processor = DocumentProcessor(db, self.table_parser, self.custom_embeddings, self.index_path)
        self.retriever = Retriever(self.reranker, self.doc_processor)

        # Agent 工具注册
        self.agent_tools = [agent_get_route, agent_search_nearby, agent_get_weather]
        self.agent_tool_map = {tool.name: tool for tool in self.agent_tools}
        self.agent_llm = self.rewriter_llm.bind_tools(self.agent_tools)

    # ----- 便捷属性 -----
    @property
    def bm25_instance(self):
        return self.doc_processor.bm25_instance

    @property
    def bm25_corpus(self):
        return self.doc_processor.bm25_corpus

    # ----- 私有工具方法 -----
    @staticmethod
    def _build_llm(
        model: str, api_key: str, base_url: str, temperature: float, streaming: bool = False
    ):
        """构造 ChatOpenAI 实例（兼容 DeepSeek/阿里云等）"""
        return ChatOpenAI(
            model=model,
            openai_api_key=api_key,
            openai_api_base=base_url,
            temperature=temperature,
            streaming=streaming,
        )

    def _process_knowledge_file(self, file_path: str, ext: str) -> List[str]:
        """解析知识文件，返回语义块列表。"""
        try:
            return self.doc_processor.process_document_advanced(file_path, ext)
        except Exception as exc:
            logger.error(f"文档解析失败: {exc}")
            raise Exception(f"文档解析失败: {exc}") from exc

    def _store_knowledge_texts(self, texts: List[str]) -> List[str]:
        """将文本存入向量库和 BM25 索引。"""
        if not texts:
            raise Exception("文档解析后没有提取到有效语义文本")
        self.vector_db = self.doc_processor.store_to_vector_db(texts, self.vector_db)
        self.doc_processor.update_bm25(texts)
        return texts

    def _load_conversation(self, session_id: str):
        """从 Redis 加载历史对话，返回 turns 列表和消息对象。"""
        history_key = f"chat_history:{session_id}"
        history_turns: List[Tuple[str, str]] = []

        if self.redis_client:
            raw = self.redis_client.get(history_key)
            if raw:
                try:
                    raw_list = json.loads(raw)[-self.MAX_HISTORY_LINES:]
                    history_turns = TextHelper.parse_history_turns(raw_list)
                except Exception:
                    history_turns = []

        history_text = TextHelper.history_turns_to_text(history_turns)
        history_lines = history_text.split("\n") if history_text else []

        chat_messages = [
            HumanMessage(content=content) if role == "human" else AIMessage(content=content)
            for role, content in history_turns
        ]

        return history_key, history_lines, chat_messages

    def _persist_history(self, history_key: str, history_lines: List[str], query: str, answer: str):
        """保存当前轮次对话到 Redis。"""
        history_lines.extend([f"用户: {query}", f"助手: {answer}"])
        if self.redis_client:
            self.redis_client.setex(
                history_key,
                self.HISTORY_TTL_SECONDS,
                json.dumps(history_lines[-self.MAX_HISTORY_LINES:]),
            )

    def _rewrite_query(self, query: str, history_text: str) -> str:
        """基于历史上下文改写查询，提升检索精度。"""
        if not history_text:
            return query
        try:
            rewrite_res = self.rewriter_llm.invoke(
                QUERY_REWRITE_PROMPT.format(history=history_text, query=query)
            )
            rewritten = TextHelper.message_content_to_text(rewrite_res).replace('"', "").strip()
            if rewritten and rewritten != query:
                print(f'✏️[改写] "{query}" → "{rewritten}"')
            return rewritten or query
        except Exception as exc:
            logger.warning(f"Query 改写失败，回退原始问题: {exc}")
            print("✏️[改写] 失败，使用原始问题")
            return query

    @staticmethod
    def _event(event_type: str, **payload) -> str:
        """生成 SSE 事件 JSON 字符串。"""
        return json.dumps({"type": event_type, **payload}, ensure_ascii=False)

    async def _prepare_agent_run(self, search_query: str, chat_history_objs: List[object]):
        """
        尝试调用 Agent 工具，若触发工具调用则返回状态消息和完整消息列表。
        否则返回 None，走普通 RAG 流程。
        """
        messages = [
            SystemMessage(content=AGENT_SYSTEM_PROMPT),
            *chat_history_objs,
            HumanMessage(content=search_query),
        ]
        agent_msg = self.agent_llm.invoke(messages)

        if not agent_msg.tool_calls:
            return None

        messages.append(agent_msg)
        status_events: List[str] = []

        for tool_call in agent_msg.tool_calls:
            status_text = next(
                (text for key, text in self.TOOL_STATUS_MESSAGES.items() if key in tool_call["name"]),
                "",
            )
            if status_text:
                status_events.append(status_text)

            tool = self.agent_tool_map.get(tool_call["name"])
            try:
                tool_result = (
                    str(await tool.ainvoke(tool_call["args"]))
                    if tool
                    else f"工具未注册: {tool_call['name']}"
                )
            except Exception as exc:
                tool_result = f"工具调用失败: {exc}"

            messages.append(ToolMessage(content=tool_result, tool_call_id=tool_call["id"]))

        return status_events, messages

    async def _stream_llm_reply(self, messages: List[object]):
        """流式获取 LLM 回复，过滤无效标记。"""
        async for chunk in self.llm.astream(messages):
            content = getattr(chunk, "content", "")
            if isinstance(content, list):
                content = "".join(
                    str(part.get("text", ""))
                    if isinstance(part, dict) and part.get("type") == "text"
                    else str(part)
                    for part in content
                )
            content = str(content or "")
            # 跳过 DSML 标记等内容
            if not content or "< | DSML" in content or "function_calls" in content or "| >" in content:
                continue
            yield content

    def _build_graph_context(self, search_query: str) -> str:
        """从知识图谱中提取与查询相关的三元组，作为辅助上下文。"""
        try:
            from app.services.graph_service import GraphService

            graph_data = GraphService(self.llm).get_full_graph(self.db)
            keywords = list(jieba.cut(search_query))
            relevant_triples = [
                f"{link['source']} -{link['value']}-> {link['target']}"
                for link in graph_data["links"]
                if any(keyword in link["source"] or keyword in link["target"] for keyword in keywords)
            ]
            return "\n".join(relevant_triples) if relevant_triples else "暂无图谱关联信息"
        except Exception as exc:
            logger.warning(f"知识图谱提取失败: {exc}")
            return "暂无图谱关联信息"

    def _build_rag_prompt(
        self,
        query: str,
        history_text: str,
        final_docs: List[str],
        search_query: str,
    ) -> str:
        """组装最终发给 LLM 的提示词，包含上下文、历史、图谱证据和数字守护提示。"""
        context = "\n".join(f"[资料{i + 1}]: {doc}" for i, doc in enumerate(final_docs))
        prompt = RAG_SYSTEM_PROMPT.format(
            context=context,
            graph_context=self._build_graph_context(search_query),
            history=history_text,
            query=query,
        )
        return prompt + TextHelper.build_evidence_guard(query, final_docs)

    async def _generate_rag_answer(self, prompt: str, query: str, final_docs: List[str]) -> str:
        """非流式 RAG 回答，并在检测到数字证据泄露时执行修复策略。"""
        first_res = await self.llm.ainvoke(prompt)
        answer = TextHelper.message_content_to_text(first_res)

        # 修复拒绝回答
        if TextHelper.has_strong_numeric_evidence(query, final_docs) and TextHelper.looks_like_refusal(
            answer
        ):
            repair_prompt = (
                prompt
                + "\n\n[Repair]\n"
                + "你之前的回答过于保守，请严格基于参考资料重新回答。如果资料中存在相关数值，请直接给出并说明来源。"
            )
            repair_res = await self.llm.ainvoke(repair_prompt)
            repaired_answer = TextHelper.message_content_to_text(repair_res)
            if repaired_answer:
                answer = repaired_answer

        return answer

    # ----- 公开方法 -----
    def async_process_and_store(self, file_path: str, ext: str, doc_id: int):
        """异步解析文件并持久化到数据库。"""
        db = SessionLocal()
        try:
            valid_texts = self._store_knowledge_texts(self._process_knowledge_file(file_path, ext))
            doc = db.query(KnowledgeDoc).filter_by(id=doc_id).first()
            if doc:
                doc.chunk_count = len(valid_texts)
                doc.parsed_content = valid_texts
                db.commit()
                logger.info(f"异步入库完成，文档 {doc_id} 共写入 {len(valid_texts)} 个切片")
        except Exception as exc:
            logger.error(f"异步解析任务失败: {exc}")
        finally:
            db.close()

    def ingest_knowledge(self, file_upload_object, filename: str) -> list:
        """同步导入知识文件，返回提取的文本块列表。"""
        logger.info(f"开始接收知识文件: {filename}")
        upload_path = os.path.join(settings.BASE_DIR, "data", "uploads")
        os.makedirs(upload_path, exist_ok=True)
        file_path = os.path.join(upload_path, filename)

        with open(file_path, "wb") as buffer:
            shutil.copyfileobj(file_upload_object.file, buffer)

        ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else "txt"
        return self._store_knowledge_texts(self._process_knowledge_file(file_path, ext))

    def collect_eval_output(self, query: str) -> dict:
        """评估接口：返回非流式的答案及其引用来源。"""
        search_query = TextHelper.strict_clean(query)

        if not self.vector_db:
            return {"answer": self.EMPTY_KNOWLEDGE_MSG, "contexts": []}

        search_query = self._rewrite_query(query, "")

        final_docs, _ = self.retriever.rerank(
            search_query,
            self.retriever.hybrid_search(search_query, self.vector_db),
        )

        if not final_docs:
            return {"answer": self.FALLBACK_MSG, "contexts": []}

        prompt = self._build_rag_prompt(query, "", final_docs, search_query)
        answer = TextHelper.message_content_to_text(self.llm.invoke(prompt))

        if TextHelper.has_strong_numeric_evidence(query, final_docs) and TextHelper.looks_like_refusal(answer):
            repair_prompt = (
                prompt
                + "\n\n[Repair]\n"
                + "你之前的回答过于保守，请严格基于参考资料重新回答。如果资料中存在相关数值，请直接给出并说明来源。"
            )
            repaired = TextHelper.message_content_to_text(self.llm.invoke(repair_prompt))
            if repaired:
                answer = repaired

        return {"answer": answer, "contexts": final_docs}

    async def chat_stream(self, query: str, session_id: str = "default"):
        """
        主入口：流式对话，支持语义缓存、Agent 工具调用和 RAG 检索。
        """
        logger.info(f"收到提问: {query}")
        print(f"📥[提问] {query}")
        query = TextHelper.strict_clean(query)

        # 向量库为空，直接返回提示
        if not self.vector_db:
            yield self._event("error", data=self.EMPTY_KNOWLEDGE_MSG)
            return

        # 加载历史
        history_key, history_lines, chat_messages = self._load_conversation(session_id)

        # 尝试语义缓存命中（仅无历史时启用）
        query_vector = self.custom_embeddings.embed_query(query)
        cached_answer, cached_sources = (
            self.cache.get_semantic_cache(query_vector) if not history_lines else (None, None)
        )
        if cached_answer:
            print("⚡[缓存命中] 语义缓存直接返回")
            if cached_sources:
                yield self._event("sources", data=cached_sources)
            yield self._event("content", data=cached_answer)
            self._persist_history(history_key, history_lines, query, cached_answer)
            yield self._event("done", full_answer=cached_answer)
            return

        # 查询改写
        search_query = self._rewrite_query(query, "\n".join(history_lines))

        # 尝试 Agent 工具调度
        try:
            agent_run = await self._prepare_agent_run(search_query, chat_messages)
        except Exception as exc:
            logger.warning(f"Agent 调度异常，回退到 RAG: {exc}")
            print(f"🤖[Agent] 调度异常，回退 RAG: {exc}")
            agent_run = None

        if agent_run:
            print("🤖[Agent] 检测到工具调用，进入 Agent 模式")
            status_events, agent_messages = agent_run
            for status_text in status_events:
                yield self._event("content", data=status_text)

            full_answer = ""
            async for content in self._stream_llm_reply(agent_messages):
                full_answer += content
                yield self._event("content", data=content)

            self._persist_history(history_key, history_lines, query, full_answer)
            yield self._event("done", full_answer=full_answer)
            return

        # 常规 RAG 检索与生成
        print("🤖[Agent] 无工具调用，走 RAG 流程")
        final_docs, _ = self.retriever.rerank(
            search_query,
            self.retriever.hybrid_search(search_query, self.vector_db),
        )

        if not final_docs:
            print("⚠️[RAG] 检索无相关结果，返回兜底回答")
            yield self._event("content", data=self.FALLBACK_MSG)
            yield self._event("done", full_answer=self.FALLBACK_MSG)
            return

        yield self._event("sources", data=final_docs)

        try:
            prompt = self._build_rag_prompt(query, "\n".join(history_lines), final_docs, search_query)
            print("🧠[生成] AI 开始流式生成...")
            full_answer = ""
            async for content in self._stream_llm_reply([HumanMessage(content=prompt)]):
                full_answer += content
                yield self._event("content", data=content)

        except Exception as exc:
            logger.error(f"RAG 生成失败: {exc}")
            print(f"❌[生成] 异常: {exc}")
            yield self._event("error", data=str(exc))
            return

        print(f"✅[生成] 完成，共 {len(full_answer)} 字")

        if TextHelper.has_strong_numeric_evidence(query, final_docs) and TextHelper.looks_like_refusal(
            full_answer
        ):
            print("🔧[Repair] 检测到拒答，触发修复重生成...")
            repair_prompt = (
                prompt
                + "\n\n[Repair]\n"
                + "你之前的回答过于保守，请严格基于参考资料重新回答。如果资料中存在相关数值，请直接给出并说明来源。"
            )
            repaired_answer = ""
            async for content in self._stream_llm_reply([HumanMessage(content=repair_prompt)]):
                repaired_answer += content
                yield self._event("content", data=content)
            if repaired_answer:
                full_answer = repaired_answer

        self.cache.set_semantic_cache(query_vector, full_answer, final_docs)
        print("💾[缓存] 已写入语义缓存")
        self._persist_history(history_key, history_lines, query, full_answer)
        yield self._event("done", full_answer=full_answer)