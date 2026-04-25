import json
import logging
import re
import time
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlalchemy.orm import Session

from app.models.agent_run import AgentRun, AgentRunCheckpoint, AgentMemoryRecord

logger = logging.getLogger(__name__)


class AgentStateStore:
    """基于 Redis 的 Agent 状态存储：长期记忆 + checkpoint。"""

    MEMORY_KEY = "agentic:memory:{user_id}"
    RUN_KEY = "agentic:run:{run_id}"
    RUN_TIMELINE_KEY = "agentic:run_timeline:{run_id}"
    SESSION_LAST_RUN_KEY = "agentic:last_run:{user_id}:{session_id}"

    CHECKPOINT_TTL_SECONDS = 86400 * 7
    MEMORY_TTL_SECONDS = 86400 * 30
    MEMORY_MAX_ITEMS = 80  # 每个用户最多存80条记忆
    TIMELINE_MAX_ITEMS = 200  # 每轮对话最多记录200步轨迹
    MSG_MAX_ITEMS = 12  # 最多保留12条消息
    HISTORY_MSG_MAX_ITEMS = 10  # 历史消息最多10条
    DOC_MAX_ITEMS = 8  # 最多带8篇文档
    SOURCE_MAX_ITEMS = 8  # 最多8个引用来源
    TOOL_CTX_MAX_ITEMS = 8  # 最多8条工具上下文
    MEMORY_CTX_MAX_ITEMS = 6  # 最多召回6条记忆
    INTERMEDIATE_MAX_ITEMS = 20  # 中间步骤最多20条

    def __init__(self, redis_client=None, db: Optional[Session] = None):
        self.redis_client = redis_client
        self.db = db
        self._local_runs: Dict[str, Dict[str, str]] = {}
        self._local_memories: Dict[str, List[dict]] = {}
        self._ensure_db_tables()

    def _ensure_db_tables(self):
        if not self.db or AgentStateStore.DB_TABLES_READY:
            return
        try:
            bind = getattr(self.db, "bind", None)
            if not bind:
                return
            AgentRun.__table__.create(bind=bind, checkfirst=True)
            AgentRunCheckpoint.__table__.create(bind=bind, checkfirst=True)
            AgentMemoryRecord.__table__.create(bind=bind, checkfirst=True)
            AgentStateStore.DB_TABLES_READY = True
        except Exception as e:
            logger.warning(f"AgentStateStore 初始化数据库表失败: {e}")

    @staticmethod
    def create_run_id(session_id: str) -> str:
        ts = int(time.time() * 1000)
        suffix = uuid.uuid4().hex[:8]
        return f"{session_id}:{ts}:{suffix}"

    @staticmethod
    def _safe_user_id(user_id: Optional[int | str]) -> str:
        if user_id in (None, ""):
            return "anonymous"
        return str(user_id)

    @classmethod
    def _build_memory_key(cls, user_id: Optional[int | str]) -> str:
        return cls.MEMORY_KEY.format(user_id=cls._safe_user_id(user_id))

    @classmethod
    def _build_run_key(cls, run_id: str) -> str:
        return cls.RUN_KEY.format(run_id=run_id)

    @classmethod
    def _build_timeline_key(cls, run_id: str) -> str:
        return cls.RUN_TIMELINE_KEY.format(run_id=run_id)

    @classmethod
    def _build_last_run_key(cls, user_id: Optional[int | str], session_id: str) -> str:
        return cls.SESSION_LAST_RUN_KEY.format(
            user_id=cls._safe_user_id(user_id),
            session_id=str(session_id or "default"),
        )

    @staticmethod
    def _serialize_message(message: Any) -> dict:
        content = getattr(message, "content", "")
        if isinstance(message, HumanMessage):
            return {"kind": "human", "content": content}
        if isinstance(message, AIMessage):
            return {
                "kind": "ai",
                "content": content,
                "tool_calls": getattr(message, "tool_calls", None) or [],
            }
        if isinstance(message, ToolMessage):
            return {
                "kind": "tool",
                "content": content,
                "tool_call_id": getattr(message, "tool_call_id", "") or "",
            }
        if isinstance(message, SystemMessage):
            return {"kind": "system", "content": content}
        return {"kind": "unknown", "content": str(content)}

    @classmethod
    def _deserialize_message(cls, item: Any):
        if not isinstance(item, dict):
            return HumanMessage(content=str(item))

        kind = str(item.get("kind", "human")).lower()
        content = str(item.get("content", ""))
        if kind == "ai":
            try:
                return AIMessage(content=content, tool_calls=item.get("tool_calls") or [])
            except TypeError:
                return AIMessage(content=content)
        if kind == "tool":
            tool_call_id = str(item.get("tool_call_id") or "recovered_tool_call")
            return ToolMessage(content=content, tool_call_id=tool_call_id)
        if kind == "system":
            return SystemMessage(content=content)
        return HumanMessage(content=content)

    @classmethod
    def _sanitize_value(cls, value: Any) -> Any:
        if value is None or isinstance(value, (str, int, float, bool)):
            return value
        if isinstance(value, (HumanMessage, AIMessage, ToolMessage, SystemMessage)):
            return cls._serialize_message(value)
        if isinstance(value, dict):
            return {str(k): cls._sanitize_value(v) for k, v in value.items()}
        if isinstance(value, (list, tuple)):
            return [cls._sanitize_value(v) for v in value]
        return str(value)

    @staticmethod
    def _truncate_text(text: Any, max_chars: int) -> str:
        raw = str(text or "")
        if max_chars <= 0 or len(raw) <= max_chars:
            return raw
        return raw[: max_chars - 3] + "..."

    @classmethod
    def _trim_list(cls, values: Any, max_items: int) -> list:
        if not isinstance(values, list):
            return []
        if max_items <= 0 or len(values) <= max_items:
            return values
        return values[-max_items:]

    @classmethod
    def _compact_messages(cls, messages: Any, max_items: int, max_chars: int) -> list:
        compacted = []
        for item in cls._trim_list(messages if isinstance(messages, list) else [], max_items):
            if not isinstance(item, dict):
                continue
            kind = str(item.get("kind", "unknown") or "unknown")
            content_cap = max_chars if kind != "tool" else min(max_chars, 320)
            compact = {
                "kind": kind,
                "content": cls._truncate_text(item.get("content", ""), content_cap),
            }
            tool_call_id = str(item.get("tool_call_id") or "").strip()
            if tool_call_id:
                compact["tool_call_id"] = cls._truncate_text(tool_call_id, 120)
            if kind == "ai" and isinstance(item.get("tool_calls"), list):
                compact["tool_calls"] = cls._trim_list(item.get("tool_calls"), 4)
            compacted.append(compact)
        return compacted

    @classmethod
    def _compact_sources(cls, sources: Any, max_items: int, content_chars: int) -> list:
        compacted = []
        for item in cls._trim_list(sources if isinstance(sources, list) else [], max_items):
            if not isinstance(item, dict):
                continue
            compacted.append(
                {
                    "type": cls._truncate_text(item.get("type", ""), 24),
                    "title": cls._truncate_text(item.get("title", ""), 120),
                    "label": cls._truncate_text(item.get("label", ""), 120),
                    "law_name": cls._truncate_text(item.get("law_name", ""), 120),
                    "article_no": cls._truncate_text(item.get("article_no", ""), 80),
                    "content": cls._truncate_text(item.get("content", ""), content_chars),
                }
            )
        return compacted

    @classmethod
    def _compact_scratchpad(cls, value: Any, max_depth: int = 3):
        if max_depth <= 0:
            return None
        if value is None or isinstance(value, (int, float, bool)):
            return value
        if isinstance(value, str):
            return cls._truncate_text(value, 320)
        if isinstance(value, list):
            return [
                cls._compact_scratchpad(v, max_depth - 1)
                for v in cls._trim_list(value, 8)
            ]
        if isinstance(value, dict):
            compacted = {}
            for index, (k, v) in enumerate(value.items()):
                if index >= 20:
                    break
                compacted[str(k)] = cls._compact_scratchpad(v, max_depth - 1)
            return compacted
        return cls._truncate_text(value, 320)

    @classmethod
    def _compact_state_for_checkpoint(cls, state: dict, aggressive: bool = False) -> dict:
        if not isinstance(state, dict):
            return {}

        def _to_int(value: Any, default: int = 0) -> int:
            try:
                return int(value)
            except (TypeError, ValueError):
                return default

        msg_max = 6 if aggressive else cls.MSG_MAX_ITEMS
        hist_max = 6 if aggressive else cls.HISTORY_MSG_MAX_ITEMS
        doc_max = 4 if aggressive else cls.DOC_MAX_ITEMS
        source_max = 4 if aggressive else cls.SOURCE_MAX_ITEMS
        tool_ctx_max = 4 if aggressive else cls.TOOL_CTX_MAX_ITEMS
        mem_ctx_max = 3 if aggressive else cls.MEMORY_CTX_MAX_ITEMS
        msg_chars = 360 if aggressive else 800
        doc_chars = 360 if aggressive else 1200
        source_chars = 320 if aggressive else 900
        ctx_chars = 280 if aggressive else 700

        compacted = {
            "run_id": cls._truncate_text(state.get("run_id", ""), 120),
            "session_id": cls._truncate_text(state.get("session_id", ""), 80),
            "user_id": cls._truncate_text(state.get("user_id", ""), 64),
            "checkpoint_recovered": bool(state.get("checkpoint_recovered", False)),
            "original_query": cls._truncate_text(state.get("original_query", ""), 1200),
            "search_query": cls._truncate_text(state.get("search_query", ""), 1200),
            "memory_summary": cls._truncate_text(state.get("memory_summary", ""), 1200),
            "generation": cls._truncate_text(state.get("generation", ""), 2200 if aggressive else 6000),
            "hallucination_passed": bool(state.get("hallucination_passed", True)),
            "relevance_retries": _to_int(state.get("relevance_retries", 0), default=0),
            "hallucination_retries": _to_int(state.get("hallucination_retries", 0), default=0),
            "resume_from_node": cls._truncate_text(state.get("resume_from_node", ""), 80),
            "messages": cls._compact_messages(state.get("messages", []), msg_max, msg_chars),
            "history_messages": cls._compact_messages(state.get("history_messages", []), hist_max, msg_chars),
            "private_documents": [
                cls._truncate_text(text, doc_chars)
                for text in cls._trim_list(state.get("private_documents", []), doc_max)
                if str(text or "").strip()
            ],
            "public_sources": cls._compact_sources(state.get("public_sources", []), source_max, source_chars),
            "private_tool_contexts": [
                cls._truncate_text(text, ctx_chars)
                for text in cls._trim_list(state.get("private_tool_contexts", []), tool_ctx_max)
                if str(text or "").strip()
            ],
            "private_memory_contexts": [
                cls._truncate_text(text, 260 if aggressive else 500)
                for text in cls._trim_list(state.get("private_memory_contexts", []), mem_ctx_max)
                if str(text or "").strip()
            ],
            "private_latest_tool_names": [
                cls._truncate_text(name, 64)
                for name in cls._trim_list(state.get("private_latest_tool_names", []), 8)
                if str(name or "").strip()
            ],
            "handoff_router": cls._compact_scratchpad(state.get("handoff_router", {})),
            "handoff_law": cls._compact_scratchpad(state.get("handoff_law", {})),
            "handoff_tool": cls._compact_scratchpad(state.get("handoff_tool", {})),
            "handoff_synth": cls._compact_scratchpad(state.get("handoff_synth", {})),
            "handoff_judge": cls._compact_scratchpad(state.get("handoff_judge", {})),
            "router_scratchpad": cls._compact_scratchpad(state.get("router_scratchpad", {})),
            "law_scratchpad": cls._compact_scratchpad(state.get("law_scratchpad", {})),
            "tool_scratchpad": cls._compact_scratchpad(state.get("tool_scratchpad", {})),
            "synth_scratchpad": cls._compact_scratchpad(state.get("synth_scratchpad", {})),
            "judge_scratchpad": cls._compact_scratchpad(state.get("judge_scratchpad", {})),
            "intermediate_steps": [
                cls._truncate_text(step, 80)
                for step in cls._trim_list(state.get("intermediate_steps", []), cls.INTERMEDIATE_MAX_ITEMS)
                if str(step or "").strip()
            ],
        }
        compacted["checkpoint_meta"] = {
            "compacted": True,
            "aggressive": aggressive,
            "max_bytes": cls.DB_STATE_JSON_MAX_BYTES,
        }
        return compacted

    @classmethod
    def _serialize_checkpoint_state(cls, state: dict) -> str:
        compacted = cls._compact_state_for_checkpoint(state, aggressive=False)
        payload = json.dumps(compacted, ensure_ascii=False)
        if len(payload.encode("utf-8")) <= cls.DB_STATE_JSON_MAX_BYTES:
            return payload

        compacted = cls._compact_state_for_checkpoint(state, aggressive=True)
        payload = json.dumps(compacted, ensure_ascii=False)
        if len(payload.encode("utf-8")) <= cls.DB_STATE_JSON_MAX_BYTES:
            return payload

        minimal = {
            "run_id": cls._truncate_text(state.get("run_id", ""), 120),
            "session_id": cls._truncate_text(state.get("session_id", ""), 80),
            "user_id": cls._truncate_text(state.get("user_id", ""), 64),
            "original_query": cls._truncate_text(state.get("original_query", ""), 800),
            "search_query": cls._truncate_text(state.get("search_query", ""), 800),
            "generation": cls._truncate_text(state.get("generation", ""), 1200),
            "messages": cls._compact_messages(state.get("messages", []), 4, 220),
            "public_sources": cls._compact_sources(state.get("public_sources", []), 2, 180),
            "private_documents": [
                cls._truncate_text(text, 180)
                for text in cls._trim_list(state.get("private_documents", []), 2)
                if str(text or "").strip()
            ],
            "private_tool_contexts": [
                cls._truncate_text(text, 180)
                for text in cls._trim_list(state.get("private_tool_contexts", []), 2)
                if str(text or "").strip()
            ],
            "hallucination_passed": bool(state.get("hallucination_passed", True)),
            "checkpoint_meta": {
                "compacted": True,
                "aggressive": True,
                "fallback_minimal": True,
                "max_bytes": cls.DB_STATE_JSON_MAX_BYTES,
            },
        }
        payload = json.dumps(minimal, ensure_ascii=False)
        payload_bytes = payload.encode("utf-8")
        if len(payload_bytes) <= cls.DB_STATE_JSON_MAX_BYTES:
            return payload

        ultra_minimal = {
            "run_id": cls._truncate_text(state.get("run_id", ""), 120),
            "session_id": cls._truncate_text(state.get("session_id", ""), 80),
            "user_id": cls._truncate_text(state.get("user_id", ""), 64),
            "checkpoint_meta": {
                "compacted": True,
                "aggressive": True,
                "fallback_minimal": True,
                "truncated": True,
                "max_bytes": cls.DB_STATE_JSON_MAX_BYTES,
            },
        }
        return json.dumps(ultra_minimal, ensure_ascii=False)

    @classmethod
    def _restore_state(cls, state: dict) -> dict:
        restored = dict(state or {})
        restored["messages"] = [
            cls._deserialize_message(item)
            for item in restored.get("messages", []) or []
        ]
        restored["history_messages"] = [
            cls._deserialize_message(item)
            for item in restored.get("history_messages", []) or []
        ]
        return restored

    @staticmethod
    def _json_loads_or_empty(raw: Any, default: Any):
        if not raw:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return default

    def start_run(self, run_id: str, user_id: Optional[int | str], session_id: str, query: str):
        safe_user_id = self._safe_user_id(user_id)
        safe_session_id = str(session_id or "default")
        payload = {
            "run_id": run_id,
            "user_id": safe_user_id,
            "session_id": safe_session_id,
            "query": str(query or "").strip(),
            "status": "running",
            "updated_at": str(int(time.time())),
            "last_node": "",
            "phase": "start",
        }
        run_key = self._build_run_key(run_id)
        timeline_key = self._build_timeline_key(run_id)
        last_run_key = self._build_last_run_key(user_id, session_id)

        if self.redis_client:
            self.redis_client.hset(run_key, mapping=payload)
            self.redis_client.expire(run_key, self.CHECKPOINT_TTL_SECONDS)
            self.redis_client.setex(last_run_key, self.CHECKPOINT_TTL_SECONDS, run_id)
            self.redis_client.lpush(timeline_key, json.dumps(payload, ensure_ascii=False))
            self.redis_client.ltrim(timeline_key, 0, self.TIMELINE_MAX_ITEMS - 1)
            self.redis_client.expire(timeline_key, self.CHECKPOINT_TTL_SECONDS)
            return

        self._local_runs[run_id] = payload

        if self.db:
            try:
                run = self.db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
                if not run:
                    run = AgentRun(run_id=run_id)
                    self.db.add(run)
                run.user_id = safe_user_id
                run.session_id = safe_session_id
                run.query = str(query or "").strip()
                run.status = "running"
                run.phase = "start"
                run.last_node = ""
                run.error = ""
                run.checkpoint_count = 0
                run.recovered = False
                run.started_at = datetime.now()
                run.updated_at = datetime.now()
                run.finished_at = None
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.warning(f"start_run 写入数据库失败: {e}")

    def save_checkpoint(
        self,
        run_id: str,
        node_name: str,
        state: dict,
        phase: str = "end",
        status: str = "running",
        error: str = "",
    ):
        if not run_id:
            return

        cleaned_state = self._sanitize_value(state or {})
        serialized_state = self._serialize_checkpoint_state(cleaned_state)
        payload = {
            "run_id": run_id,
            "last_node": str(node_name or ""),
            "phase": str(phase or "end"),
            "status": str(status or "running"),
            "updated_at": str(int(time.time())),
            "error": str(error or ""),
            "state_json": serialized_state,
        }
        run_key = self._build_run_key(run_id)
        timeline_key = self._build_timeline_key(run_id)

        if self.redis_client:
            self.redis_client.hset(run_key, mapping=payload)
            self.redis_client.expire(run_key, self.CHECKPOINT_TTL_SECONDS)
            self.redis_client.lpush(timeline_key, json.dumps(payload, ensure_ascii=False))
            self.redis_client.ltrim(timeline_key, 0, self.TIMELINE_MAX_ITEMS - 1)
            self.redis_client.expire(timeline_key, self.CHECKPOINT_TTL_SECONDS)
        else:
            prev = self._local_runs.get(run_id, {})
            prev.update(payload)
            self._local_runs[run_id] = prev

        if self.db:
            try:
                safe_user_id = self._safe_user_id((state or {}).get("user_id"))
                safe_session_id = str((state or {}).get("session_id") or "default")
                checkpoint = AgentRunCheckpoint(
                    run_id=run_id,
                    user_id=safe_user_id,
                    session_id=safe_session_id,
                    node_name=str(node_name or ""),
                    phase=str(phase or "end"),
                    status=str(status or "running"),
                    error=str(error or ""),
                    state_json=payload["state_json"],
                )
                self.db.add(checkpoint)

                run = self.db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
                if not run:
                    run = AgentRun(
                        run_id=run_id,
                        user_id=safe_user_id,
                        session_id=safe_session_id,
                        query=str((state or {}).get("original_query", "") or ""),
                        started_at=datetime.now(),
                    )
                    self.db.add(run)
                run.last_node = str(node_name or "")
                run.phase = str(phase or "end")
                run.status = str(status or "running")
                run.error = str(error or "")
                run.updated_at = datetime.now()
                run.checkpoint_count = int(run.checkpoint_count or 0) + 1
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.warning(f"save_checkpoint 写入数据库失败: {e}")

    def load_latest_checkpoint(self, run_id: str) -> dict:
        if not run_id:
            return {}

        run_key = self._build_run_key(run_id)
        if self.redis_client:
            data = self.redis_client.hgetall(run_key) or {}
            if isinstance(data, dict) and data:
                return data

        local_data = dict(self._local_runs.get(run_id, {}))
        if local_data:
            return local_data

        if self.db:
            try:
                run = self.db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
                if not run:
                    return {}
                latest_cp = (
                    self.db.query(AgentRunCheckpoint)
                    .filter(AgentRunCheckpoint.run_id == run_id)
                    .order_by(AgentRunCheckpoint.id.desc())
                    .first()
                )
                return {
                    "run_id": run.run_id,
                    "last_node": run.last_node or "",
                    "phase": run.phase or "",
                    "status": run.status or "",
                    "updated_at": str(int(run.updated_at.timestamp())) if run.updated_at else str(int(time.time())),
                    "error": run.error or "",
                    "state_json": latest_cp.state_json if latest_cp else "{}",
                }
            except Exception as e:
                logger.warning(f"load_latest_checkpoint 从数据库读取失败: {e}")

        return {}

    def load_state_snapshot(self, run_id: str) -> dict:
        latest = self.load_latest_checkpoint(run_id)
        state_json = latest.get("state_json", "")
        state_data = self._json_loads_or_empty(state_json, {})
        if not isinstance(state_data, dict):
            return {}
        return self._restore_state(state_data)

    def finish_run(
        self,
        run_id: str,
        status: str = "finished",
        error: str = "",
        recovered: bool = False,
        answer_length: int = 0,
        sources_count: int = 0,
    ):
        latest = self.load_latest_checkpoint(run_id)
        if not latest:
            return

        latest["status"] = status
        latest["error"] = str(error or "")
        latest["updated_at"] = str(int(time.time()))
        run_key = self._build_run_key(run_id)
        if self.redis_client:
            self.redis_client.hset(run_key, mapping=latest)
            self.redis_client.expire(run_key, self.CHECKPOINT_TTL_SECONDS)
        else:
            self._local_runs[run_id] = latest

        if self.db:
            try:
                run = self.db.query(AgentRun).filter(AgentRun.run_id == run_id).first()
                if run:
                    run.status = str(status or "finished")
                    run.error = str(error or "")
                    run.updated_at = datetime.now()
                    run.finished_at = datetime.now()
                    run.recovered = bool(recovered)
                    run.answer_length = int(answer_length or 0)
                    run.sources_count = int(sources_count or 0)
                    self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.warning(f"finish_run 写入数据库失败: {e}")

    @staticmethod
    def _extract_high_value_memories(query: str, answer: str) -> List[dict]:
        query = str(query or "").strip()
        answer = str(answer or "").strip()
        now_ts = int(time.time())
        items: List[dict] = []

        preference_pattern = re.compile(r"(偏好|喜欢|希望|请用|尽量|优先|不要|别用)", re.IGNORECASE)
        fact_pattern = re.compile(r"(我在|我现在在|我是|我的|位于|公司|家庭|车型|车牌)", re.IGNORECASE)
        conclusion_pattern = re.compile(r"(结论|综上|建议|因此|应当|可以|不可以|处罚|罚款|扣分)", re.IGNORECASE)

        if query and preference_pattern.search(query):
            items.append({"type": "preference", "text": query, "ts": now_ts})
        if query and fact_pattern.search(query):
            items.append({"type": "fact", "text": query, "ts": now_ts})

        if answer:
            sentences = re.split(r"[。！？!?;\n]", answer)
            for sentence in sentences:
                text = sentence.strip()
                if not text:
                    continue
                if conclusion_pattern.search(text):
                    items.append({"type": "conclusion", "text": text[:300], "ts": now_ts})
                if len(items) >= 6:
                    break

        dedup: Dict[str, dict] = {}
        for item in items:
            normalized = re.sub(r"\s+", "", item["text"]).lower()
            if normalized and normalized not in dedup:
                dedup[normalized] = item
        return list(dedup.values())

    @staticmethod
    def _tokenize(text: str) -> set:
        text = str(text or "").lower()
        chunks = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", text)
        tokens = set(chunks)
        for chunk in chunks:
            if re.fullmatch(r"[\u4e00-\u9fff]+", chunk):
                if len(chunk) <= 2:
                    tokens.add(chunk)
                    continue
                for i in range(len(chunk) - 1):
                    tokens.add(chunk[i : i + 2])
        return tokens

    def upsert_long_term_memories(
        self,
        user_id: Optional[int | str],
        session_id: str,
        query: str,
        answer: str,
        run_id: str = "",
    ) -> List[dict]:
        """抽取并持久化高价值记忆（偏好/事实/结论）。"""
        safe_user_id = self._safe_user_id(user_id)
        safe_session_id = str(session_id or "default")
        key = self._build_memory_key(safe_user_id)
        fresh_items = self._extract_high_value_memories(query, answer)
        if not fresh_items:
            return []

        if self.redis_client:
            existing = self._json_loads_or_empty(self.redis_client.get(key), [])
        else:
            existing = list(self._local_memories.get(key, []))

        if not isinstance(existing, list):
            existing = []

        normalized_seen = {
            re.sub(r"\s+", "", str(item.get("text", ""))).lower()
            for item in existing
            if isinstance(item, dict)
        }
        appended: List[dict] = []
        for item in fresh_items:
            normalized = re.sub(r"\s+", "", item["text"]).lower()
            if not normalized or normalized in normalized_seen:
                continue
            record = {
                "id": uuid.uuid4().hex[:12],
                "type": item["type"],
                "text": item["text"],
                "session_id": safe_session_id,
                "user_id": safe_user_id,
                "run_id": str(run_id or ""),
                "created_at": int(time.time()),
            }
            existing.append(record)
            normalized_seen.add(normalized)
            appended.append(record)

        if len(existing) > self.MEMORY_MAX_ITEMS:
            existing = existing[-self.MEMORY_MAX_ITEMS :]

        payload = json.dumps(existing, ensure_ascii=False)
        if self.redis_client:
            self.redis_client.setex(key, self.MEMORY_TTL_SECONDS, payload)
        else:
            self._local_memories[key] = existing

        if self.db and appended:
            try:
                for item in appended:
                    self.db.add(
                        AgentMemoryRecord(
                            user_id=safe_user_id,
                            session_id=safe_session_id,
                            run_id=str(run_id or None) if str(run_id or "").strip() else None,
                            memory_type=str(item.get("type", "memory") or "memory"),
                            memory_text=str(item.get("text", "") or ""),
                            created_at=datetime.now(),
                        )
                    )
                self.db.commit()
            except Exception as e:
                self.db.rollback()
                logger.warning(f"upsert_long_term_memories 写入数据库失败: {e}")

        return appended

    def recall_long_term_memories(
        self,
        user_id: Optional[int | str],
        query: str,
        limit: int = 3,
    ) -> List[dict]:
        safe_user_id = self._safe_user_id(user_id)
        key = self._build_memory_key(safe_user_id)
        if self.redis_client:
            memories = self._json_loads_or_empty(self.redis_client.get(key), [])
        else:
            memories = list(self._local_memories.get(key, []))

        if (not isinstance(memories, list) or not memories) and self.db:
            try:
                rows = (
                    self.db.query(AgentMemoryRecord)
                    .filter(AgentMemoryRecord.user_id == safe_user_id)
                    .order_by(AgentMemoryRecord.id.desc())
                    .limit(self.MEMORY_MAX_ITEMS)
                    .all()
                )
                memories = [
                    {
                        "id": str(row.id),
                        "type": row.memory_type,
                        "text": row.memory_text,
                        "session_id": row.session_id,
                        "run_id": row.run_id or "",
                        "created_at": int(row.created_at.timestamp()) if row.created_at else int(time.time()),
                    }
                    for row in rows
                ]
            except Exception as e:
                logger.warning(f"recall_long_term_memories 从数据库读取失败: {e}")

        if not isinstance(memories, list) or not memories:
            return []

        query_tokens = self._tokenize(query)
        scored: List[dict] = []
        now_ts = int(time.time())
        for item in memories:
            if not isinstance(item, dict):
                continue
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            item_tokens = self._tokenize(text)
            overlap = len(query_tokens & item_tokens)
            recency = 0.0
            created_at = int(item.get("created_at", now_ts) or now_ts)
            age_hours = max((now_ts - created_at) / 3600.0, 0.0)
            recency = max(0.0, 1.0 - age_hours / (24.0 * 14.0))
            score = float(overlap) + recency
            if overlap > 0:
                scored.append({**item, "score": score})

        if not scored:
            fallback = [m for m in memories if isinstance(m, dict)][-limit:]
            return list(reversed(fallback))

        scored.sort(key=lambda x: x.get("score", 0.0), reverse=True)
        return scored[: max(limit, 1)]

    def recall_memory_contexts(
        self,
        user_id: Optional[int | str],
        query: str,
        limit: int = 3,
    ) -> List[str]:
        records = self.recall_long_term_memories(user_id=user_id, query=query, limit=limit)
        contexts = []
        for item in records:
            m_type = str(item.get("type", "memory")).strip() or "memory"
            text = str(item.get("text", "")).strip()
            if not text:
                continue
            contexts.append(f"[长期记忆-{m_type}] {text}")
        return contexts
