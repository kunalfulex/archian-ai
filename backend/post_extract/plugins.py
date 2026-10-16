from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from post_extract.rules_proposer import propose_rules_delta
from post_extract.session import OperationStep, PostExtractSession


class OperationPlugin(ABC):
    operation_id: str
    rule_buckets: List[str]

    @abstractmethod
    async def propose_rules(
        self, session: PostExtractSession, step: OperationStep
    ) -> Dict[str, Any]:
        ...


class TransformPlugin(OperationPlugin):
    operation_id = "transform"
    rule_buckets = ["transformation_rules"]

    async def propose_rules(self, session: PostExtractSession, step: OperationStep) -> Dict[str, Any]:
        delta = await propose_rules_delta(
            self.operation_id,
            step.prompt or "",
            session.sample_rows(),
            session.source_schema,
            data_quality_report=step.params.get("data_quality_report"),
            user_id=session.user_id,
        )
        if delta.get("transformation_rules"):
            return delta
        fields = delta.get("fields")
        if isinstance(fields, list):
            return {"transformation_rules": {"fields": fields}}
        return delta


class FilterPlugin(OperationPlugin):
    operation_id = "filter"
    rule_buckets = ["filter_rules"]

    async def propose_rules(self, session: PostExtractSession, step: OperationStep) -> Dict[str, Any]:
        return await propose_rules_delta(
            self.operation_id,
            step.prompt or "",
            session.sample_rows(),
            session.source_schema,
            data_quality_report=step.params.get("data_quality_report"),
            user_id=session.user_id,
            existing_rules=session.extraction_rules,
        )


class DedupePlugin(OperationPlugin):
    operation_id = "dedupe"
    rule_buckets = ["deduplication_rules"]

    async def propose_rules(self, session: PostExtractSession, step: OperationStep) -> Dict[str, Any]:
        return await propose_rules_delta(
            self.operation_id,
            step.prompt or "",
            session.sample_rows(),
            session.source_schema,
            data_quality_report=step.params.get("data_quality_report"),
            user_id=session.user_id,
        )


class ValidatePlugin(OperationPlugin):
    operation_id = "validate"
    rule_buckets = ["data_cleanup_rules"]

    async def propose_rules(self, session: PostExtractSession, step: OperationStep) -> Dict[str, Any]:
        return await propose_rules_delta(
            self.operation_id,
            step.prompt or "",
            session.sample_rows(),
            session.source_schema,
            data_quality_report=step.params.get("data_quality_report"),
            user_id=session.user_id,
        )


class OperationRegistry:
    def __init__(self) -> None:
        self._plugins: Dict[str, OperationPlugin] = {}

    def register(self, plugin: OperationPlugin) -> None:
        self._plugins[plugin.operation_id] = plugin
        alias_map = {
            "transform": ("transformation",),
            "dedupe": ("deduplication",),
            "validate": ("validation", "cleanup_values"),
        }
        for alias in alias_map.get(plugin.operation_id, ()):
            self._plugins[alias] = plugin

    def get(self, operation_id: str) -> OperationPlugin:
        key = (operation_id or "").strip().lower()
        aliases = {
            "transformation": "transform",
            "deduplication": "dedupe",
            "validation": "validate",
            "cleanup_values": "validate",
        }
        key = aliases.get(key, key)
        if key not in self._plugins:
            raise KeyError(f"Unknown post-extract operation: {operation_id}")
        return self._plugins[key]


def default_registry() -> OperationRegistry:
    reg = OperationRegistry()
    reg.register(TransformPlugin())
    reg.register(FilterPlugin())
    reg.register(DedupePlugin())
    reg.register(ValidatePlugin())
    return reg
