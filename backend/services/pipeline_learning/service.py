from __future__ import annotations

import asyncio
import logging
from typing import Any, Dict, List, Optional

from services.pipeline_learning import chunk_builder
from services.pipeline_learning.retrieve import retrieve_similar_chunks
from services.pipeline_learning.store import PipelineLearningStore
from utils.learning_diff import (
    copy_rules_snapshot,
    diff_extraction_rules,
    diff_has_signal,
    diff_mapping_config,
    redact_snapshot_for_storage,
)

logger = logging.getLogger(__name__)


class PipelineLearningService:
    """Capture stage corrections and retrieve similar pipeline examples."""

    @staticmethod
    def enabled() -> bool:
        return True

    @classmethod
    async def ensure_run(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str] = None,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        source_fingerprint: Optional[str] = None,
    ) -> Optional[str]:
        if not cls.enabled():
            return None
        try:
            return await PipelineLearningStore.get_or_create_run(
                user_id=str(user_id),
                conversation_id=conversation_id,
                source_type=source_type,
                destination_type=destination_type,
                source_fingerprint=source_fingerprint,
            )
        except Exception as exc:
            logger.warning("pipeline_learning ensure_run failed: %s", exc)
            return None

    @classmethod
    async def capture_extraction(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        job_id: str,
        ai_rules: Optional[Dict[str, Any]],
        final_rules: Optional[Dict[str, Any]],
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        source_schema: Optional[List[str]] = None,
        stage_version: int = 1,
        prompt: str = "",
        mapping_context_summary: Optional[str] = None,
    ) -> None:
        if not cls.enabled():
            return
        try:
            fp = chunk_builder.source_fingerprint_from_schema(source_schema)
            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
                source_type=source_type,
                destination_type=destination_type,
                source_fingerprint=fp,
            )
            if not run_id:
                return
            await PipelineLearningStore.update_run(run_id, latest_job_id=job_id)
            ai_snap = copy_rules_snapshot(ai_rules)
            final_snap = copy_rules_snapshot(final_rules)
            diff = diff_extraction_rules(ai_rules, final_rules)
            if not diff_has_signal(diff) and not diff.get("changed"):
                diff = {"changed": True, "sections": {"accepted": True}}

            await PipelineLearningStore.insert_correction(
                pipeline_run_id=run_id,
                user_id=str(user_id),
                stage="extraction",
                stage_version=stage_version,
                ai_snapshot=ai_snap,
                final_snapshot=final_snap,
                diff=diff,
                context={"job_id": job_id},
            )
            meta = chunk_builder.chunk_metadata(
                source_type=source_type,
                destination_type=destination_type,
                source_fingerprint=fp,
                job_id=job_id,
            )
            text = chunk_builder.build_extraction_rules_text(final_rules or ai_rules)
            if text:
                await PipelineLearningStore.upsert_chunk(
                    user_id=str(user_id),
                    pipeline_run_id=run_id,
                    stage="extraction",
                    chunk_type="extraction_rules",
                    content_text=text,
                    metadata=meta,
                )
            intent = chunk_builder.build_user_intent_text(
                prompt=prompt,
                mapping_context_summary=mapping_context_summary,
                source_type=source_type,
                destination_type=destination_type,
            )
            if intent.strip():
                await PipelineLearningStore.upsert_chunk(
                    user_id=str(user_id),
                    pipeline_run_id=run_id,
                    stage="extraction",
                    chunk_type="user_intent",
                    content_text=intent,
                    metadata=meta,
                )
        except Exception as exc:
            logger.warning("capture_extraction failed: %s", exc)

    @classmethod
    async def capture_data_ops(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        job_id: str,
        parent_job_id: str,
        operation_id: str,
        proposed_delta: Optional[Dict[str, Any]],
        rules_after_merge: Optional[Dict[str, Any]],
        operations_applied: List[str],
        row_count_before: Optional[int] = None,
        row_count_after: Optional[int] = None,
        source_type: Optional[str] = None,
        stage_version: int = 1,
    ) -> None:
        if not cls.enabled():
            return
        try:
            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
                source_type=source_type,
            )
            if not run_id:
                return
            await PipelineLearningStore.update_run(run_id, latest_job_id=job_id)
            ai_snap = redact_snapshot_for_storage(proposed_delta or {})
            final_snap = copy_rules_snapshot(rules_after_merge)
            diff = diff_extraction_rules(proposed_delta, rules_after_merge)

            await PipelineLearningStore.insert_correction(
                pipeline_run_id=run_id,
                user_id=str(user_id),
                stage="data_ops",
                stage_version=stage_version,
                ai_snapshot=ai_snap,
                final_snapshot=final_snap,
                diff=diff,
                context={
                    "job_id": job_id,
                    "parent_job_id": parent_job_id,
                    "operation_id": operation_id,
                    "operations_applied": operations_applied,
                },
            )
            text = chunk_builder.build_data_ops_recipe_text(
                operations_applied=operations_applied,
                extraction_rules=rules_after_merge,
                row_count_before=row_count_before,
                row_count_after=row_count_after,
            )
            if text:
                await PipelineLearningStore.upsert_chunk(
                    user_id=str(user_id),
                    pipeline_run_id=run_id,
                    stage="data_ops",
                    chunk_type="data_ops_recipe",
                    content_text=text,
                    metadata=chunk_builder.chunk_metadata(source_type=source_type, job_id=job_id),
                )
        except Exception as exc:
            logger.warning("capture_data_ops failed: %s", exc)

    @classmethod
    async def capture_data_ops_run_complete(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        job_id: str,
        operations_applied: List[str],
        extraction_rules: Optional[Dict[str, Any]],
        row_count_before: int,
        row_count_after: int,
        source_type: Optional[str] = None,
    ) -> None:
        """Summary capture at end of orchestrator run."""
        if not cls.enabled():
            return
        await cls.capture_data_ops(
            user_id=user_id,
            conversation_id=conversation_id,
            job_id=job_id,
            parent_job_id=job_id,
            operation_id="pipeline_complete",
            proposed_delta={},
            rules_after_merge=extraction_rules,
            operations_applied=operations_applied,
            row_count_before=row_count_before,
            row_count_after=row_count_after,
            source_type=source_type,
        )

    @classmethod
    async def capture_mapping_ai(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        job_id: str,
        mapping_config: Dict[str, Any],
        mapping_plan: Optional[Dict[str, Any]] = None,
        transformation_rules: Optional[Dict[str, Any]] = None,
        destination_object: Optional[str] = None,
        destination_type: Optional[str] = None,
        source_type: Optional[str] = None,
        mapping_message_id: Optional[str] = None,
        stage_version: int = 1,
    ) -> None:
        if not cls.enabled():
            return
        try:
            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
                source_type=source_type,
                destination_type=destination_type,
            )
            if not run_id:
                return
            await PipelineLearningStore.update_run(
                run_id,
                latest_job_id=job_id,
                latest_mapping_message_id=mapping_message_id,
            )
            ai_snap = {
                "mapping_config": redact_snapshot_for_storage(mapping_config),
                "mapping_plan": mapping_plan,
                "transformation_rules": transformation_rules,
            }
            await PipelineLearningStore.insert_correction(
                pipeline_run_id=run_id,
                user_id=str(user_id),
                stage="mapping",
                stage_version=stage_version,
                ai_snapshot=ai_snap,
                final_snapshot=None,
                diff={"changed": False, "pending_user": True},
                context={"job_id": job_id, "phase": "ai_snapshot"},
            )
            text = chunk_builder.build_mapping_template_text(
                destination_object=destination_object,
                mapping_config=mapping_config,
                mapping_plan=mapping_plan,
                transformation_rules=transformation_rules,
            )
            if text:
                await PipelineLearningStore.upsert_chunk(
                    user_id=str(user_id),
                    pipeline_run_id=run_id,
                    stage="mapping",
                    chunk_type="mapping_template",
                    content_text=text,
                    metadata=chunk_builder.chunk_metadata(
                        source_type=source_type,
                        destination_type=destination_type,
                        destination_object=destination_object,
                        job_id=job_id,
                    ),
                )
        except Exception as exc:
            logger.warning("capture_mapping_ai failed: %s", exc)

    @classmethod
    async def finalize_mapping(
        cls,
        *,
        user_id: str,
        conversation_id: str,
        message_id: Optional[str],
        job_id: Optional[str],
        mapping_config: Dict[str, Any],
        transformation_rules: Optional[Dict[str, Any]] = None,
        mapping_plan: Optional[Dict[str, Any]] = None,
        destination_object: Optional[str] = None,
    ) -> None:
        if not cls.enabled():
            return
        try:
            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if not run_id:
                return
            await PipelineLearningStore.ensure_tables()
            # Load latest mapping AI correction for diff
            from db.connection import database

            ai_row = await database.fetch_one(
                """
                SELECT ai_snapshot, stage_version FROM stage_corrections
                WHERE pipeline_run_id = :run_id AND stage = 'mapping'
                  AND context->>'phase' = 'ai_snapshot'
                ORDER BY created_at DESC LIMIT 1
                """,
                values={"run_id": run_id},
            )
            ai_mapping = {}
            stage_version = 1
            if ai_row:
                import json

                snap = ai_row["ai_snapshot"]
                if isinstance(snap, str):
                    snap = json.loads(snap)
                ai_mapping = (snap or {}).get("mapping_config") or {}
                stage_version = int(ai_row["stage_version"] or 1)

            final_snap = {
                "mapping_config": redact_snapshot_for_storage(mapping_config),
                "transformation_rules": transformation_rules,
                "mapping_plan": mapping_plan,
            }
            diff = diff_mapping_config(ai_mapping, mapping_config)
            await PipelineLearningStore.insert_correction(
                pipeline_run_id=run_id,
                user_id=str(user_id),
                stage="mapping",
                stage_version=stage_version,
                ai_snapshot={"mapping_config": ai_mapping} if ai_mapping else None,
                final_snapshot=final_snap,
                diff=diff,
                context={"job_id": job_id, "message_id": message_id, "phase": "user_final"},
            )
            if message_id:
                from uuid import UUID

                try:
                    UUID(str(message_id))
                    await PipelineLearningStore.update_run(
                        run_id, latest_mapping_message_id=str(message_id)
                    )
                except (TypeError, ValueError):
                    pass
            text = chunk_builder.build_mapping_template_text(
                destination_object=destination_object,
                mapping_config=mapping_config,
                mapping_plan=mapping_plan,
                transformation_rules=transformation_rules,
            )
            if text and diff_has_signal(diff):
                await PipelineLearningStore.upsert_chunk(
                    user_id=str(user_id),
                    pipeline_run_id=run_id,
                    stage="mapping",
                    chunk_type="mapping_template",
                    content_text=text,
                    metadata=chunk_builder.chunk_metadata(
                        destination_object=destination_object,
                        job_id=job_id,
                    ),
                )
        except Exception as exc:
            logger.warning("finalize_mapping failed: %s", exc)

    @classmethod
    async def capture_migration_outcome(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        batch_id: str,
        total_records: int,
        successful_records: int,
        failed_records: int,
        failed_fields: Optional[List[str]] = None,
        error_samples: Optional[List[str]] = None,
        destination_type: Optional[str] = None,
    ) -> None:
        if not cls.enabled():
            return
        try:
            from agent.recovery_policy import migration_chunk_type

            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
                destination_type=destination_type,
            )
            if not run_id:
                return
            total = max(total_records, 1)
            success_rate = successful_records / total
            chunk_type = migration_chunk_type(success_rate)
            await PipelineLearningStore.update_run(
                run_id,
                latest_migration_batch_id=batch_id,
                status="completed" if success_rate >= 0.99 else ("failed" if success_rate < 0.3 else "active"),
            )
            outcome = {
                "success_rate": success_rate,
                "total_records": total_records,
                "successful_records": successful_records,
                "failed_records": failed_records,
                "failed_fields": failed_fields or [],
                "error_samples": (error_samples or [])[:10],
            }
            await PipelineLearningStore.insert_correction(
                pipeline_run_id=run_id,
                user_id=str(user_id),
                stage="migration",
                stage_version=1,
                ai_snapshot=None,
                final_snapshot=None,
                diff=None,
                context={"batch_id": batch_id},
                outcome=outcome,
            )
            text = chunk_builder.build_migration_outcome_text(
                success_rate=success_rate,
                total_records=total_records,
                failed_fields=failed_fields,
                error_samples=error_samples,
            )
            await PipelineLearningStore.upsert_chunk(
                user_id=str(user_id),
                pipeline_run_id=run_id,
                stage="migration",
                chunk_type=chunk_type,
                content_text=text,
                metadata=chunk_builder.chunk_metadata(
                    destination_type=destination_type,
                    success_rate=success_rate,
                ),
            )
            await PipelineLearningStore.update_chunks_success_rate(run_id, success_rate)
            from db.connection import database

            approved_row = await database.fetch_one(
                """
                SELECT content_text, metadata FROM learning_chunks
                WHERE pipeline_run_id = :run_id AND chunk_type = 'plan_approved'
                ORDER BY created_at DESC LIMIT 1
                """,
                values={"run_id": run_id},
            )
            if approved_row:
                row_dict = dict(approved_row)
                content_text = (row_dict.get("content_text") or "").strip()
                if content_text:
                    recipe_body = f"{content_text}\noutcome_success_rate:{success_rate:.3f}"
                    meta = row_dict.get("metadata") or {}
                    if isinstance(meta, str):
                        import json

                        try:
                            meta = json.loads(meta)
                        except (json.JSONDecodeError, TypeError):
                            meta = {}
                    if not isinstance(meta, dict):
                        meta = {}
                    meta["success_rate"] = success_rate
                    await PipelineLearningStore.upsert_chunk(
                        user_id=str(user_id),
                        pipeline_run_id=run_id,
                        stage="planning",
                        chunk_type="pipeline_recipe",
                        content_text=recipe_body[:8000],
                        metadata=meta,
                    )
        except Exception as exc:
            logger.warning("capture_migration_outcome failed: %s", exc)

    @classmethod
    async def capture_recovery_attempt(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        batch_id: str,
        proposal: Dict[str, Any],
        outcome: str = "offered",
    ) -> None:
        if not cls.enabled():
            return
        try:
            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
                destination_type=(proposal.get("failure_report") or {}).get("destination_type"),
            )
            if not run_id:
                return
            decision = proposal.get("decision") or {}
            text_parts = [
                f"recovery_{outcome}",
                f"batch:{batch_id}",
                f"tool:{decision.get('tool_id') or 'ask_user'}",
            ]
            if proposal.get("narrative"):
                text_parts.append(str(proposal["narrative"])[:1500])
            if decision.get("rationale"):
                text_parts.append(str(decision["rationale"])[:800])
            await PipelineLearningStore.upsert_chunk(
                user_id=str(user_id),
                pipeline_run_id=run_id,
                stage="migration",
                chunk_type="recovery_attempt" if outcome == "offered" else f"recovery_{outcome}",
                content_text="\n".join(text_parts)[:8000],
                metadata=chunk_builder.chunk_metadata(
                    batch_id=batch_id,
                    outcome=outcome,
                    requires_write=bool(proposal.get("requires_write")),
                ),
            )
        except Exception as exc:
            logger.warning("capture_recovery_attempt failed: %s", exc)

    @classmethod
    async def capture_recovery_success(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        batch_id: str,
        success_rate: float,
        prior_batch_id: Optional[str] = None,
    ) -> None:
        if not cls.enabled():
            return
        try:
            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
            )
            if not run_id:
                return
            text = (
                f"recovery_success\nbatch:{batch_id}\n"
                f"prior_batch:{prior_batch_id or 'unknown'}\n"
                f"success_rate:{success_rate:.3f}"
            )
            await PipelineLearningStore.upsert_chunk(
                user_id=str(user_id),
                pipeline_run_id=run_id,
                stage="migration",
                chunk_type="recovery_success",
                content_text=text[:8000],
                metadata=chunk_builder.chunk_metadata(
                    batch_id=batch_id,
                    prior_batch_id=prior_batch_id,
                    success_rate=success_rate,
                ),
            )
        except Exception as exc:
            logger.warning("capture_recovery_success failed: %s", exc)

    @classmethod
    def _plan_step_labels(
        cls,
        classification: Dict[str, Any],
        pending: Optional[Dict[str, Any]] = None,
    ) -> List[str]:
        steps = list(classification.get("plan_steps_summary") or [])
        if not steps and pending:
            ep = pending.get("execution_plan") or {}
            if isinstance(ep, dict):
                steps = ep.get("steps") or []
        labels: List[str] = []
        for step in steps:
            if not isinstance(step, dict):
                continue
            label = (step.get("label") or step.get("step_type") or "").strip()
            if label:
                labels.append(label)
        return labels

    @classmethod
    async def _capture_plan_event(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        prompt: str,
        classification: Dict[str, Any],
        chunk_type: str,
        approved: Optional[bool] = None,
        rejected: Optional[bool] = None,
        success_rate: Optional[float] = None,
        pending: Optional[Dict[str, Any]] = None,
        edited_prompt: Optional[str] = None,
    ) -> None:
        if not cls.enabled():
            return
        try:
            source_type = classification.get("source")
            destination_type = classification.get("destination")
            run_id = await cls.ensure_run(
                user_id=str(user_id),
                conversation_id=conversation_id,
                source_type=source_type,
                destination_type=destination_type,
            )
            if not run_id:
                return

            ep = classification.get("execution_plan") or (pending or {}).get("execution_plan") or {}
            meta_ep = ep.get("metadata") if isinstance(ep, dict) else {}
            meta_ep = meta_ep if isinstance(meta_ep, dict) else {}
            pipeline_label = meta_ep.get("pipeline_label")
            pipeline_steps = meta_ep.get("pipeline_steps")

            step_labels = cls._plan_step_labels(classification, pending)
            plan_id = classification.get("plan_id") or (pending or {}).get("plan_id")
            snapshot = {
                "pipeline_label": pipeline_label,
                "pipeline_steps": pipeline_steps,
                "source": source_type,
                "destination": destination_type,
                "source_name": classification.get("source_name"),
                "destination_name": classification.get("destination_name"),
                "plan_status": classification.get("plan_status"),
                "step_labels": step_labels,
                "plan_id": plan_id,
            }
            await PipelineLearningStore.insert_correction(
                pipeline_run_id=run_id,
                user_id=str(user_id),
                stage="planning",
                stage_version=1,
                ai_snapshot=snapshot if chunk_type == "plan_proposed" else None,
                final_snapshot=snapshot if chunk_type in ("plan_approved", "plan_rejected") else None,
                diff={
                    "changed": chunk_type == "plan_rejected",
                    "rejected": chunk_type == "plan_rejected",
                    "edited_prompt": edited_prompt,
                }
                if chunk_type == "plan_rejected"
                else {"changed": chunk_type == "plan_approved", "approved": True},
                context={
                    "plan_id": plan_id,
                    "chunk_type": chunk_type,
                    "prompt_excerpt": (prompt or "")[:500],
                },
            )

            meta = chunk_builder.chunk_metadata(
                source_type=source_type,
                destination_type=destination_type,
                success_rate=success_rate,
                plan_id=str(plan_id) if plan_id else None,
            )
            text = chunk_builder.build_plan_snapshot_text(
                prompt=prompt,
                pipeline_label=pipeline_label,
                pipeline_steps=list(pipeline_steps) if isinstance(pipeline_steps, list) else None,
                step_labels=step_labels,
                source_type=source_type,
                destination_type=destination_type,
                source_name=classification.get("source_name"),
                destination_name=classification.get("destination_name"),
                plan_status=classification.get("plan_status"),
                approved=approved,
                rejected=rejected,
            )
            if text.strip():
                await PipelineLearningStore.upsert_chunk(
                    user_id=str(user_id),
                    pipeline_run_id=run_id,
                    stage="planning",
                    chunk_type=chunk_type,
                    content_text=text,
                    metadata=meta,
                )
            intent = chunk_builder.build_user_intent_text(
                prompt=prompt,
                source_type=source_type,
                destination_type=destination_type,
            )
            if intent.strip() and chunk_type == "plan_proposed":
                await PipelineLearningStore.upsert_chunk(
                    user_id=str(user_id),
                    pipeline_run_id=run_id,
                    stage="planning",
                    chunk_type="user_intent",
                    content_text=intent,
                    metadata=meta,
                )
        except Exception as exc:
            logger.warning("_capture_plan_event failed: %s", exc)

    @classmethod
    async def capture_plan_proposed(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        prompt: str,
        classification: Dict[str, Any],
    ) -> None:
        await cls._capture_plan_event(
            user_id=user_id,
            conversation_id=conversation_id,
            prompt=prompt,
            classification=classification,
            chunk_type="plan_proposed",
        )

    @classmethod
    async def capture_plan_approved(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        prompt: str,
        classification: Dict[str, Any],
        pending: Optional[Dict[str, Any]] = None,
    ) -> None:
        await cls._capture_plan_event(
            user_id=user_id,
            conversation_id=conversation_id,
            prompt=prompt,
            classification=classification,
            chunk_type="plan_approved",
            approved=True,
            pending=pending,
        )

    @classmethod
    async def capture_plan_rejected(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        prompt: str,
        classification: Dict[str, Any],
        pending: Optional[Dict[str, Any]] = None,
        edited_prompt: Optional[str] = None,
    ) -> None:
        await cls._capture_plan_event(
            user_id=user_id,
            conversation_id=conversation_id,
            prompt=prompt,
            classification=classification,
            chunk_type="plan_rejected",
            rejected=True,
            success_rate=0.0,
            pending=pending,
            edited_prompt=edited_prompt,
        )

    @classmethod
    async def get_planning_retrieval_context(
        cls,
        *,
        user_id: str,
        query_text: str,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> str:
        """Prefer approved plans; fall back to user_intent chunks."""
        ctx = await cls.get_retrieval_context(
            user_id=user_id,
            query_text=query_text,
            stage="planning",
            chunk_type="plan_approved",
            source_type=source_type,
            destination_type=destination_type,
            caller="goal_parser",
            conversation_id=conversation_id,
        )
        if ctx:
            return ctx
        return await cls.get_retrieval_context(
            user_id=user_id,
            query_text=query_text,
            stage="planning",
            chunk_type="user_intent",
            source_type=source_type,
            destination_type=destination_type,
            caller="goal_parser",
            conversation_id=conversation_id,
        )

    @classmethod
    async def retrieve(
        cls,
        *,
        user_id: str,
        query_text: str,
        stage: Optional[str] = None,
        chunk_type: Optional[str] = None,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        top_k: Optional[int] = None,
        caller: Optional[str] = None,
        conversation_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        if not cls.enabled():
            return []
        try:
            return await retrieve_similar_chunks(
                user_id=str(user_id),
                query_text=query_text,
                stage=stage,
                chunk_type=chunk_type,
                source_type=source_type,
                destination_type=destination_type,
                top_k=top_k,
                caller=caller or "retrieve_tool",
                conversation_id=conversation_id,
            )
        except Exception as exc:
            logger.warning("retrieve failed: %s", exc)
            return []

    @classmethod
    async def get_retrieval_context(
        cls,
        *,
        user_id: str,
        query_text: str,
        stage: Optional[str] = None,
        chunk_type: Optional[str] = None,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        caller: Optional[str] = None,
        conversation_id: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> str:
        chunks = await cls.retrieve(
            user_id=user_id,
            query_text=query_text,
            stage=stage,
            chunk_type=chunk_type,
            source_type=source_type,
            destination_type=destination_type,
            caller=caller or "get_retrieval_context",
            conversation_id=conversation_id,
            top_k=top_k,
        )
        return cls.format_retrieval_context(chunks)

    @staticmethod
    def format_retrieval_context(chunks: List[Dict[str, Any]], *, max_chars: int = 4000) -> str:
        if not chunks:
            return ""
        lines = ["Similar past pipeline examples (use as hints, do not copy blindly):"]
        used = 0
        for i, ch in enumerate(chunks, 1):
            block = f"\n--- Example {i} ({ch.get('chunk_type', '')}) ---\n{ch.get('content_text', '')[:1200]}"
            if used + len(block) > max_chars:
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines)

    @classmethod
    async def capture_tool_trace(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        prompt: str,
        steps: List[Dict[str, Any]],
        partial: bool = False,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        job_id: Optional[str] = None,
        mode: Optional[str] = None,
    ) -> None:
        if not cls.enabled() or not steps:
            return
        try:
            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
                source_type=source_type,
                destination_type=destination_type,
            )
            if not run_id:
                return
            if job_id:
                await PipelineLearningStore.update_run(run_id, latest_job_id=job_id)
            chunk_type = "tool_trace_partial" if partial else "tool_trace"
            text = chunk_builder.build_tool_trace_text(
                prompt=prompt,
                steps=steps,
                source_type=source_type,
                destination_type=destination_type,
                mode=mode,
                partial=partial,
                job_id=job_id,
            )
            if not text.strip():
                return
            tool_ids = [str(s.get("tool_id")) for s in steps if s.get("tool_id")]
            meta = chunk_builder.chunk_metadata(
                source_type=source_type,
                destination_type=destination_type,
                job_id=job_id,
            )
            meta["tool_ids"] = tool_ids
            meta["partial"] = partial
            if mode:
                meta["mode"] = mode
            await PipelineLearningStore.upsert_chunk(
                user_id=str(user_id),
                pipeline_run_id=run_id,
                stage="agent",
                chunk_type=chunk_type,
                content_text=text[:6000],
                metadata=meta,
            )
            if not partial and len(steps) >= 2:
                platform_text = chunk_builder.build_platform_tool_pattern_text(
                    steps=steps,
                    source_type=source_type,
                    destination_type=destination_type,
                    mode=mode,
                )
                if platform_text.strip():
                    platform_meta = chunk_builder.chunk_metadata(
                        source_type=source_type,
                        destination_type=destination_type,
                    )
                    platform_meta["tool_ids"] = tool_ids
                    platform_meta["anonymized"] = True
                    if mode:
                        platform_meta["mode"] = mode
                    await PipelineLearningStore.upsert_chunk(
                        user_id=str(user_id),
                        pipeline_run_id=run_id,
                        stage="agent",
                        chunk_type="platform_tool_pattern",
                        content_text=platform_text[:4000],
                        metadata=platform_meta,
                    )
        except Exception as exc:
            logger.warning("capture_tool_trace failed: %s", exc)

    @classmethod
    async def capture_tool_pick(
        cls,
        *,
        user_id: str,
        conversation_id: Optional[str],
        prompt: str,
        pick_source: str,
        actual_tool_ids: List[str],
        shadow_tool_ids: List[str],
        decisions: Optional[List[Dict[str, Any]]] = None,
        mode: Optional[str] = None,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
    ) -> None:
        if not cls.enabled():
            return
        try:
            run_id = await cls.ensure_run(
                user_id=user_id,
                conversation_id=conversation_id,
                source_type=source_type,
                destination_type=destination_type,
            )
            if not run_id:
                return
            first_match = (
                shadow_tool_ids[0] == actual_tool_ids[0]
                if shadow_tool_ids and actual_tool_ids
                else None
            )
            text = chunk_builder.build_tool_pick_text(
                prompt=prompt,
                pick_source=pick_source,
                actual_tool_ids=actual_tool_ids,
                shadow_tool_ids=shadow_tool_ids,
                mode=mode,
                first_match=first_match,
            )
            meta = chunk_builder.chunk_metadata(
                source_type=source_type,
                destination_type=destination_type,
            )
            meta["pick_source"] = pick_source
            meta["actual_tool_ids"] = actual_tool_ids
            meta["shadow_tool_ids"] = shadow_tool_ids
            meta["first_match"] = first_match
            if decisions:
                meta["decision_count"] = len(decisions)
            await PipelineLearningStore.upsert_chunk(
                user_id=str(user_id),
                pipeline_run_id=run_id,
                stage="agent",
                chunk_type="tool_pick",
                content_text=text[:4000],
                metadata=meta,
            )
        except Exception as exc:
            logger.warning("capture_tool_pick failed: %s", exc)

    @classmethod
    async def retrieve_tool_traces(
        cls,
        *,
        user_id: str,
        query_text: str,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        conversation_id: Optional[str] = None,
        top_k: Optional[int] = None,
        caller: Optional[str] = None,
        include_partial: bool = True,
    ) -> List[Dict[str, Any]]:
        if not cls.enabled():
            return []
        from services.pipeline_learning.retrieve import (
            _retrieve_platform_chunks,
            retrieve_similar_chunks,
        )
        from services.pipeline_learning.trace_retrieval_policy import (
            include_user_retrieval,
            platform_retrieval_enabled,
            trace_retrieval_policy,
        )

        k = top_k or 5
        merged: List[Dict[str, Any]] = []
        seen_ids: set[str] = set()

        def _append(chunks: List[Dict[str, Any]]) -> None:
            for ch in chunks:
                cid = str(ch.get("id") or "")
                if cid and cid in seen_ids:
                    continue
                if cid:
                    seen_ids.add(cid)
                ch["retrieval_policy"] = trace_retrieval_policy()
                merged.append(ch)
                if len(merged) >= k:
                    break

        if include_user_retrieval():
            user_traces = await retrieve_similar_chunks(
                user_id=str(user_id),
                query_text=query_text,
                chunk_type="tool_trace",
                source_type=source_type,
                destination_type=destination_type,
                top_k=k,
                caller=caller or "tool_trace_retrieve",
                conversation_id=conversation_id,
                scope="user",
            )
            _append(user_traces)
            if include_partial and len(merged) < k:
                partial = await retrieve_similar_chunks(
                    user_id=str(user_id),
                    query_text=query_text,
                    chunk_type="tool_trace_partial",
                    source_type=source_type,
                    destination_type=destination_type,
                    top_k=k - len(merged),
                    caller=caller or "tool_trace_retrieve",
                    conversation_id=conversation_id,
                    scope="user",
                )
                _append(partial)

        if platform_retrieval_enabled() and len(merged) < k:
            platform = await _retrieve_platform_chunks(
                user_id=str(user_id),
                query_text=query_text,
                source_type=source_type,
                destination_type=destination_type,
                top_k=k - len(merged),
                caller=caller or "tool_trace_retrieve",
                conversation_id=conversation_id,
            )
            _append(platform)

        merged.sort(
            key=lambda x: float(x.get("score") or x.get("similarity") or 0),
            reverse=True,
        )
        return merged[:k]

    @classmethod
    async def get_tool_trace_context(
        cls,
        *,
        user_id: str,
        query_text: str,
        source_type: Optional[str] = None,
        destination_type: Optional[str] = None,
        conversation_id: Optional[str] = None,
        caller: Optional[str] = None,
        top_k: Optional[int] = None,
    ) -> str:
        chunks = await cls.retrieve_tool_traces(
            user_id=user_id,
            query_text=query_text,
            source_type=source_type,
            destination_type=destination_type,
            conversation_id=conversation_id,
            caller=caller,
            top_k=top_k,
        )
        if not chunks:
            return ""
        lines = ["Similar past tool execution traces (prefer adapting these sequences):"]
        used = 0
        max_chars = 4000
        for i, ch in enumerate(chunks, 1):
            block = (
                f"\n--- Trace {i} ({ch.get('chunk_type', '')}) ---\n"
                f"{ch.get('content_text', '')[:1200]}"
            )
            if used + len(block) > max_chars:
                break
            lines.append(block)
            used += len(block)
        return "\n".join(lines)

    @classmethod
    def schedule_capture(cls, coro_factory) -> None:
        """Fire-and-forget async capture without blocking callers."""
        if not cls.enabled():
            return
        try:
            loop = asyncio.get_running_loop()
            coro = coro_factory() if callable(coro_factory) else coro_factory
            loop.create_task(coro)
        except RuntimeError:
            pass
