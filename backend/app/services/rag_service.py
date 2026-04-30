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
import jieba  # 中文分词，用于 BM25 关键词提取
from langchain_community.vectorstores import FAISS  # 向量数据库（本地）
from langchain_core.documents import Document  # LangChain 文档对象
from langchain_core.embeddings import Embeddings  # 嵌入模型基类
from langchain_core.messages import HumanMessage, ToolMessage, AIMessage, SystemMessage  # 对话消息类型
from langchain_openai import ChatOpenAI  # 兼容 OpenAI 接口的大模型调用
from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter  # 文本分块工具
from pypdf import PdfReader, PdfWriter  # PDF 读取与写入
from rank_bm25 import BM25Okapi  # BM25 关键词检索算法
from sqlalchemy.orm import Session  # 数据库会话

# 从项目内部导入配置、提示词、数据库模型、缓存服务等
from app.core.config import settings
from app.core.prompts import RAG_SYSTEM_PROMPT, QUERY_REWRITE_PROMPT, AGENT_SYSTEM_PROMPT
from app.db.session import SessionLocal
from app.models import User
from app.models.knowledge import KnowledgeDoc
from app.services.cache_service import CacheManager
from app.services.config_service import ConfigService
from app.services.tool_service import agent_get_route, agent_search_nearby, agent_get_weather

# 解决 Windows 控制台中文输出乱码问题
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')

# 关闭 LangChain 的匿名遥测（不向官方发送使用数据）
os.environ["ANONYMIZED_TELEMETRY"] = "False"

# 获取当前模块的日志记录器
logger = logging.getLogger("RAGService")


# ================================================
# 自定义的阿里云文本嵌入模型封装
# 作用：把一段文本转换成数字向量（用于语义搜索）
# ================================================
class AliyunEmbeddingWrapper(Embeddings):
    def __init__(self, model, api_key, base_url):
        self.model, self.api_key = model, api_key
        # 构造阿里云 Embedding 接口的完整 URL
        self.url = base_url.replace("/compatible-mode/v1", "/compatible-mode/v1/embeddings")

    # 批量将多篇文本转为向量，返回 List[List[float]]
    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        results = []
        # 限制 HTTP 连接数，避免过多并发
        limits = httpx.Limits(max_keepalive_connections=5, max_connections=10)
        with httpx.Client(timeout=120.0, limits=limits) as client:
            # 每 10 个文本为一组发送请求（避免单次请求过大）
            for i in range(0, len(texts), 10):
                batch = [str(t) for t in texts[i: i + 10]]
                payload = {"model": self.model, "input": batch}
                headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

                # 网络异常时最多重试 3 次
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        resp = client.post(self.url, json=payload, headers=headers)
                        if resp.status_code != 200:
                            raise Exception(f"Embedding API Error: {resp.status_code} - {resp.text}")
                        data = resp.json()
                        # 把返回结果中的向量依次取出
                        results.extend([item["embedding"] for item in data["data"]])
                        break
                    except Exception as e:
                        logger.warning(f"⚠️ 阿里云 Embedding 请求断开 (尝试 {attempt + 1}/{max_retries}): {e}")
                        if attempt == max_retries - 1:
                            raise Exception(f"Embedding 最终失败: {e}")
                        time.sleep(2)

                # 每组之间休息 0.5 秒，防止触发频率限制
                time.sleep(0.5)

        return results

    # 将单条查询文本转为向量，内部复用上面方法
    def embed_query(self, text: str) -> List[float]:
        return self.embed_documents([text])[0]


