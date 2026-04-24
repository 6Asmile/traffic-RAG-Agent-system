import json
from typing import Any, Dict, List, Literal, Optional

from langchain_core.messages import AIMessage, HumanMessage


HistoryRole = Literal["user", "assistant"]
HistoryEntry = Dict[str, str]


def build_scoped_redis_key(template: str, session_id: str, user_id: Optional[int | str] = None) -> str:
    """统一构建会话键，优先使用 user_id + session_id 进行隔离。"""
    safe_session_id = str(session_id or "default")
    safe_user_id = str(user_id) if user_id not in (None, "") else "anonymous"

    try:
        return template.format(user_id=safe_user_id, session_id=safe_session_id)
    except KeyError:
        # 兼容旧模板（仅包含 session_id）
        return template.format(session_id=safe_session_id)


def _normalize_role(role: Optional[str], fallback_role: HistoryRole) -> HistoryRole:
    normalized = str(role or "").strip().lower()
    if normalized in {"assistant", "ai", "bot"}:
        return "assistant"
    if normalized in {"user", "human"}:
        return "user"
    return fallback_role


def _extract_role_and_content(raw_item: Any, fallback_role: HistoryRole) -> Optional[HistoryEntry]:
    if isinstance(raw_item, dict):
        role = _normalize_role(raw_item.get("role"), fallback_role)
        content = str(raw_item.get("content", "")).strip()
        if content:
            return {"role": role, "content": content}
        return None

    text = str(raw_item or "").strip()
    if not text:
        return None

    lowered = text.lower()
    if lowered.startswith("用户:") or lowered.startswith("user:"):
        return {"role": "user", "content": text.split(":", 1)[1].strip()}
    if lowered.startswith("助手:") or lowered.startswith("assistant:"):
        return {"role": "assistant", "content": text.split(":", 1)[1].strip()}

    return {"role": fallback_role, "content": text}


def load_history_entries(redis_client, history_key: str, max_turns: int) -> List[HistoryEntry]:
    if not redis_client:
        return []

    raw = redis_client.get(history_key)
    if not raw:
        return []

    try:
        stored_items = json.loads(raw)
    except json.JSONDecodeError:
        return []

    if not isinstance(stored_items, list):
        return []

    normalized_entries: List[HistoryEntry] = []
    for index, item in enumerate(stored_items):
        fallback_role: HistoryRole = "user" if index % 2 == 0 else "assistant"
        normalized = _extract_role_and_content(item, fallback_role)
        if normalized:
            normalized_entries.append(normalized)

    return normalized_entries[-max_turns * 2:]


def load_history_summary(redis_client, summary_key: str) -> str:
    if not redis_client:
        return ""

    raw = redis_client.get(summary_key)
    if not raw:
        return ""
    return str(raw).strip()


def append_history_entries(
    history_entries: List[HistoryEntry],
    user_query: str,
    assistant_answer: str,
    max_turns: int,
) -> List[HistoryEntry]:
    next_entries = list(history_entries)
    if user_query.strip():
        next_entries.append({"role": "user", "content": user_query.strip()})
    if assistant_answer.strip():
        next_entries.append({"role": "assistant", "content": assistant_answer.strip()})
    return next_entries[-max_turns * 2:]


def maybe_compact_history_entries(
    history_entries: List[HistoryEntry],
    trigger_turns: int,
    keep_turns: int,
) -> tuple[List[HistoryEntry], List[HistoryEntry]]:
    """超过阈值时压缩历史：保留最近 keep_turns，较老内容交给摘要层。"""
    trigger_count = max(trigger_turns, 1) * 2
    if len(history_entries) <= trigger_count:
        return history_entries, []

    keep_count = max(keep_turns, 1) * 2
    if len(history_entries) <= keep_count:
        return history_entries, []

    archived_entries = history_entries[:-keep_count]
    compacted_entries = history_entries[-keep_count:]
    return compacted_entries, archived_entries


def dump_history_entries(history_entries: List[HistoryEntry]) -> str:
    return json.dumps(history_entries, ensure_ascii=False)


def merge_history_summary(existing_summary: str, archived_entries: List[HistoryEntry], max_chars: int = 1800) -> str:
    """将旧摘要与新归档历史合并，生成下一版摘要文本（轻量无模型版）。"""
    archived_text = render_history_text(archived_entries).strip()
    parts = [p for p in [existing_summary.strip(), archived_text] if p]
    if not parts:
        return existing_summary.strip()

    merged = "\n".join(parts).strip()
    if len(merged) <= max_chars:
        return merged
    return merged[-max_chars:]


def build_langchain_messages(history_entries: List[HistoryEntry]) -> List[Any]:
    messages = []
    for entry in history_entries:
        if entry["role"] == "user":
            messages.append(HumanMessage(content=entry["content"]))
        else:
            messages.append(AIMessage(content=entry["content"]))
    return messages


def render_history_text(history_entries: List[HistoryEntry]) -> str:
    return "\n".join(
        f"{'用户' if entry['role'] == 'user' else '助手'}: {entry['content']}"
        for entry in history_entries
    )


def render_history_context(history_entries: List[HistoryEntry], summary_text: str) -> str:
    """组合“摘要记忆 + 近期对话”，供改写与生成阶段统一使用。"""
    recent_text = render_history_text(history_entries).strip()
    summary_text = str(summary_text or "").strip()
    if summary_text and recent_text:
        return f"【历史摘要】\n{summary_text}\n\n【近期对话】\n{recent_text}"
    return summary_text or recent_text
