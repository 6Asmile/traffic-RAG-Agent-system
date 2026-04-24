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
    MEMORY_MAX_ITEMS = 80
    TIMELINE_MAX_ITEMS = 200
    DB_TABLES_READY = False

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
        payload = {
            "run_id": run_id,
            "last_node": str(node_name or ""),
            "phase": str(phase or "end"),
            "status": str(status or "running"),
            "updated_at": str(int(time.time())),
            "error": str(error or ""),
            "state_json": json.dumps(cleaned_state, ensure_ascii=False),
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