# ================================================
# 阿里云的文档重排序（Rerank）服务封装
# 作用：对候选文档进行更精细的相关性打分，挑出最匹配的
# ================================================
class AliyunReranker:
    def __init__(self, api_key, base_url):
        self.api_key = api_key
        self.url = "https://dashscope.aliyuncs.com/api/v1/services/rerank/text-rerank/text-rerank"

    # 传入 query 和多个文档内容，返回按相关性排序的列表（带分数）
    def rerank(self, query: str, documents: List[str], top_n: int = 10) -> List[dict]:
        payload = {
            "model": "gte-rerank",
            "input": {
                "query": query,
                "documents": documents
            },
            "parameters": {
                "return_documents": False,  # 只返回分数和索引，不返回全文
                "top_n": top_n
            }
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
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


# ================================================
# 文本处理工具类
# 包含清洗、历史解析、表格识别辅助等功能
# ================================================
class TextHelper:
    # 严格清洗文本：去除非可见控制字符，合并空白，修复数字与字母间的多余空格等
    @staticmethod
    def strict_clean(text: str) -> str:
        if not text:
            return ""
        text = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', text)
        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'(?<=\d)\s+(?=[a-zA-Z])', '', text)
        text = re.sub(r'(?<=[a-zA-Z])\s+(?=/)', '', text)
        text = re.sub(r'(?<=/)\s+(?=[a-zA-Z])', '', text)
        return text.strip()

    # 去除消息前缀中的角色标记（例如 "用户:"  "助手:"）
    @staticmethod
    def strip_role_prefix(msg: str) -> str:
        if not isinstance(msg, str):
            msg = str(msg)
        return re.sub(
            r"^\s*(?:user|assistant|human|ai|用户|助手)\s*[:：]\s*",
            "",
            msg,
            flags=re.IGNORECASE,
        ).strip()

    # 解析聊天历史列表，按奇偶位置推断角色（human/ai），返回 (角色, 内容) 列表
    @staticmethod
    def parse_history_turns(raw_messages: List[str]) -> List[Tuple[str, str]]:
        turns: List[Tuple[str, str]] = []
        for i, raw_msg in enumerate(raw_messages):
            msg = str(raw_msg) if raw_msg is not None else ""
            role = "human" if i % 2 == 0 else "ai"  # 默认第一条是人类
            explicit = re.match(
                r"^\s*(user|assistant|human|ai|用户|助手)\s*[:：]",
                msg,
                flags=re.IGNORECASE,
            )
            # 如果消息中明确写了角色标记，则按标记识别
            if explicit:
                role_name = explicit.group(1).lower()
                role = "ai" if role_name in ("assistant", "ai", "助手") else "human"
            content = TextHelper.strip_role_prefix(msg)
            if content:
                turns.append((role, content))
        return turns

    # 将对话轮次列表转换成纯文本（便于拼接 prompt）
    @staticmethod
    def history_turns_to_text(turns: List[Tuple[str, str]]) -> str:
        return "\n".join(
            f"{'用户' if role == 'human' else '助手'}: {content}"
            for role, content in turns
        )

    # 从 LangChain 消息对象中提取纯文本内容
    @staticmethod
    def message_content_to_text(message_obj) -> str:
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

    # 判断 query 是否强烈需要数值证据（比如问多少、罚款金额等）且文档中确实有数字或表格
    @staticmethod
    def has_strong_numeric_evidence(query: str, docs: List[str]) -> bool:
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

    # 为 prompt 增加一个“一致性守卫”提示，强制大模型必须从资料中提取具体数值并注明出处
    @staticmethod
    def build_evidence_guard(query: str, docs: List[str]) -> str:
        if not TextHelper.has_strong_numeric_evidence(query, docs):
            return ""
        return (
            "\n[Consistency Guard]\n"
            "- If reference materials include relevant table or numeric mappings, you MUST answer with the exact value and unit.\n"
            "- You MUST cite source tags like [资料1].\n"
            "- You MUST NOT output '未找到' style refusal when a matching value exists in references.\n"
            "- Put the conclusion in the first sentence."
        )

    # 简单检测大模型输出是否是拒答语句（如“未找到”）
    @staticmethod
    def looks_like_refusal(text: str) -> bool:
        if not text:
            return False
        return bool(re.search(r"(未找到|抱歉|无明确规定|没有相关依据)", text))


