# app/services/agentic/agentic_workflow.py

import asyncio
import json
import logging
import os
import re
import time
from typing import Any, Literal

from langchain_core.messages import HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, StateGraph

from app.core.agentic.agent_constants import (
    AgentToolNames,
    GraderThresholds,
    NodeNames,
    RAGToolConfig,
    UIEventTypes,
)
from app.core.agentic.agent_prompts import ExpertPrompts
from app.services.agentic.agentic_state import AgenticState
from app.services.agentic.state_store import AgentStateStore
from app.services.agentic.subgraphs import (
    JudgeAgentSubgraph,
    LawAgentSubgraph,
    RouterAgentSubgraph,
    SynthAgentSubgraph,
    ToolAgentSubgraph,
)

logger = logging.getLogger(__name__)


class AgenticWorkflowManager:
    def __init__(
        self,
        llm,
        json_llm,
        tools_list,
        vector_db,
        bm25_instance,
        bm25_corpus,
        reranker,
        state_store: AgentStateStore | None = None,
        tool_metadata_by_name: dict[str, dict[str, Any]] | None = None,
        capability_hints: dict[str, list[str]] | None = None,
        workflow_manifest_path: str | None = None,
    ):
        self.llm = llm
        self.json_llm = json_llm
        self.state_store = state_store

        self.tools = {t.name: t for t in tools_list}
        self.tool_metadata_by_name = dict(tool_metadata_by_name or {})
        self.capability_hints = dict(capability_hints or {})
        self.workflow_manifest_path = workflow_manifest_path or os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "..", "core", "agentic", "workflow_manifest.json")
        )
        self.enabled_nodes: set[str] = set()

        self.available_capabilities = self._collect_available_capabilities()
        self.law_tool_names = self._find_law_tool_names()
        self.law_tool_name = self.law_tool_names[0] if self.law_tool_names else ""
        self.law_tool = self.tools.get(self.law_tool_name) if self.law_tool_name else None
        self.external_tool_names = self._find_planner_enabled_tool_names()
        self.external_tools = [self.tools[name] for name in self.external_tool_names if name in self.tools]
        self.tool_llm = self.llm.bind_tools(self.external_tools) if self.external_tools else None
        self.agent_policies = {
            "router": {"timeout_s": 12, "retries": 1, "breaker_threshold": 3, "cooldown_s": 20},
            "law": {"timeout_s": 15, "retries": 1, "breaker_threshold": 3, "cooldown_s": 30},
            "tool": {"timeout_s": 12, "retries": 1, "breaker_threshold": 3, "cooldown_s": 25},
            "synth": {"timeout_s": 18, "retries": 1, "breaker_threshold": 3, "cooldown_s": 20},
            "judge": {"timeout_s": 10, "retries": 1, "breaker_threshold": 3, "cooldown_s": 20},
        }
        self.agent_breakers = {
            key: {"failures": 0, "opened_until": 0.0}
            for key in self.agent_policies
        }

        # compatibility: 保留底层检索组件引用
        self.vector_db = vector_db
        self.bm25_instance = bm25_instance
        self.bm25_corpus = bm25_corpus
        self.reranker = reranker

        RouterAgentSubgraph.configure_capability_patterns(self.capability_hints)
        self.graph = self._build_graph()

    @staticmethod
    def _snapshot_with_updates(state: AgenticState, updates: dict) -> dict:
        snapshot = dict(state or {})
        snapshot.update(updates or {})
        return snapshot

    @staticmethod
    def _merge_unique_texts(base_items: list[str], extra_items: list[str]) -> list[str]:
        merged = []
        seen = set()
        for text in (base_items or []) + (extra_items or []):
            normalized = str(text or "").strip()
            if not normalized or normalized in seen:
                continue
            seen.add(normalized)
            merged.append(normalized)
        return merged

    def _save_node_checkpoint(
        self,
        state: AgenticState,
        node_name: str,
        phase: str,
        status: str = "running",
        error: str = "",
    ):
        if not self.state_store:
            return
        run_id = str(state.get("run_id", "") or "").strip()
        if not run_id:
            return
        self.state_store.save_checkpoint(
            run_id=run_id,
            node_name=node_name,
            state=dict(state or {}),
            phase=phase,
            status=status,
            error=error,
        )

    def _is_circuit_open(self, agent_key: str) -> bool:
        state = self.agent_breakers.get(agent_key, {"opened_until": 0.0})
        return float(state.get("opened_until", 0.0) or 0.0) > time.time()

    def _record_success(self, agent_key: str):
        if agent_key not in self.agent_breakers:
            return
        self.agent_breakers[agent_key] = {"failures": 0, "opened_until": 0.0}

    def _record_failure(self, agent_key: str):
        policy = self.agent_policies.get(agent_key, {})
        threshold = int(policy.get("breaker_threshold", 3))
        cooldown_s = int(policy.get("cooldown_s", 20))
        breaker = self.agent_breakers.get(agent_key, {"failures": 0, "opened_until": 0.0})
        failures = int(breaker.get("failures", 0)) + 1
        opened_until = float(breaker.get("opened_until", 0.0))
        if failures >= threshold:
            opened_until = time.time() + cooldown_s
            failures = 0
        self.agent_breakers[agent_key] = {"failures": failures, "opened_until": opened_until}

    async def _run_guarded(self, agent_key: str, op_name: str, coro_factory, policy_override: dict | None = None):
        policy = dict(self.agent_policies.get(agent_key, {}))
        if isinstance(policy_override, dict):
            policy.update(policy_override)
        timeout_s = int(policy.get("timeout_s", 10))
        retries = int(policy.get("retries", 0))
        metrics = {
            "agent": agent_key,
            "operation": op_name,
            "timeout_s": timeout_s,
            "max_retries": retries,
            "attempts": 0,
            "success": False,
            "error": "",
            "timed_out": False,
            "circuit_open": False,
            "duration_ms": 0,
        }

        if self._is_circuit_open(agent_key):
            metrics["circuit_open"] = True
            metrics["error"] = "circuit_open"
            return None, metrics, "circuit_open"

        last_error = ""
        for attempt in range(1, retries + 2):
            started = time.perf_counter()
            metrics["attempts"] = attempt
            try:
                result = await asyncio.wait_for(coro_factory(), timeout=timeout_s)
                metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
                metrics["success"] = True
                self._record_success(agent_key)
                return result, metrics, ""
            except asyncio.TimeoutError:
                last_error = f"timeout_{timeout_s}s"
                metrics["timed_out"] = True
                metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)
            except Exception as e:
                last_error = str(e)
                metrics["duration_ms"] = int((time.perf_counter() - started) * 1000)

        self._record_failure(agent_key)
        metrics["error"] = last_error
        return None, metrics, last_error

    def _extract_json_score(self, text: str, default: str = GraderThresholds.SCORE_NO) -> str:
        match = re.search(r"\{[\s\S]*\}", text)
        if match:
            try:
                data = json.loads(match.group())
                return str(data.get("score", default)).lower()
            except json.JSONDecodeError:
                pass
        return default

    @staticmethod
    def _extract_json_object(text: str, default: dict | None = None) -> dict:
        default = default or {}
        if not text:
            return default
        match = re.search(r"\{[\s\S]*\}", text)
        if not match:
            return default
        try:
            payload = json.loads(match.group())
            return payload if isinstance(payload, dict) else default
        except json.JSONDecodeError:
            return default

    def _get_tool_meta(self, tool_name: str) -> dict[str, Any]:
        return self.tool_metadata_by_name.get(str(tool_name or "").strip(), {}) or {}

    def _get_tool_capabilities(self, tool_name: str) -> list[str]:
        meta = self._get_tool_meta(tool_name)
        caps = meta.get("capabilities", [])
        if isinstance(caps, list):
            return [str(item).strip() for item in caps if str(item).strip()]
        return []

    def _get_tool_policy(self, tool_name: str) -> dict[str, Any]:
        meta = self._get_tool_meta(tool_name)
        policy = meta.get("policy", {})
        return policy if isinstance(policy, dict) else {}

    def _collect_available_capabilities(self) -> set[str]:
        caps: set[str] = set()
        for tool_name in self.tools:
            tool_caps = self._get_tool_capabilities(tool_name)
            for cap in tool_caps:
                normalized = str(cap).strip()
                if normalized:
                    caps.add(normalized)
        return caps

    def _find_law_tool_names(self) -> list[str]:
        names = []
        for tool_name in self.tools:
            if "law_search" in self._get_tool_capabilities(tool_name):
                names.append(tool_name)
        if not names and AgentToolNames.LAW_SEARCH in self.tools:
            names.append(AgentToolNames.LAW_SEARCH)
        return names

    def _find_planner_enabled_tool_names(self) -> list[str]:
        planner_tools = []
        for tool_name in self.tools:
            if tool_name in self.law_tool_names:
                continue
            meta = self._get_tool_meta(tool_name)
            if bool(meta.get("planner_enabled", True)):
                planner_tools.append(tool_name)
        return planner_tools

    def _resolve_tool_names_by_capabilities(
        self,
        capabilities: list[str],
        candidate_pool: list[str] | None = None,
    ) -> list[str]:
        pool = [name for name in (candidate_pool or self.external_tool_names) if name in self.tools]
        normalized_caps = [str(cap).strip() for cap in (capabilities or []) if str(cap).strip()]
        if not normalized_caps:
            return list(pool)

        resolved = []
        seen = set()
        for tool_name in pool:
            tool_caps = set(self._get_tool_capabilities(tool_name))
            if not tool_caps:
                continue
            if any(cap in tool_caps for cap in normalized_caps):
                if tool_name not in seen:
                    seen.add(tool_name)
                    resolved.append(tool_name)
        return resolved

    def _normalize_router_handoff(self, handoff: dict[str, Any]) -> dict[str, Any]:
        raw_caps = handoff.get("need_capabilities", [])
        caps = [str(cap).strip() for cap in (raw_caps or []) if str(cap).strip()]
        filtered_caps = [cap for cap in caps if cap in self.available_capabilities]

        need_law = "law_search" in filtered_caps
        need_tool = any(cap != "law_search" for cap in filtered_caps)
        if need_law and need_tool:
            task_type = "hybrid"
        elif need_law:
            task_type = "law_only"
        elif need_tool:
            task_type = "tool_only"
        else:
            task_type = "chat"

        reason_codes = [
            str(code).strip()
            for code in (handoff.get("reason_codes", []) or [])
            if str(code).strip()
        ]
        if not reason_codes and not filtered_caps:
            reason_codes = ["fallback_chat"]

        normalized = dict(handoff or {})
        normalized.update(
            {
                "need_capabilities": filtered_caps,
                "need_law": need_law,
                "need_tool": need_tool,
                "task_type": task_type,
                "reason_codes": reason_codes,
            }
        )
        return normalized

    def _build_tool_planner_prompt(self, query: str, tool_names: list[str]) -> str:
        catalog_lines = []
        for tool_name in tool_names:
            tool = self.tools.get(tool_name)
            if tool is None:
                continue
            meta = self._get_tool_meta(tool_name)
            desc = str(meta.get("description") or getattr(tool, "description", "") or "").strip()
            capabilities = ",".join(self._get_tool_capabilities(tool_name))
            input_schema = meta.get("input_schema", {})
            schema_json = json.dumps(input_schema, ensure_ascii=False) if isinstance(input_schema, dict) else "{}"
            catalog_lines.append(
                f"- tool={tool_name} | capabilities={capabilities or 'unknown'} | description={desc or '无'} | "
                f"input_schema={schema_json}"
            )

        catalog_text = "\n".join(catalog_lines) if catalog_lines else "- 无可用工具"
        allowed_names = ", ".join(tool_names) if tool_names else "无"
        return (
            "你是 tool_agent 参数规划器。"
            "请根据用户问题返回严格 JSON："
            '{"tasks":[{"tool":"工具名","args":{"参数名":"参数值"}}],"notes":["可选说明"]}。'
            "只允许使用白名单工具，禁止输出白名单外工具。"
            "当问题不需要外部工具时，tasks 返回空数组。"
            f"\n可用工具白名单: {allowed_names}"
            f"\n工具目录:\n{catalog_text}"
            f"\n用户问题: {query}"
        )

    def _validate_tool_args(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any] | None:
        tool = self.tools.get(tool_name)
        if tool is None:
            return None
        raw_args = args if isinstance(args, dict) else {}
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is None:
            return raw_args
        try:
            payload = args_schema(**raw_args)
            dump_fn = getattr(payload, "model_dump", None)
            if callable(dump_fn):
                return dump_fn()
            return raw_args
        except Exception:
            return None

    def _build_tool_plan(self, planner_output: str, allowed_tool_names: list[str]) -> list[dict]:
        payload = self._extract_json_object(planner_output, default={})
        tasks = payload.get("tasks", [])
        if not isinstance(tasks, list):
            return []

        allowed = set([str(name).strip() for name in (allowed_tool_names or []) if str(name).strip()])
        normalized_tasks = []
        for item in tasks:
            if not isinstance(item, dict):
                continue
            tool_name = str(item.get("tool") or "").strip()
            if not tool_name or (allowed and tool_name not in allowed):
                continue
            args = item.get("args") if isinstance(item.get("args"), dict) else {}
            validated_args = self._validate_tool_args(tool_name, args)
            if validated_args is None:
                continue
            normalized_tasks.append({"tool": tool_name, "args": validated_args})

        return normalized_tasks

    def _normalize_tool_result(self, raw_result) -> str:
        if raw_result is None:
            return ""
        result_text = str(raw_result)
        try:
            payload = json.loads(result_text)
            if isinstance(payload, dict):
                return str(payload.get("text_data") or payload.get("content") or "").strip()
        except json.JSONDecodeError:
            pass
        return result_text.strip()

    def _extract_law_sources(self, raw_result) -> list[dict]:
        if raw_result is None:
            return []

        result_text = str(raw_result)
        try:
            payload = json.loads(result_text)
        except json.JSONDecodeError:
            return []

        if not isinstance(payload, dict):
            return []

        sources = payload.get("sources", [])
        if not isinstance(sources, list):
            return []

        normalized_sources = []
        for index, item in enumerate(sources):
            if not isinstance(item, dict):
                continue
            content = str(item.get("content") or "").strip()
            if not content:
                continue
            normalized_sources.append(
                {
                    "type": item.get("type", "law"),
                    "title": str(item.get("title") or f"法规依据 {index + 1}").strip(),
                    "label": str(item.get("label") or f"检索片段 {index + 1}").strip(),
                    "law_name": str(item.get("law_name") or "").strip(),
                    "article_no": str(item.get("article_no") or "").strip(),
                    "content": content,
                }
            )
        return normalized_sources

    @staticmethod
    def _render_history(history_messages, max_turns: int = 4) -> str:
        if not history_messages:
            return ""
        sliced = list(history_messages[-(max_turns * 2) :])
        lines = []
        for msg in sliced:
            role = "用户" if isinstance(msg, HumanMessage) else "助手"
            content = getattr(msg, "content", "")
            if content:
                lines.append(f"{role}: {content}")
        return "\n".join(lines)

    @staticmethod
    def _compose_history_context(history_text: str, memory_summary: str) -> str:
        summary = str(memory_summary or "").strip()
        recent = str(history_text or "").strip()
        if summary and recent:
            return f"【历史摘要】\n{summary}\n\n【近期对话】\n{recent}"
        return summary or recent

    @staticmethod
    def _build_law_context(law_sources: list[dict], documents: list[str]) -> str:
        if law_sources:
            blocks = []
            for index, item in enumerate(law_sources):
                header = item.get("title") or item.get("label") or f"法规依据 {index + 1}"
                content = item.get("content", "")
                if not content:
                    continue
                blocks.append(f"[{header}]\n{content}")
            if blocks:
                return "\n\n".join(blocks)
        return "\n\n".join(documents).strip() or "无"

    def _recall_memory_contexts(self, state: AgenticState, query: str) -> list[str]:
        if not self.state_store:
            return []
        user_id = state.get("user_id", "")
        contexts = self.state_store.recall_memory_contexts(user_id=user_id, query=query, limit=3)
        return [item for item in contexts if str(item or "").strip()]

    async def node_bootstrap(self, state: AgenticState):
        self._save_node_checkpoint(state, NodeNames.BOOTSTRAP, phase="start")
        updates = {}
        self._save_node_checkpoint(
            self._snapshot_with_updates(state, updates),
            NodeNames.BOOTSTRAP,
            phase="end",
        )
        return updates

    async def node_router_agent(self, state: AgenticState):
        self._save_node_checkpoint(state, NodeNames.ROUTER_AGENT, phase="start")
        original_query = state.get("original_query", "")
        history_messages = state.get("history_messages", [])
        memory_summary = state.get("memory_summary", "")
        history_text = self._render_history(history_messages) if history_messages else ""
        history_context = self._compose_history_context(history_text, memory_summary)

        rewritten_query = original_query
        router_metrics = {}
        if history_context:
            result, router_metrics, err = await self._run_guarded(
                "router",
                "contextual_rewrite",
                lambda: self.llm.ainvoke(
                    ExpertPrompts.CONTEXTUAL_QUERY_REWRITER.format(
                        history=history_context,
                        question=original_query,
                    )
                ),
            )
            if result is not None and not err:
                resolved = str(result.content or "").strip()
                if resolved:
                    rewritten_query = resolved
            else:
                logger.warning(f"router_agent 指代消解失败，降级原始问题: {err}")
        else:
            router_metrics = {
                "agent": "router",
                "operation": "contextual_rewrite",
                "skipped": True,
                "reason": "history_empty",
            }

        handoff_router = RouterAgentSubgraph.build_handoff(
            query=original_query,
            rewritten_query=rewritten_query,
        )
        handoff_router = self._normalize_router_handoff(handoff_router)
        updates = {
            "search_query": handoff_router["rewritten_query"],
            "handoff_router": handoff_router,
            "router_scratchpad": {
                "history_chars": len(history_context),
                "task_type": handoff_router["task_type"],
                "need_capabilities": handoff_router.get("need_capabilities", []),
                "metrics": router_metrics,
            },
            "intermediate_steps": [UIEventTypes.ROUTING],
        }
        self._save_node_checkpoint(
            self._snapshot_with_updates(state, updates),
            NodeNames.ROUTER_AGENT,
            phase="end",
        )
        return updates

    async def node_law_agent(self, state: AgenticState):
        self._save_node_checkpoint(state, NodeNames.LAW_AGENT, phase="start")
        handoff_router = state.get("handoff_router", {}) or {}
        router_caps = [
            str(cap).strip()
            for cap in (handoff_router.get("need_capabilities", []) or [])
            if str(cap).strip()
        ]
        need_law = bool(handoff_router.get("need_law")) or ("law_search" in router_caps)
        query = str(state.get("search_query") or state.get("original_query") or "").strip()
        law_candidate_tools = self._resolve_tool_names_by_capabilities(["law_search"], candidate_pool=self.law_tool_names)

        if not need_law or not query:
            updates = {
                "handoff_law": LawAgentSubgraph.build_handoff([], [], confidence=0.0),
                "law_scratchpad": {
                    "skipped": True,
                    "reason": "need_law_false",
                    "metrics": {"agent": "law", "operation": "law_search", "skipped": True},
                },
                "intermediate_steps": [UIEventTypes.LAW_WORKING],
            }
            self._save_node_checkpoint(
                self._snapshot_with_updates(state, updates),
                NodeNames.LAW_AGENT,
                phase="end",
            )
            return updates

        if not law_candidate_tools:
            updates = {
                "handoff_law": LawAgentSubgraph.build_handoff([], [], confidence=0.0),
                "law_scratchpad": {
                    "skipped": True,
                    "reason": "law_tool_missing",
                    "metrics": {"agent": "law", "operation": "law_search", "skipped": True},
                    "candidate_tools": law_candidate_tools,
                },
                "intermediate_steps": [UIEventTypes.LAW_WORKING],
            }
            self._save_node_checkpoint(
                self._snapshot_with_updates(state, updates),
                NodeNames.LAW_AGENT,
                phase="end",
            )
            return updates

        law_docs = list(state.get("private_documents", []))
        law_sources: list[dict] = list(state.get("public_sources", []))
        memory_contexts = list(state.get("private_memory_contexts", []))
        try:
            selected_law_tool = law_candidate_tools[0]
            law_tool_obj = self.tools[selected_law_tool]
            law_policy = self._get_tool_policy(selected_law_tool)
            raw_result, law_metrics, law_err = await self._run_guarded(
                "law",
                f"law_search_{selected_law_tool}",
                lambda: law_tool_obj.ainvoke({"query": query}),
                policy_override=law_policy,
            )
            law_metrics["policy"] = law_policy
            law_metrics["selected_tool"] = selected_law_tool
            if raw_result is None and law_err:
                raise RuntimeError(law_err)
            extracted_sources = self._extract_law_sources(raw_result)
            if extracted_sources:
                law_sources = extracted_sources
                law_docs = [item["content"] for item in extracted_sources]
            else:
                normalized_result = self._normalize_tool_result(raw_result)
                if normalized_result and normalized_result != RAGToolConfig.FALLBACK_MESSAGE:
                    law_docs = [normalized_result]

            recalled_memories = self._recall_memory_contexts(state, query)
            memory_contexts = self._merge_unique_texts(memory_contexts, recalled_memories)
            law_docs = self._merge_unique_texts(law_docs, recalled_memories)

            handoff_law = LawAgentSubgraph.build_handoff(
                law_docs=law_docs,
                law_sources=law_sources,
                confidence=0.7 if law_sources else 0.4 if law_docs else 0.0,
            )
            updates = {
                "private_documents": law_docs,
                "public_sources": law_sources,
                "private_memory_contexts": memory_contexts,
                "handoff_law": handoff_law,
                "law_scratchpad": {
                    "query": query,
                    "candidate_tools": law_candidate_tools,
                    "law_doc_count": len(law_docs),
                    "law_source_count": len(law_sources),
                    "memory_count": len(memory_contexts),
                    "metrics": law_metrics,
                },
                "intermediate_steps": [UIEventTypes.LAW_WORKING],
            }
            self._save_node_checkpoint(
                self._snapshot_with_updates(state, updates),
                NodeNames.LAW_AGENT,
                phase="end",
            )
            return updates
        except Exception as e:
            self._save_node_checkpoint(
                state,
                NodeNames.LAW_AGENT,
                phase="error",
                status="error",
                error=str(e),
            )
            updates = {
                "handoff_law": LawAgentSubgraph.build_handoff([], [], confidence=0.0),
                "law_scratchpad": {
                    "error": str(e),
                    "metrics": {"agent": "law", "operation": "law_search", "success": False, "error": str(e)},
                },
                "intermediate_steps": [UIEventTypes.LAW_WORKING],
            }
            self._save_node_checkpoint(
                self._snapshot_with_updates(state, updates),
                NodeNames.LAW_AGENT,
                phase="end",
            )
            return updates

    async def node_tool_agent(self, state: AgenticState):
        self._save_node_checkpoint(state, NodeNames.TOOL_AGENT, phase="start")
        handoff_router = state.get("handoff_router", {}) or {}
        router_caps = [
            str(cap).strip()
            for cap in (handoff_router.get("need_capabilities", []) or [])
            if str(cap).strip()
        ]
        requested_capabilities = [cap for cap in router_caps if cap != "law_search"]
        need_tool = bool(handoff_router.get("need_tool")) or bool(requested_capabilities)
        query = str(state.get("search_query") or state.get("original_query") or "").strip()
        candidate_tool_names = self._resolve_tool_names_by_capabilities(requested_capabilities)
        if need_tool and not candidate_tool_names:
            candidate_tool_names = list(self.external_tool_names)

        if not need_tool or not query or not candidate_tool_names:
            skip_reason = "need_tool_false"
            if need_tool and not query:
                skip_reason = "empty_query"
            elif need_tool and not candidate_tool_names:
                skip_reason = "no_candidate_tools"
            updates = {
                "handoff_tool": ToolAgentSubgraph.build_handoff([], [], widget_count=0),
                "tool_scratchpad": {
                    "skipped": True,
                    "reason": skip_reason,
                    "metrics": {"agent": "tool", "operation": "planning", "skipped": True},
                    "requested_capabilities": requested_capabilities,
                },
                "intermediate_steps": [UIEventTypes.TOOL_WORKING],
            }
            self._save_node_checkpoint(
                self._snapshot_with_updates(state, updates),
                NodeNames.TOOL_AGENT,
                phase="end",
            )
            return updates

        tool_contexts = list(state.get("private_tool_contexts", []))
        tool_names: list[str] = []
        tool_messages = []
        execution_metrics = []
        planner_metrics = {}
        planned_tasks: list[dict] = []

        try:
            planner_prompt = self._build_tool_planner_prompt(query, candidate_tool_names)
            planner_result, planner_metrics, planner_err = await self._run_guarded(
                "tool",
                "json_planning",
                lambda: self.json_llm.ainvoke(planner_prompt),
            )
            planner_text = str(getattr(planner_result, "content", "") or "")
            planned_tasks = (
                self._build_tool_plan(planner_text, candidate_tool_names)
                if not planner_err else []
            )
            if not planned_tasks and candidate_tool_names:
                # 规划失败时降级到工具调用模型，保持可用性
                fallback_tools = [self.tools[name] for name in candidate_tool_names if name in self.tools]
                fallback_llm = self.llm.bind_tools(fallback_tools) if fallback_tools else self.tool_llm
                fallback_resp, fallback_metrics, _ = await self._run_guarded(
                    "tool",
                    "fallback_tool_call",
                    lambda: fallback_llm.ainvoke(
                        [
                            SystemMessage(content="你是tool_agent。仅调用外部工具，不回答法规。"),
                            HumanMessage(content=query),
                        ]
                    ),
                )
                execution_metrics.append({"fallback_metrics": fallback_metrics})
                fallback_calls = list(getattr(fallback_resp, "tool_calls", []) or [])
                for call in fallback_calls:
                    planned_tasks.append(
                        {
                            "tool": str(call.get("name") or "").strip(),
                            "args": call.get("args") if isinstance(call.get("args"), dict) else {},
                        }
                    )
                # fallback 任务也需要做字段规范化
                normalized_fallback = self._build_tool_plan(
                    json.dumps({"tasks": planned_tasks}, ensure_ascii=False),
                    candidate_tool_names,
                )
                planned_tasks = normalized_fallback

            for idx, task in enumerate(planned_tasks):
                tool_name = str(task.get("tool") or "").strip()
                tool_args = task.get("args", {}) if isinstance(task.get("args"), dict) else {}
                if (
                    tool_name not in self.tools
                    or tool_name == self.law_tool_name
                    or tool_name not in candidate_tool_names
                ):
                    continue
                tool_names.append(tool_name)
                tool_policy = self._get_tool_policy(tool_name)

                result, invoke_metrics, invoke_err = await self._run_guarded(
                    "tool",
                    f"invoke_{tool_name}",
                    lambda n=tool_name, a=tool_args: self.tools[n].ainvoke(a),
                    policy_override=tool_policy,
                )
                invoke_metrics["policy"] = tool_policy
                execution_metrics.append(invoke_metrics)
                if result is not None and not invoke_err:
                    normalized = self._normalize_tool_result(result)
                    if normalized:
                        tool_contexts.append(normalized)
                    tool_messages.append(ToolMessage(content=str(result), tool_call_id=f"{tool_name}_{idx}"))
                else:
                    tool_messages.append(
                        ToolMessage(
                            content=f"工具执行异常: {invoke_err}",
                            tool_call_id=f"{tool_name}_{idx}",
                        )
                    )

            handoff_tool = ToolAgentSubgraph.build_handoff(
                tool_contexts=tool_contexts,
                tool_names=tool_names,
                widget_count=0,
            )
            updates = {
                "messages": tool_messages,
                "private_tool_contexts": tool_contexts,
                "private_latest_tool_names": tool_names,
                "handoff_tool": handoff_tool,
                "tool_scratchpad": {
                    "query": query,
                    "requested_capabilities": requested_capabilities,
                    "candidate_tool_names": candidate_tool_names,
                    "planned_tasks": planned_tasks,
                    "tool_count": len(tool_names),
                    "tool_context_count": len(tool_contexts),
                    "metrics": {
                        "planning": planner_metrics,
                        "execution": execution_metrics,
                    },
                },
                "intermediate_steps": [UIEventTypes.TOOL_WORKING],
            }
            self._save_node_checkpoint(
                self._snapshot_with_updates(state, updates),
                NodeNames.TOOL_AGENT,
                phase="end",
            )
            return updates
        except Exception as e:
            self._save_node_checkpoint(
                state,
                NodeNames.TOOL_AGENT,
                phase="error",
                status="error",
                error=str(e),
            )
            updates = {
                "handoff_tool": ToolAgentSubgraph.build_handoff([], [], widget_count=0),
                "tool_scratchpad": {
                    "error": str(e),
                    "metrics": {"agent": "tool", "operation": "tool_agent", "success": False, "error": str(e)},
                },
                "intermediate_steps": [UIEventTypes.TOOL_WORKING],
            }
            self._save_node_checkpoint(
                self._snapshot_with_updates(state, updates),
                NodeNames.TOOL_AGENT,
                phase="end",
            )
            return updates

    async def node_synth_agent(self, state: AgenticState):
        self._save_node_checkpoint(state, NodeNames.SYNTH_AGENT, phase="start")
        original_query = state.get("original_query", "")
        search_query = state.get("search_query", "")
        question = search_query if search_query and search_query != original_query else original_query

        documents = state.get("private_documents", [])
        law_sources = state.get("public_sources", [])
        tool_contexts = state.get("private_tool_contexts", [])
        memory_contexts = state.get("private_memory_contexts", [])
        history_messages = state.get("history_messages", [])
        memory_summary = state.get("memory_summary", "")

        law_context = self._build_law_context(law_sources, documents)
        tool_context = "\n\n".join(tool_contexts).strip() or "无"
        memory_context = "\n\n".join(memory_contexts).strip() or "无"
        history_text = self._render_history(history_messages) if history_messages else ""
        history_context = self._compose_history_context(history_text, memory_summary)

        prompt = ExpertPrompts.FINAL_GENERATOR.format(
            law_context=law_context,
            tool_context=tool_context,
            memory_context=memory_context,
            question=question,
            history=history_context,
        )
        result, synth_metrics, synth_err = await self._run_guarded(
            "synth",
            "final_generation",
            lambda: self.llm.ainvoke(prompt),
        )
        if result is None and synth_err:
            answer = "生成阶段超时或失败，请稍后重试。"
        else:
            answer = str(result.content or "").strip()

        handoff_synth = SynthAgentSubgraph.build_handoff(
            answer=answer,
            law_count=len(law_sources),
            tool_count=len(tool_contexts),
            memory_count=len(memory_contexts),
        )
        updates = {
            "generation": answer,
            "handoff_synth": handoff_synth,
            "synth_scratchpad": {
                "law_sources": len(law_sources),
                "tool_contexts": len(tool_contexts),
                "memory_contexts": len(memory_contexts),
                "metrics": synth_metrics,
            },
            "intermediate_steps": [UIEventTypes.SYNTHESIZING],
        }
        self._save_node_checkpoint(
            self._snapshot_with_updates(state, updates),
            NodeNames.SYNTH_AGENT,
            phase="end",
        )
        return updates

    async def node_judge_agent(self, state: AgenticState):
        self._save_node_checkpoint(state, NodeNames.JUDGE_AGENT, phase="start")
        handoff_router = state.get("handoff_router", {}) or {}
        router_caps = [
            str(cap).strip()
            for cap in (handoff_router.get("need_capabilities", []) or [])
            if str(cap).strip()
        ]
        need_law = (bool(handoff_router.get("need_law")) or ("law_search" in router_caps)) and bool(self.law_tool_names)
        documents = state.get("private_documents", [])
        law_sources = state.get("public_sources", [])
        generation = state.get("generation", "")

        passed = True
        issues = []
        if need_law and not (law_sources or documents):
            passed = False
            issues.append("法律任务缺少有效法规证据")

        if documents and generation:
            prompt = ExpertPrompts.HALLUCINATION_GRADER.format(
                documents="\n".join(documents),
                generation=generation,
            )
            judge_result, judge_metrics, judge_err = await self._run_guarded(
                "judge",
                "fact_consistency",
                lambda: self.json_llm.ainvoke(prompt),
            )
            if judge_result is not None and not judge_err:
                score = self._extract_json_score(judge_result.content, default=GraderThresholds.SCORE_YES)
                if score == GraderThresholds.SCORE_NO:
                    passed = False
                    issues.append("回答与证据一致性不足")
            elif judge_err:
                logger.warning(f"judge_agent 幻觉检查异常，保守放行: {judge_err}")
                judge_metrics = {
                    "agent": "judge",
                    "operation": "fact_consistency",
                    "success": False,
                    "error": judge_err,
                }
        else:
            judge_metrics = {
                "agent": "judge",
                "operation": "fact_consistency",
                "skipped": True,
                "reason": "no_documents_or_generation",
            }

        handoff_judge = JudgeAgentSubgraph.build_handoff(passed=passed, issues=issues)
        final_generation = generation
        if not passed:
            final_generation = (
                "当前结果未通过事实一致性审查，已拦截输出。"
                "请补充更明确的场景信息后重试。"
            )

        updates = {
            "generation": final_generation,
            "hallucination_passed": bool(passed),
            "handoff_judge": handoff_judge,
            "judge_scratchpad": {
                "issues_count": len(issues),
                "risk_level": handoff_judge["risk_level"],
                "metrics": judge_metrics,
            },
            "intermediate_steps": [UIEventTypes.JUDGING],
        }
        self._save_node_checkpoint(
            self._snapshot_with_updates(state, updates),
            NodeNames.JUDGE_AGENT,
            phase="end",
        )
        return updates

    @staticmethod
    def _default_workflow_manifest() -> dict[str, Any]:
        return {
            "entry_point": NodeNames.BOOTSTRAP,
            "nodes": {
                NodeNames.BOOTSTRAP: {"enabled": True},
                NodeNames.ROUTER_AGENT: {"enabled": True},
                NodeNames.LAW_AGENT: {"enabled": True},
                NodeNames.TOOL_AGENT: {"enabled": True},
                NodeNames.SYNTH_AGENT: {"enabled": True},
                NodeNames.JUDGE_AGENT: {"enabled": True},
            },
            "conditional_edges": [
                {"from": NodeNames.BOOTSTRAP, "type": "resume_router"},
            ],
            "edges": [
                {"from": NodeNames.ROUTER_AGENT, "to": NodeNames.LAW_AGENT},
                {"from": NodeNames.ROUTER_AGENT, "to": NodeNames.TOOL_AGENT},
                {"from": NodeNames.LAW_AGENT, "to": NodeNames.SYNTH_AGENT},
                {"from": NodeNames.TOOL_AGENT, "to": NodeNames.SYNTH_AGENT},
                {"from": NodeNames.SYNTH_AGENT, "to": NodeNames.JUDGE_AGENT},
                {"from": NodeNames.JUDGE_AGENT, "to": "__end__"},
            ],
        }

    def _load_workflow_manifest(self) -> dict[str, Any]:
        default_manifest = self._default_workflow_manifest()
        path = str(self.workflow_manifest_path or "").strip()
        if not path or not os.path.isfile(path):
            return default_manifest
        try:
            with open(path, "r", encoding="utf-8") as f:
                payload = json.load(f)
            if not isinstance(payload, dict):
                return default_manifest
            merged = dict(default_manifest)
            if isinstance(payload.get("entry_point"), str):
                merged["entry_point"] = payload["entry_point"]
            if isinstance(payload.get("nodes"), dict):
                nodes = dict(default_manifest.get("nodes", {}))
                for node_name, cfg in payload.get("nodes", {}).items():
                    if not isinstance(cfg, dict):
                        continue
                    current = dict(nodes.get(node_name, {}))
                    current.update(cfg)
                    nodes[node_name] = current
                merged["nodes"] = nodes
            if isinstance(payload.get("conditional_edges"), list):
                merged["conditional_edges"] = payload.get("conditional_edges", [])
            if isinstance(payload.get("edges"), list):
                merged["edges"] = payload.get("edges", [])
            return merged
        except Exception as e:
            logger.warning(f"workflow manifest 读取失败，回退默认配置: {e}")
            return default_manifest

    def route_from_bootstrap(
        self, state: AgenticState
    ) -> Literal[
        NodeNames.ROUTER_AGENT,
        NodeNames.LAW_AGENT,
        NodeNames.TOOL_AGENT,
        NodeNames.SYNTH_AGENT,
        NodeNames.JUDGE_AGENT,
    ]:
        resume_from = str(state.get("resume_from_node", "") or "").strip()
        allowed = set(self.enabled_nodes) or {
            NodeNames.ROUTER_AGENT,
            NodeNames.LAW_AGENT,
            NodeNames.TOOL_AGENT,
            NodeNames.SYNTH_AGENT,
            NodeNames.JUDGE_AGENT,
        }
        allowed.discard(NodeNames.BOOTSTRAP)
        def _fallback_node() -> str:
            if NodeNames.ROUTER_AGENT in allowed:
                return NodeNames.ROUTER_AGENT
            if NodeNames.TOOL_AGENT in allowed:
                return NodeNames.TOOL_AGENT
            if NodeNames.LAW_AGENT in allowed:
                return NodeNames.LAW_AGENT
            if NodeNames.SYNTH_AGENT in allowed:
                return NodeNames.SYNTH_AGENT
            return NodeNames.JUDGE_AGENT
        legacy_map = {
            NodeNames.AGENT: NodeNames.ROUTER_AGENT,
            NodeNames.ACTION: NodeNames.TOOL_AGENT,
            NodeNames.GRADE_DOCS: NodeNames.LAW_AGENT,
            NodeNames.REWRITE: NodeNames.ROUTER_AGENT,
            NodeNames.RETRIEVE_RETRY: NodeNames.LAW_AGENT,
            NodeNames.GENERATE: NodeNames.SYNTH_AGENT,
            NodeNames.GRADE_HALLUCINATION: NodeNames.JUDGE_AGENT,
        }
        if resume_from in allowed:
            logger.warning(f"[恢复执行] 从断点节点恢复: {resume_from}")
            return resume_from  # type: ignore[return-value]
        if resume_from in legacy_map:
            mapped = legacy_map[resume_from]
            if mapped not in allowed:
                return _fallback_node()  # type: ignore[return-value]
            logger.warning(f"[恢复执行] 旧节点 {resume_from} 映射到新节点 {mapped}")
            return mapped  # type: ignore[return-value]
        return _fallback_node()  # type: ignore[return-value]

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(AgenticState)
        manifest = self._load_workflow_manifest()
        node_handlers = {
            NodeNames.BOOTSTRAP: self.node_bootstrap,
            NodeNames.ROUTER_AGENT: self.node_router_agent,
            NodeNames.LAW_AGENT: self.node_law_agent,
            NodeNames.TOOL_AGENT: self.node_tool_agent,
            NodeNames.SYNTH_AGENT: self.node_synth_agent,
            NodeNames.JUDGE_AGENT: self.node_judge_agent,
        }

        nodes_cfg = manifest.get("nodes", {}) if isinstance(manifest.get("nodes"), dict) else {}
        enabled_nodes = []
        for node_name, handler in node_handlers.items():
            cfg = nodes_cfg.get(node_name, {}) if isinstance(nodes_cfg, dict) else {}
            enabled = bool(cfg.get("enabled", True)) if isinstance(cfg, dict) else True
            if enabled:
                workflow.add_node(node_name, handler)
                enabled_nodes.append(node_name)
        self.enabled_nodes = set(enabled_nodes)
        if not enabled_nodes:
            workflow.add_node(NodeNames.BOOTSTRAP, self.node_bootstrap)
            workflow.add_node(NodeNames.ROUTER_AGENT, self.node_router_agent)
            workflow.add_node(NodeNames.SYNTH_AGENT, self.node_synth_agent)
            workflow.add_node(NodeNames.JUDGE_AGENT, self.node_judge_agent)
            enabled_nodes = [NodeNames.BOOTSTRAP, NodeNames.ROUTER_AGENT, NodeNames.SYNTH_AGENT, NodeNames.JUDGE_AGENT]
            self.enabled_nodes = set(enabled_nodes)

        entry_point = str(manifest.get("entry_point") or NodeNames.BOOTSTRAP).strip()
        if entry_point not in self.enabled_nodes:
            entry_point = NodeNames.BOOTSTRAP if NodeNames.BOOTSTRAP in self.enabled_nodes else enabled_nodes[0]
        workflow.set_entry_point(entry_point)

        conditional_edges = manifest.get("conditional_edges", [])
        conditional_added = False
        if isinstance(conditional_edges, list):
            for item in conditional_edges:
                if not isinstance(item, dict):
                    continue
                src = str(item.get("from") or "").strip()
                edge_type = str(item.get("type") or "").strip()
                if src == NodeNames.BOOTSTRAP and edge_type == "resume_router" and src in self.enabled_nodes:
                    workflow.add_conditional_edges(NodeNames.BOOTSTRAP, self.route_from_bootstrap)
                    conditional_added = True
        if (
            not conditional_added
            and NodeNames.BOOTSTRAP in self.enabled_nodes
            and NodeNames.ROUTER_AGENT in self.enabled_nodes
        ):
            workflow.add_conditional_edges(NodeNames.BOOTSTRAP, self.route_from_bootstrap)

        edges = manifest.get("edges", [])
        seen_edges = set()
        if isinstance(edges, list):
            for item in edges:
                if not isinstance(item, dict):
                    continue
                src = str(item.get("from") or "").strip()
                dst = str(item.get("to") or "").strip()
                if not src or not dst:
                    continue
                edge_key = f"{src}->{dst}"
                if edge_key in seen_edges:
                    continue
                seen_edges.add(edge_key)
                if src not in self.enabled_nodes:
                    continue
                if dst == "__end__":
                    workflow.add_edge(src, END)
                    continue
                if dst in self.enabled_nodes:
                    workflow.add_edge(src, dst)

        return workflow.compile()
