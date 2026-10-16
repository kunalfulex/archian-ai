"""Self-learning label refinement from Architect execution evidence."""
from __future__ import annotations

import asyncio
import copy
import logging
from typing import Any, Dict, List, Optional, Set, Tuple

from services.labeling.config import LabelingConfig
from services.labeling.connector_profile_service import get_connector_display_name
from services.labeling.connector_ref import connector_instance_id, normalize_connector_ref
from services.labeling.evidence_connector_resolve import (
    entity_key_from_classification,
    resolve_connector_target,
)
from services.labeling.learning_prompts import build_label_refinement_prompt
from services.labeling.models import EntitySemantics
from services.labeling.store import LabelingStore, _dedupe_strings
from utils.openai_helpers import parse_openai_json_response

logger = logging.getLogger(__name__)


def merge_semantics_proposal(
    existing: Dict[str, Any],
    proposal: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Merge policy: auto-apply synonyms/domains/processes only.
    Never change primary category/description.
    """
    merged = copy.deepcopy(existing or {})
    primary = merged.get("primary")
    if not isinstance(primary, dict):
        primary = {}
    merged["primary"] = {
        "category": str(primary.get("category") or existing.get("document_type") or ""),
        "description": str(primary.get("description") or existing.get("document_description") or ""),
    }
    merged["domains"] = _dedupe_strings(
        list(merged.get("domains") or []) + list(proposal.get("add_domains") or [])
    )
    merged["processes"] = _dedupe_strings(
        list(merged.get("processes") or []) + list(proposal.get("add_processes") or [])
    )
    merged["synonyms"] = _dedupe_strings(
        list(merged.get("synonyms") or []) + list(proposal.get("add_synonyms") or [])
    )
    merged["relationship_hints"] = merged.get("relationship_hints") or []
    merged["confidence"] = merged.get("confidence", 0.5)
    merged["source"] = "execution_learning"
    return merged


class LabelLearningService:
    """Capture execution evidence and refine entity/connector labels in background."""

    @staticmethod
    def enabled() -> bool:
        return True

    @classmethod
    def schedule_capture(cls, coro_factory) -> None:
        if not cls.enabled():
            return
        try:
            loop = asyncio.get_running_loop()
            coro = coro_factory() if callable(coro_factory) else coro_factory
            loop.create_task(coro)
        except RuntimeError:
            pass

    @classmethod
    async def _insert_and_schedule(
        cls,
        *,
        user_id: str,
        event_type: str,
        connector_type: str,
        connector_ref: Dict[str, str],
        entity_key: Optional[str] = None,
        entity_display_name: Optional[str] = None,
        prompt_excerpt: Optional[str] = None,
        user_phrase: Optional[str] = None,
        context: Optional[Dict[str, Any]] = None,
        outcome: Optional[Dict[str, Any]] = None,
        pipeline_run_id: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> None:
        await LabelingStore.insert_label_evidence(
            user_id=str(user_id),
            event_type=event_type,
            connector_type=connector_type,
            connector_ref=connector_ref,
            entity_key=entity_key,
            entity_display_name=entity_display_name,
            prompt_excerpt=prompt_excerpt,
            user_phrase=user_phrase,
            context=context,
            outcome=outcome,
            pipeline_run_id=pipeline_run_id,
            conversation_id=conversation_id,
        )
        from services.labeling.learning_schedule import schedule_label_refinement_debounced

        schedule_label_refinement_debounced(
            user_id=str(user_id),
            connector_type=connector_type,
            connector_ref=connector_ref,
        )

    @classmethod
    async def _targets_from_classification(
        cls,
        user_id: str,
        classification: Dict[str, Any],
    ) -> List[Tuple[str, Dict[str, str], Optional[str]]]:
        targets: List[Tuple[str, Dict[str, str], Optional[str]]] = []
        seen: Set[str] = set()
        for side in ("source", "destination"):
            ct = classification.get(side)
            name = classification.get(f"{side}_name")
            cid = classification.get(f"{side}_connector_id") or classification.get(
                f"{side}_token_id"
            )
            resolved = await resolve_connector_target(
                user_id, ct, display_name=name, connector_id=cid
            )
            if not resolved:
                continue
            connector_type, connector_ref, _ = resolved
            inst = connector_instance_id(connector_type, connector_ref)
            key = f"{connector_type}:{inst}"
            if key in seen:
                continue
            seen.add(key)
            entity_key = entity_key_from_classification(classification, connector_type)
            targets.append((connector_type, connector_ref, entity_key))
        return targets

    @classmethod
    async def capture_plan_event(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        prompt: str,
        classification: Dict[str, Any],
        event_type: str,
        pipeline_run_id: Optional[str] = None,
    ) -> None:
        if not cls.enabled():
            return
        try:
            targets = await cls._targets_from_classification(user_id, classification)
            if not targets:
                return
            excerpt = (prompt or "")[:500]
            for connector_type, connector_ref, entity_key in targets:
                await cls._insert_and_schedule(
                    user_id=user_id,
                    event_type=event_type,
                    connector_type=connector_type,
                    connector_ref=connector_ref,
                    entity_key=entity_key,
                    prompt_excerpt=excerpt,
                    user_phrase=excerpt,
                    context={
                        "source": classification.get("source"),
                        "destination": classification.get("destination"),
                        "source_name": classification.get("source_name"),
                        "destination_name": classification.get("destination_name"),
                        "pipeline_label": (
                            ((classification.get("execution_plan") or {}).get("metadata") or {})
                            .get("pipeline_label")
                        ),
                        "destination_object_api_name": classification.get(
                            "destination_object_api_name"
                        ),
                    },
                    pipeline_run_id=pipeline_run_id,
                    conversation_id=conversation_id,
                )
        except Exception as exc:
            logger.warning("capture_plan_event failed: %s", exc)

    @classmethod
    async def capture_clarification_resolved(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        prompt: Optional[str],
        classification: Dict[str, Any],
        selections: List[Dict[str, Any]],
    ) -> None:
        if not cls.enabled() or not selections:
            return
        try:
            targets = await cls._targets_from_classification(user_id, classification)
            phrase = (prompt or "")[:500]
            for connector_type, connector_ref, entity_key in targets:
                await cls._insert_and_schedule(
                    user_id=user_id,
                    event_type="clarification",
                    connector_type=connector_type,
                    connector_ref=connector_ref,
                    entity_key=entity_key,
                    prompt_excerpt=phrase,
                    user_phrase=phrase,
                    context={"selections": selections[:20]},
                    conversation_id=conversation_id,
                )
        except Exception as exc:
            logger.warning("capture_clarification_resolved failed: %s", exc)

    @classmethod
    async def capture_mapping_finalized(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        mapping_config: Dict[str, Any],
        destination_object: Optional[str] = None,
        destination_type: Optional[str] = None,
        destination_name: Optional[str] = None,
        connector_id: Optional[str] = None,
        source_type: Optional[str] = None,
        source_name: Optional[str] = None,
    ) -> None:
        if not cls.enabled():
            return
        try:
            dest_target = await resolve_connector_target(
                user_id,
                destination_type,
                display_name=destination_name,
                connector_id=connector_id,
            )
            if dest_target:
                ct, ref, _ = dest_target
                entity_key = destination_object or entity_key_from_classification(
                    {"destination_object_api_name": destination_object or ""},
                    ct,
                )
                await cls._insert_and_schedule(
                    user_id=user_id,
                    event_type="mapping_finalized",
                    connector_type=ct,
                    connector_ref=ref,
                    entity_key=entity_key,
                    context={
                        "destination_object": destination_object,
                        "field_count": len(mapping_config or {}),
                        "mapping_keys": list((mapping_config or {}).keys())[:30],
                    },
                    conversation_id=conversation_id,
                )
            src_target = await resolve_connector_target(
                user_id, source_type, display_name=source_name
            )
            if src_target:
                ct, ref, entity_key = src_target
                await cls._insert_and_schedule(
                    user_id=user_id,
                    event_type="mapping_finalized",
                    connector_type=ct,
                    connector_ref=ref,
                    entity_key=entity_key,
                    context={"role": "source", "destination_object": destination_object},
                    conversation_id=conversation_id,
                )
        except Exception as exc:
            logger.warning("capture_mapping_finalized failed: %s", exc)

    @classmethod
    async def capture_migration_outcome(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        batch_id: str,
        destination_type: Optional[str] = None,
        destination_name: Optional[str] = None,
        total_records: int = 0,
        successful_records: int = 0,
        failed_records: int = 0,
        destination_object: Optional[str] = None,
        failed_fields: Optional[List[str]] = None,
        error_samples: Optional[List[str]] = None,
    ) -> None:
        if not cls.enabled():
            return
        from agent.recovery_policy import migration_event_type

        total = max(total_records, 1)
        success_rate = successful_records / total
        event_type = migration_event_type(success_rate)
        try:
            target = await resolve_connector_target(
                user_id,
                destination_type,
                display_name=destination_name,
            )
            if not target:
                return
            ct, ref, _ = target
            await cls._insert_and_schedule(
                user_id=user_id,
                event_type=event_type,
                connector_type=ct,
                connector_ref=ref,
                entity_key=destination_object,
                outcome={
                    "batch_id": batch_id,
                    "success_rate": success_rate,
                    "total_records": total_records,
                    "successful_records": successful_records,
                    "failed_records": failed_records,
                    "failed_fields": failed_fields or [],
                    "error_samples": (error_samples or [])[:10],
                },
                conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.warning("capture_migration_outcome failed: %s", exc)

    @classmethod
    async def refine_connector_labels(
        cls,
        *,
        user_id: str,
        connector_type: str,
        connector_ref: Dict[str, Any],
        evidence_ids: Optional[List[str]] = None,
    ) -> Dict[str, Any]:
        connector_ref = await normalize_connector_ref(user_id, connector_type, connector_ref)
        inst = connector_instance_id(connector_type, connector_ref)
        evidence = await LabelingStore.list_pending_evidence(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=inst,
            limit=LabelingConfig.batch_size() * 3,
        )
        if evidence_ids:
            allowed = set(evidence_ids)
            evidence = [e for e in evidence if str(e.get("id")) in allowed]
        if not evidence:
            return {"updated_entities": 0, "skipped": True}

        entities = await LabelingStore.list_ready_entity_summaries(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=inst,
            limit=40,
        )
        if not entities:
            processed_ids = [str(e["id"]) for e in evidence if e.get("id")]
            await LabelingStore.mark_evidence_processed(processed_ids, user_id)
            return {"updated_entities": 0, "skipped": True, "reason": "no_ready_entities"}

        display_name = await get_connector_display_name(
            user_id=user_id,
            connector_type=connector_type,
            connector_instance_id=inst,
        )
        evidence_payload = [
            {
                "event_type": e.get("event_type"),
                "entity_key": e.get("entity_key"),
                "user_phrase": e.get("user_phrase"),
                "prompt_excerpt": e.get("prompt_excerpt"),
                "context": e.get("context"),
                "outcome": e.get("outcome"),
            }
            for e in evidence
        ]
        prompt = build_label_refinement_prompt(
            connector_type=connector_type,
            connector_display_name=display_name,
            entity_summaries=entities,
            evidence_items=evidence_payload,
        )

        from services.architect_agent import async_openai_call
        from services.llm import tasks as llm_tasks

        response = await async_openai_call(
            None,
            task=llm_tasks.SCHEMA_CATEGORIZATION,
            messages=[{"role": "user", "content": prompt}],
            response_format={"type": "json_object"},
            temperature=0.2,
            max_tokens=2000,
        )
        raw = response.choices[0].message.content or "{}"
        parsed = parse_openai_json_response(raw)
        if not isinstance(parsed, dict):
            parsed = {}

        updated = 0
        for upd in parsed.get("entity_updates") or []:
            if not isinstance(upd, dict):
                continue
            entity_key = str(upd.get("entity_key") or "").strip()
            if not entity_key:
                continue
            row = await LabelingStore.get_entity_semantics_row(
                user_id=user_id,
                connector_type=connector_type,
                connector_instance_id=inst,
                entity_key=entity_key,
            )
            if not row or not row.get("semantics"):
                continue
            merged = merge_semantics_proposal(dict(row["semantics"]), upd)
            if merged == row["semantics"]:
                continue
            await LabelingStore.merge_entity_semantics(
                user_id=user_id,
                connector_type=connector_type,
                connector_ref=connector_ref,
                entity_key=entity_key,
                merged_semantics=merged,
            )
            await cls._embed_entity_label(
                user_id=user_id,
                conversation_id=evidence[0].get("conversation_id") if evidence else None,
                connector_type=connector_type,
                entity_key=entity_key,
                semantics=merged,
            )
            updated += 1

        conn_upd = parsed.get("connector_updates")
        if isinstance(conn_upd, dict):
            await LabelingStore.merge_connector_profile_synonyms(
                user_id=user_id,
                connector_type=connector_type,
                connector_instance_id=inst,
                add_synonyms=list(conn_upd.get("add_synonyms") or []),
                add_business_domains=list(conn_upd.get("add_business_domains") or []),
            )

        processed_ids = [str(e["id"]) for e in evidence if e.get("id")]
        await LabelingStore.mark_evidence_processed(processed_ids, user_id)
        return {"updated_entities": updated, "evidence_processed": len(processed_ids)}

    @classmethod
    async def _embed_entity_label(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        connector_type: str,
        entity_key: str,
        semantics: Dict[str, Any],
    ) -> None:
        try:
            from services.pipeline_learning.service import PipelineLearningService

            if not PipelineLearningService.enabled():
                return
            run_id = await PipelineLearningService.ensure_run(
                user_id=str(user_id),
                conversation_id=conversation_id,
            )
            if not run_id:
                return
            sem = EntitySemantics.from_llm_dict(semantics)
            text = (
                f"{entity_key}: {sem.primary.category}\n"
                f"{sem.primary.description}\n"
                f"domains: {', '.join(sem.domains)}\n"
                f"synonyms: {', '.join(sem.synonyms)}"
            ).strip()
            if not text:
                return
            from services.pipeline_learning.store import PipelineLearningStore

            await PipelineLearningStore.upsert_chunk(
                user_id=str(user_id),
                pipeline_run_id=run_id,
                stage="labeling",
                chunk_type="entity_label",
                content_text=text[:4000],
                metadata={
                    "connector_type": connector_type,
                    "entity_key": entity_key,
                    "source": "execution_learning",
                },
            )
        except Exception as exc:
            logger.debug("entity label embed skipped: %s", exc)