# ================================================
# 表格解析与提取工具类
# 自动识别 Markdown 表格、竖线分隔表格、空格分隔表格等
# ================================================
class TableParser:
    # 判断是否是 Markdown 表格的分隔行（例如 |---|---|）
    @staticmethod
    def is_markdown_table_separator(line: str) -> bool:
        if not line:
            return False
        stripped = line.strip()
        if "|" not in stripped:
            return False
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        valid_cells = [cell for cell in cells if cell]
        if not valid_cells:
            return False
        return all(re.fullmatch(r":?-{3,}:?", cell) for cell in valid_cells)

    # 判断是否是 ASCII 边框线（例如 +---+---+）
    @staticmethod
    def is_ascii_grid_border(line: str) -> bool:
        stripped = line.strip()
        return bool(stripped) and bool(re.fullmatch(r"\+-[-+=:]+(?:\+-[-+=:]+)*\+?", stripped))

    # 判断行中是否含有竖线（可能是表格）
    @staticmethod
    def is_pipe_like_line(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith("|") or stripped.endswith("|"):
            return True
        return stripped.count("|") >= 2

    # 按连续两个以上空格或制表符分割一行，得到列内容
    @staticmethod
    def split_space_columns(line: str) -> List[str]:
        stripped = line.strip()
        if not stripped:
            return []
        return [cell.strip() for cell in re.split(r"\s{2,}|\t+", stripped) if cell.strip()]

    # 判断一行是否是空格分隔的表格行（至少有3列，且包含数字）
    def is_whitespace_table_line(self, line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if stripped.startswith("#"):
            return False
        cols = self.split_space_columns(stripped)
        if len(cols) < 3:
            return False
        has_digit = any(re.search(r"\d", c) for c in cols)
        sentence_like = len(cols) == 3 and all(len(c) > 12 for c in cols)
        return has_digit and not sentence_like

    # 判断是否是表格标题（如“表1 xxx”）
    @staticmethod
    def looks_like_table_title(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        if re.search(r"(?:^|[\s（(])(?:表|附表|Table)\s*[A-Za-z]?\s*[\d一二三四五六七八九十\.]+", stripped):
            return True
        if len(stripped) <= 80 and ("表" in stripped or "Table" in stripped):
            return True
        return False

    # 判断是否是表格注释行（例如“注：”）
    @staticmethod
    def looks_like_table_note(line: str) -> bool:
        stripped = line.strip()
        if not stripped:
            return False
        return bool(re.match(r"(?i)^(?:注|备注|说明|note)\s*(?:[:：\.、]|$)", stripped))

    # 解析单个 Markdown 表格行，按竖线分割
    @staticmethod
    def parse_markdown_row(line: str) -> List[str]:
        if "|" not in line:
            return []
        return [cell.strip() for cell in line.strip().strip("|").split("|")]

    # 自动探测当前位置是否是某种表格的开始，返回表格类型：md / grid / pipe / space
    def detect_table_mode(self, lines: List[str], idx: int) -> Optional[str]:
        line = lines[idx]
        next_line = lines[idx + 1] if idx + 1 < len(lines) else ""
        next2_line = lines[idx + 2] if idx + 2 < len(lines) else ""

        if self.is_pipe_like_line(line) and self.is_markdown_table_separator(next_line):
            return "md"

        if self.is_ascii_grid_border(line) and self.is_pipe_like_line(next_line):
            return "grid"

        if self.is_pipe_like_line(line):
            if self.is_pipe_like_line(next_line):
                return "pipe"
            if not next_line.strip() and self.is_pipe_like_line(next2_line):
                return "pipe"

        if self.is_whitespace_table_line(line):
            if self.is_whitespace_table_line(next_line):
                return "space"
            if not next_line.strip() and self.is_whitespace_table_line(next2_line):
                return "space"

        return None

    # 核心：将整篇 Markdown 文本拆成 **文本块** 和 **表格块** 的列表
    def extract_markdown_blocks(self, text: str) -> List[dict]:
        lines = text.splitlines()
        blocks: List[dict] = []
        text_buffer: List[str] = []  # 暂存连续的普通文本
        i = 0

        while i < len(lines):
            line = lines[i]
            mode = self.detect_table_mode(lines, i)
            if mode:
                # 如果发现表格，先看看前面有没有可能属于该表格的标题行
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

                # 将之前堆积的普通文本先保存为一个文本块
                text_part = "\n".join(text_buffer).strip()
                if text_part:
                    blocks.append({"type": "text", "content": text_part})
                text_buffer = []

                # 开始收集表格行
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

                # 表格后的几行注释也收编进来
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

            # 不是表格开头，把当前行放入普通文本缓冲区
            text_buffer.append(line)
            i += 1

        # 最后剩余的文本作为一个块
        text_part = "\n".join(text_buffer).strip()
        if text_part:
            blocks.append({"type": "text", "content": text_part})

        return blocks

    # 把过长的 Markdown 表格按行切分成多个小块，带上表头和重叠
    def split_markdown_table(self, table_markdown: str, max_chars: int = 1200, row_overlap: int = 1) -> List[str]:
        lines = [line.rstrip() for line in table_markdown.splitlines() if line.strip()]
        if len(lines) < 2:
            stripped = table_markdown.strip()
            return [stripped] if stripped else []

        # 分离出表格前面的说明文字和后面的注释
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

        # 如果有标准表头+分隔行，按行数切分
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
                    overlap_rows = current_rows[-row_overlap:] if row_overlap > 0 else []
                    current_rows = overlap_rows.copy()
                    current_len = base_len + sum(len(r) + 1 for r in current_rows)
                current_rows.append(row)
                current_len += row_len

            if current_rows:
                chunk_lines = leading_context + [header, separator] + current_rows + trailing_context
                chunks.append("\n".join(chunk_lines))
            return chunks

        # 无标准分隔行时，也尽量合理切分
        prefix_rows = leading_context[:]
        data_rows = core[:]
        if len(data_rows) >= 2 and self.is_pipe_like_line(data_rows[0]):
            first_cells = [c for c in self.parse_markdown_row(data_rows[0]) if c]
            numeric_like = 0
            for c in first_cells:
                if re.search(r"\d", c):
                    numeric_like += 1
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

    # 将表格内容转换为结构化文本（“列名=值”的方式），方便大模型理解
    def table_to_structured_text(self, table_markdown: str, max_rows: int = 40) -> str:
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
            if all(re.fullmatch(r":?-{3,}:?", cell) for cell in cells if cell):
                continue
            rows.append(cells)

        if len(rows) < 2:
            return ""

        headers = rows[0]
        structured_lines: List[str] = []
        for idx, row in enumerate(rows[1:max_rows + 1], start=1):
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


# ================================================
# 文档处理器：解析各种文件 → 分块 → 存入向量库 + 建立 BM25 索引
# ================================================
class DocumentProcessor:
    def __init__(self, db: Session, table_parser: TableParser, embeddings, index_path: str):
        self.db = db
        self.table_parser = table_parser
        self.embeddings = embeddings
        self.index_path = index_path
        self.bm25_instance = None  # BM25 模型实例
        self.bm25_corpus: List[str] = []  # BM25 文本语料
        self._init_bm25()  # 初始化时加载数据库中已有的文本

    # 对 Markdown 文本进行语义分块（按标题层级切分，再对表格/文本分别处理）
    def split_markdown_with_table_awareness(self, md_text: str) -> List[Document]:
        headers_to_split_on = [("#", "章"), ("##", "节"), ("###", "条"), ("####", "款")]
        md_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on, strip_headers=False)
        md_splits = md_splitter.split_text(md_text)  # 按标题层次粗分

        text_splitter = RecursiveCharacterTextSplitter(chunk_size=800, chunk_overlap=150)  # 文本二次细分
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
                    # 表格块可能太长，需要切成合适大小
                    table_chunks = self.table_parser.split_markdown_table(content, max_chars=1200, row_overlap=1)
                    for table_chunk in table_chunks:
                        final_splits.append(
                            Document(page_content=table_chunk, metadata={**metadata, "block_type": "table"})
                        )
                else:
                    # 普通文本再用递归分块器细分
                    for piece in text_splitter.split_text(content):
                        piece = piece.strip()
                        if piece:
                            final_splits.append(
                                Document(page_content=piece, metadata={**metadata, "block_type": "text"})
                            )

        return final_splits

    # 初始化 BM25：从数据库加载所有文档的已解析文本，分词后构建 BM25
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
                logger.info(f"⚡ BM25 极速构建完成，共挂载 {len(all_texts)} 条高质量语义片段")
            else:
                logger.warning("BM25 初始化为空，知识库没有可用文本")
        except Exception as e:
            logger.error(f"BM25 初始化失败: {e}")

    # 在新增文档后，增量更新 BM25 索引
    def update_bm25(self, new_texts: List[str]):
        self.bm25_corpus.extend(new_texts)
        tokenized_corpus = [list(jieba.cut(text)) for text in self.bm25_corpus]
        self.bm25_instance = BM25Okapi(tokenized_corpus)

    # 高级文档解析入口：支持 PDF / TXT / 其他（docling 转换）
    def process_document_advanced(self, file_path: str, ext: str) -> list:
        print(f"👁️ [解析路由] 启动文档解析: {file_path} (格式: {ext})")
        md_text = ""

        if ext == 'pdf':
            from docling.document_converter import DocumentConverter  # 使用 Docling 将 PDF 转为 Markdown
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

        elif ext == 'txt':
            print("📄 检测到 TXT 文件，采用极速纯文本读取...")
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    md_text = f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='gbk') as f:
                    md_text = f.read()

        else:
            from docling.document_converter import DocumentConverter
            converter = DocumentConverter()
            result = converter.convert(file_path)
            md_text = result.document.export_to_markdown()

        # 统一进行分块处理
        final_splits = self.split_markdown_with_table_awareness(md_text)

        # 对每个分块进行语义富化：添加章节路径、表格结构化信息等
        valid_texts = []
        for split in final_splits:
            hierarchy = [split.metadata[h] for h in ["章", "节", "条", "款"] if h in split.metadata]
            path_str = " > ".join(hierarchy) if hierarchy else "通用正文"

            block_type = split.metadata.get("block_type", "text")
            content = split.page_content.strip()

            # 跳过目录、无意义内容
            if "目 次" in path_str or "........" in content:
                continue

            content = re.sub(r'[\x00-\x08\x0b-\x0c\x0e-\x1f\x7f]', '', content)

            if len(content) < 15:  # 太短的句子丢弃
                continue

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

    # 将文本向量化并存入本地 FAISS 索引
    def store_to_vector_db(self, texts: List[str], vector_db) -> object:
        if vector_db is None:
            vector_db = FAISS.from_texts(texts, self.embeddings)
        else:
            vector_db.add_texts(texts)
        vector_db.save_local(self.index_path)
        return vector_db


# ================================================
# 检索器：混合检索 + 重排序
# ================================================
class Retriever:
    def __init__(self, embeddings, reranker: AliyunReranker, doc_processor: DocumentProcessor):
        self.embeddings = embeddings
        self.reranker = reranker
        self.doc_processor = doc_processor

    # 混合检索：同时使用 FAISS 向量搜索 + BM25 关键词搜索，合并去重
    def hybrid_search(self, query: str, vector_db, faiss_k: int = 40, bm25_k: int = 20) -> List[str]:
        faiss_docs = vector_db.similarity_search(query, k=faiss_k)  # 语义检索

        bm25_docs = []
        if self.doc_processor.bm25_instance:
            tokenized_query = list(jieba.cut(query))
            scores = self.doc_processor.bm25_instance.get_scores(tokenized_query)
            top_n = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:bm25_k]
            bm25_docs = [Document(page_content=self.doc_processor.bm25_corpus[i]) for i in top_n if scores[i] > 0]

        # 合并去重（以 text 为键）
        candidates = {}
        for d in faiss_docs + bm25_docs:
            if d.page_content not in candidates:
                candidates[d.page_content] = d.page_content

        candidate_list = list(candidates.values())
        print(f"🎯[Rerank] 正在对 {len(candidate_list)} 个片段进行精排...")
        return candidate_list

    # 使用阿里云 Rerank 模型对候选文档进行精确排序，并过滤低分文档
    def rerank(self, query: str, candidates: List[str], top_n: int = 10,
               threshold: float = 0.05) -> Tuple[List[str], List[Tuple[float, str]]]:
        final_docs = []
        rerank_scored_docs: List[Tuple[float, str]] = []

        try:
            rerank_results = self.reranker.rerank(query, candidates, top_n=top_n)

            for res in rerank_results:
                score = res.get('relevance_score', -100)
                idx = res.get('index')
                if idx is None or idx < 0 or idx >= len(candidates):
                    continue
                rerank_scored_docs.append((score, candidates[idx]))

                if score < threshold:  # 相关性太低的不纳入最终结果
                    continue

                final_docs.append(candidates[idx])

        except Exception as e:
            print(f"Rerank 异常 (降级为普通截取): {e}")
            final_docs = candidates[:5]  # 降级方案：直接取前5个

        # 如果全被阈值过滤掉了，取分数最高的前3个兜底
        if not final_docs and rerank_scored_docs:
            rerank_scored_docs.sort(key=lambda x: x[0], reverse=True)
            for _, doc in rerank_scored_docs[:3]:
                if doc not in final_docs:
                    final_docs.append(doc)

        return final_docs, rerank_scored_docs


