import importlib
import inspect
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass
class ToolPluginManifest:
    name: str
    enabled: bool
    factory: str
    capabilities: list[str] = field(default_factory=list)
    description: str = ""
    tool_names: list[str] = field(default_factory=list)
    tools: list[dict[str, Any]] = field(default_factory=list)
    route_hints: dict[str, list[str]] = field(default_factory=dict)
    policy: dict[str, Any] = field(default_factory=dict)
    factory_kwargs: dict[str, Any] = field(default_factory=dict)
    path: str = ""


@dataclass
class ToolRegistryLoadResult:
    tools: list[Any] = field(default_factory=list)
    manifests: list[ToolPluginManifest] = field(default_factory=list)
    tool_metadata_by_name: dict[str, dict[str, Any]] = field(default_factory=dict)
    capability_to_tools: dict[str, list[str]] = field(default_factory=dict)
    capability_hints: dict[str, list[str]] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)


class ToolRegistry:
    """基于 manifest 的工具注册中心（第一阶段：注册与装配自动化）。"""

    def __init__(self, plugin_root: str | None = None):
        default_root = os.path.join(settings.BASE_DIR, "app", "skills", "plugins")
        self.plugin_root = plugin_root or default_root

    @staticmethod
    def _to_str_list(values: Any) -> list[str]:
        if not isinstance(values, list):
            return []
        return [str(item).strip() for item in values if str(item).strip()]

    @classmethod
    def _to_hints_map(cls, raw: Any) -> dict[str, list[str]]:
        if not isinstance(raw, dict):
            return {}
        hints: dict[str, list[str]] = {}
        for key, value in raw.items():
            capability = str(key).strip()
            if not capability:
                continue
            items = cls._to_str_list(value)
            if items:
                hints[capability] = items
        return hints

    @staticmethod
    def _to_dict(raw: Any) -> dict[str, Any]:
        return raw if isinstance(raw, dict) else {}

    def discover_manifests(self) -> list[ToolPluginManifest]:
        manifests: list[ToolPluginManifest] = []
        if not os.path.isdir(self.plugin_root):
            logger.warning(f"ToolRegistry 插件目录不存在: {self.plugin_root}")
            return manifests

        for entry in sorted(os.listdir(self.plugin_root)):
            plugin_dir = os.path.join(self.plugin_root, entry)
            if not os.path.isdir(plugin_dir):
                continue
            manifest_path = os.path.join(plugin_dir, "manifest.json")
            if not os.path.isfile(manifest_path):
                continue
            try:
                with open(manifest_path, "r", encoding="utf-8") as f:
                    raw = json.load(f)
                manifest = ToolPluginManifest(
                    name=str(raw.get("name") or entry).strip() or entry,
                    enabled=bool(raw.get("enabled", True)),
                    factory=str(raw.get("factory") or "").strip(),
                    capabilities=self._to_str_list(raw.get("capabilities") or []),
                    description=str(raw.get("description") or "").strip(),
                    tool_names=self._to_str_list(raw.get("tool_names") or []),
                    tools=[
                        item for item in (raw.get("tools") or [])
                        if isinstance(item, dict)
                    ],
                    route_hints=self._to_hints_map(raw.get("route_hints") or {}),
                    policy=self._to_dict(raw.get("policy") or {}),
                    factory_kwargs=raw.get("factory_kwargs")
                    if isinstance(raw.get("factory_kwargs"), dict)
                    else {},
                    path=manifest_path,
                )
                if not manifest.factory:
                    logger.warning(f"ToolRegistry manifest 缺少 factory，已跳过: {manifest_path}")
                    continue
                manifests.append(manifest)
            except Exception as e:
                logger.warning(f"ToolRegistry 读取 manifest 失败 {manifest_path}: {e}")
        return manifests

    @staticmethod
    def _resolve_factory(factory_ref: str):
        if ":" not in factory_ref:
            raise ValueError("factory 格式错误，应为 module.path:function_name")
        module_name, fn_name = factory_ref.split(":", 1)
        module = importlib.import_module(module_name)
        factory = getattr(module, fn_name, None)
        if factory is None:
            raise ValueError(f"factory 不存在: {factory_ref}")
        if not callable(factory):
            raise ValueError(f"factory 不可调用: {factory_ref}")
        return factory

    @staticmethod
    def _invoke_factory(factory, context: dict[str, Any], static_kwargs: dict[str, Any]) -> Any:
        context = dict(context or {})
        static_kwargs = dict(static_kwargs or {})
        signature = inspect.signature(factory)
        params = signature.parameters
        accepts_kwargs = any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())

        if accepts_kwargs:
            payload = dict(context)
        else:
            payload = {k: v for k, v in context.items() if k in params}

        payload.update(static_kwargs)
        return factory(**payload)

    @staticmethod
    def _normalize_tools(raw: Any) -> list[Any]:
        if raw is None:
            return []
        if isinstance(raw, (list, tuple)):
            return list(raw)
        return [raw]

    @staticmethod
    def _extract_input_schema(tool: Any) -> dict[str, Any]:
        args_schema = getattr(tool, "args_schema", None)
        if args_schema is None:
            return {}
        schema_fn = getattr(args_schema, "model_json_schema", None)
        if callable(schema_fn):
            try:
                payload = schema_fn()
                return payload if isinstance(payload, dict) else {}
            except Exception:
                return {}
        return {}

    @staticmethod
    def _merge_unique(base: list[str], extra: list[str]) -> list[str]:
        merged = list(base or [])
        seen = set(merged)
        for item in extra or []:
            normalized = str(item).strip()
            if not normalized or normalized in seen:
                continue
            merged.append(normalized)
            seen.add(normalized)
        return merged

    @staticmethod
    def _get_tool_spec(manifest: ToolPluginManifest, tool_name: str) -> dict[str, Any]:
        for item in manifest.tools:
            if str(item.get("name") or "").strip() == tool_name:
                return item
        return {}

    def load_tools(self, context: dict[str, Any] | None = None) -> ToolRegistryLoadResult:
        result = ToolRegistryLoadResult()
        context = dict(context or {})
        seen_tool_names: set[str] = set()

        manifests = self.discover_manifests()
        result.manifests = manifests

        for manifest in manifests:
            if not manifest.enabled:
                continue
            try:
                factory = self._resolve_factory(manifest.factory)
                built = self._invoke_factory(factory, context, manifest.factory_kwargs)
                tools = self._normalize_tools(built)
            except Exception as e:
                msg = f"[{manifest.name}] 工具工厂加载失败: {e}"
                logger.warning(msg)
                result.errors.append(msg)
                continue

            for tool in tools:
                tool_name = str(getattr(tool, "name", "") or "").strip()
                if not tool_name:
                    msg = f"[{manifest.name}] 返回对象缺少 tool.name，已跳过"
                    logger.warning(msg)
                    result.errors.append(msg)
                    continue
                if tool_name in seen_tool_names:
                    logger.warning(f"ToolRegistry 检测到重复工具名，保留首个: {tool_name}")
                    continue
                seen_tool_names.add(tool_name)

                tool_spec = self._get_tool_spec(manifest, tool_name)
                tool_caps = self._to_str_list(tool_spec.get("capabilities") or manifest.capabilities)
                planner_enabled = bool(tool_spec.get("planner_enabled", True))
                tool_policy = dict(manifest.policy or {})
                if isinstance(tool_spec.get("policy"), dict):
                    tool_policy.update(tool_spec.get("policy") or {})
                tool_route_hints = self._to_str_list(tool_spec.get("route_hints") or [])
                input_schema = self._extract_input_schema(tool)

                if tool_route_hints and tool_caps:
                    for cap in tool_caps:
                        merged_hints = self._merge_unique(
                            result.capability_hints.get(cap, []),
                            tool_route_hints,
                        )
                        if merged_hints:
                            result.capability_hints[cap] = merged_hints

                for cap, hints in (manifest.route_hints or {}).items():
                    merged_hints = self._merge_unique(
                        result.capability_hints.get(cap, []),
                        hints,
                    )
                    if merged_hints:
                        result.capability_hints[cap] = merged_hints

                for cap in tool_caps:
                    slot = result.capability_to_tools.setdefault(cap, [])
                    if tool_name not in slot:
                        slot.append(tool_name)

                result.tools.append(tool)
                result.tool_metadata_by_name[tool_name] = {
                    "plugin_name": manifest.name,
                    "capabilities": tool_caps,
                    "description": manifest.description,
                    "planner_enabled": planner_enabled,
                    "policy": tool_policy,
                    "input_schema": input_schema,
                    "route_hints": tool_route_hints,
                    "declared_tool_names": list(manifest.tool_names),
                    "manifest_path": manifest.path,
                }

        return result