# ================================================
# RAG 服务主类：整合所有组件，提供知识库问答接口
# ================================================
class RAGService:
    def __init__(self, db: Session, current_user: User = None):
        self.db = db
        # 获取嵌入模型和 LLM 的配置
        emb_cfg = ConfigService.get_active_config(db, "embedding")
        llm_cfg = ConfigService.get_active_config(db, "llm")
        if not emb_cfg or not llm_cfg:
            raise Exception("AI 配置缺失")

        # 连接 Redis（用于缓存对话历史等）
        import redis
        try:
            self.redis_client = redis.Redis(
                host=settings.REDIS_HOST,
                port=settings.REDIS_PORT,
                db=0,
                decode_responses=True
            )
        except:
            self.redis_client = None

        self.cache = CacheManager(self.redis_client)  # 语义缓存管理器

        # 用户自定义的模型配置 > 系统默认配置
        user_prefs = current_user.ai_preferences if (current_user and current_user.ai_preferences) else {}
        final_llm_model = user_prefs.get("llm_model") or llm_cfg.model_name
        final_llm_key = user_prefs.get("llm_key") or llm_cfg.api_key
        final_emb_model = user_prefs.get("embed_model") or emb_cfg.model_name
        final_emb_key = user_prefs.get("embed_key") or emb_cfg.api_key
        llm_base_url = "https://api.deepseek.com" if "deepseek" in final_llm_model else llm_cfg.base_url

        # 初始化嵌入模型、重排序器
        self.custom_embeddings = AliyunEmbeddingWrapper(final_emb_model, final_emb_key, emb_cfg.base_url)
        self.reranker = AliyunReranker(final_emb_key, emb_cfg.base_url)

        # 初始化大模型（生成答案）和改写模型（用于 query 改写）
        self.llm = ChatOpenAI(
            model=final_llm_model,
            openai_api_key=final_llm_key,
            openai_api_base=llm_base_url,
            temperature=0,
            streaming=True  # 流式输出
        )

        self.rewriter_llm = ChatOpenAI(
            model=final_llm_model,
            openai_api_key=final_llm_key,
            openai_api_base=llm_base_url,
            temperature=0.5
        )

        # 本地 FAISS 索引路径
        self.index_path = os.path.abspath(os.path.join(settings.BASE_DIR, "data", "faiss_index"))
        self.vector_db = None
        if os.path.exists(os.path.join(self.index_path, "index.faiss")):
            self.vector_db = FAISS.load_local(self.index_path, self.custom_embeddings,
                                              allow_dangerous_deserialization=True)

        # 表格解析器、文档处理器、检索器
        self.table_parser = TableParser()
        self.doc_processor = DocumentProcessor(db, self.table_parser, self.custom_embeddings, self.index_path)
        self.retriever = Retriever(self.custom_embeddings, self.reranker, self.doc_processor)

    @property
    def bm25_instance(self):
        return self.doc_processor.bm25_instance

    @property
    def bm25_corpus(self):
        return self.doc_processor.bm25_corpus

    # 异步处理文档（后台任务）：解析、存储到向量库和BM25，并更新数据库
    def async_process_and_store(self, file_path: str, ext: str, doc_id: int):
        db = SessionLocal()
        try:
            valid_texts = self.doc_processor.process_document_advanced(file_path, ext)
            if not valid_texts:
                logger.warning(f"文档 {doc_id} 未提取到有效文本")
                return

            self.vector_db = self.doc_processor.store_to_vector_db(valid_texts, self.vector_db)
            self.doc_processor.update_bm25(valid_texts)

            doc = db.query(KnowledgeDoc).filter_by(id=doc_id).first()
            if doc:
                doc.chunk_count = len(valid_texts)
                doc.parsed_content = valid_texts
                db.commit()
                logger.info(f"🎉 异步任务完成！文档ID:{doc_id} 已成功存入 {len(valid_texts)} 个切片。")

        except Exception as e:
            logger.error(f"❌ 异步解析任务崩溃: {e}")
        finally:
            db.close()

    # 知识入库接口：接收上传文件，解析后存入向量数据库
    def ingest_knowledge(self, file_upload_object, filename: str) -> list:
        print(f"\n🚀 [知识入库] 接收文件: {filename}")

        upload_path = os.path.join(settings.BASE_DIR, "data", "uploads")
        os.makedirs(upload_path, exist_ok=True)
        file_save_path = os.path.join(upload_path, filename)

        with open(file_save_path, "wb") as buffer:
            shutil.copyfileobj(file_upload_object.file, buffer)

        try:
            ext = filename.rsplit('.', 1)[-1].lower() if '.' in filename else 'txt'
            valid_texts = self.doc_processor.process_document_advanced(file_save_path, ext)
        except Exception as e:
            logger.error(f"文档视觉解析失败: {e}")
            raise Exception(f"文档解析失败: {str(e)}")

        if not valid_texts:
            raise Exception("文档解析后没有提取到有效语义文本")

        self.vector_db = self.doc_processor.store_to_vector_db(valid_texts, self.vector_db)
        self.doc_processor.update_bm25(valid_texts)

        return valid_texts

    # 聊天流式接口：接收用户问题，返回流式回答（支持 Agent 工具调用和 RAG 检索）
    async def chat_stream(self, query: str, session_id: str = "default"):
        print(f"\n{'=' * 10} [提问] {query} {'=' * 10}")
        query = TextHelper.strict_clean(query)
        q_vec = self.custom_embeddings.embed_query(query)

        if not self.vector_db:
            yield json.dumps({"type": "error", "data": "知识库为空"})
            return

        # 从 Redis 中获取历史对话（最近 8 轮）
        history_key = f"chat_history:{session_id}"
        history_turns: List[Tuple[str, str]] = []
        chat_history_objs = []
        if self.redis_client:
            raw = self.redis_client.get(history_key)
            if raw:
                try:
                    raw_list = json.loads(raw)[-8:]
                    history_turns = TextHelper.parse_history_turns(raw_list)
                except Exception:
                    history_turns = []

        for role, content in history_turns:
            if role == "human":
                chat_history_objs.append(HumanMessage(content=content))
            else:
                chat_history_objs.append(AIMessage(content=content))

        history_text = TextHelper.history_turns_to_text(history_turns)
        history_list = history_text.split("\n") if history_text else []

        # 如果有历史对话，调用改写模型优化搜素 query（融合上下文）
        search_query = query
        if history_text:
            history_str = history_text
            try:
                rewrite_res = self.rewriter_llm.invoke(QUERY_REWRITE_PROMPT.format(history=history_str, query=query))
                search_query = rewrite_res.content.strip().replace('"', '')
                print(f"🔍 [改写后] {search_query}")
            except:
                pass

        # ---------- Agent 工具调用阶段 ----------
        try:
            print("🤖 [Agent] 正在思考...")

            agent_system_prompt = SystemMessage(content=AGENT_SYSTEM_PROMPT)

            # 工具列表：路线规划、附近搜索、天气查询
            tools = [agent_get_route, agent_search_nearby, agent_get_weather]
            llm_with_tools = self.rewriter_llm.bind_tools(tools)

            messages_for_agent = [agent_system_prompt] + chat_history_objs + [HumanMessage(content=search_query)]

            agent_msg = llm_with_tools.invoke(messages_for_agent)

            # 如果 Agent 决定调用工具
            if agent_msg.tool_calls:
                print(f"🛠️ [Agent] 命中工具: {[t['name'] for t in agent_msg.tool_calls]}")
                messages_for_agent.append(agent_msg)

                for tool_call in agent_msg.tool_calls:
                    tool_name = tool_call["name"]
                    tool_args = tool_call["args"]

                    # 发送工具执行的状态提示给前端
                    status_text = ""
                    if "route" in tool_name: status_text = "🔄 **正在规划出行方案...**\n\n"
                    elif "nearby" in tool_name: status_text = "🔄 **正在搜索周边设施...**\n\n"
                    elif "weather" in tool_name: status_text = "🔄 **正在查询实时天气...**\n\n"

                    yield json.dumps({"type": "content", "data": status_text})

                    selected_tool = next(t for t in tools if t.name == tool_name)
                    try:
                        tool_result = await selected_tool.ainvoke(tool_args)
                    except Exception as tool_err:
                        tool_result = f"工具调用失败: {str(tool_err)}"

                    messages_for_agent.append(ToolMessage(content=str(tool_result), tool_call_id=tool_call["id"]))

                print("🤖 [Agent] 生成综合建议...")
                full_answer = ""

                # 流式输出 Agent 的最终回答
                async for chunk in self.llm.astream(messages_for_agent):
                    content = chunk.content
                    if content:
                        if "< | DSML" in content or "function_calls" in content or "| >" in content:
                            continue
                        full_answer += content
                        yield json.dumps({"type": "content", "data": content}, ensure_ascii=False)

                # 保存对话历史到 Redis
                if self.redis_client:
                    history_list.extend([f"用户: {query}", f"助手: {full_answer}"])
                    history_key = f"chat_history:{session_id}"
                    self.redis_client.setex(history_key, 3600, json.dumps(history_list[-16:]))

                yield json.dumps({"type": "done", "full_answer": full_answer})
                return
            else:
                print("🧠 [Agent] 未命中工具，转入法规库检索...")

        except Exception as e:
            print(f"⚠️ Agent 调度异常: {e}")

        # ---------- RAG 检索与生成阶段 ----------
        print("📡 [混合检索] 执行中...")
        candidate_list = self.retriever.hybrid_search(search_query, self.vector_db)

        final_docs, _ = self.retriever.rerank(search_query, candidate_list)

        # 如果没有合适的文档，返回兜底回答
        if not final_docs:
            print("❌ [防幻觉] 所有片段得分均低于阈值，判定为知识库无相关内容。")
            fallback_msg = "抱歉，根据目前的知识库，未找到与您描述完全匹配的交通法规条款。建议您提供更详细的关键词，或咨询当地交管部门。"
            yield json.dumps({"type": "content", "data": fallback_msg})
            yield json.dumps({"type": "done", "full_answer": fallback_msg})
            return

        yield json.dumps({"type": "sources", "data": final_docs})  # 返回引用来源

        # 提取知识图谱中的相关关系（可选增强）
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

        # 拼接最终 Prompt，包含资料、图谱、历史
        context = "\n".join([f"[资料{i + 1}]: {d}" for i, d in enumerate(final_docs)])

        final_prompt = RAG_SYSTEM_PROMPT.format(
            context=context,
            graph_context=graph_context,
            history="\n".join(history_list),
            query=query
        )
        final_prompt += TextHelper.build_evidence_guard(query, final_docs)  # 数值类问题特殊保护

        full_answer = ""
        try:
            print("🤖 [AI生成] 开始生成...")
            first_res = await self.llm.ainvoke(final_prompt)
            full_answer = TextHelper.message_content_to_text(first_res)

            # 如果高证据需求的问题出现了拒答，强制二次生成
            if TextHelper.has_strong_numeric_evidence(query, final_docs) and TextHelper.looks_like_refusal(full_answer):
                print("♻️ [一致性修复] 首次回答疑似误拒答，触发二次生成...")
                repair_prompt = (
                    final_prompt
                    + "\n\n[Repair]\n"
                    + "Your previous answer was too conservative. Re-answer strictly based on references. "
                    + "If a numeric/table value exists, provide it directly with [资料x] citation."
                )
                repair_res = await self.llm.ainvoke(repair_prompt)
                repaired_answer = TextHelper.message_content_to_text(repair_res)
                if repaired_answer:
                    full_answer = repaired_answer

            yield json.dumps({"type": "content", "data": full_answer}, ensure_ascii=False)

            # 存入语义缓存与对话历史
            self.cache.set_semantic_cache(q_vec, full_answer, final_docs)
            if self.redis_client:
                history_list.extend([f"用户: {query}", f"助手: {full_answer}"])
                self.redis_client.setex(history_key, 3600, json.dumps(history_list[-16:]))

            yield json.dumps({"type": "done", "full_answer": full_answer})

        except Exception as e:
            print(f"❌ 生成异常: {e}")
            yield json.dumps({"type": "error", "data": str(e)})