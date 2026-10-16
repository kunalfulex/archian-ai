"""Lineage-aware migration mapping lookup (post-extract job versions)."""
from __future__ import annotations

from typing import Optional, Tuple

from services.migration.context import MigrationContext
from services.migration.context_repository import get_context


async def is_lineage_ancestor(
    user_id: str,
    ancestor_job_id: str,
    job_id: str,
) -> bool:
    """True when ``ancestor_job_id`` is the same job or an ancestor of ``job_id``."""
    ancestor = (ancestor_job_id or "").strip()
    target = (job_id or "").strip()
    if not ancestor or not target:
        return False
    if ancestor == target:
        return True

    from services.extraction_results_service import ExtractionResultsService

    lineage = await ExtractionResultsService.get_lineage_job_ids(target, user_id)
    if not lineage or ancestor not in lineage or target not in lineage:
        return False
    return lineage.index(ancestor) <= lineage.index(target)


async def find_lineage_mapping_context(
    *,
    user_id: str,
    conversation_id: str,
    target_job_id: str,
) -> Optional[Tuple[MigrationContext, str]]:
    """
    Find the nearest migration context with mapping on ``target_job_id`` or a lineage ancestor.

    Returns (stored context, job_id that owns the mapping) or None.
    Walks newest → oldest so the most recent mapping wins.
    """
    target = (target_job_id or "").strip()
    if not target or not conversation_id:
        return None

    from services.extraction_results_service import ExtractionResultsService

    lineage = await ExtractionResultsService.get_lineage_job_ids(target, user_id)
    if not lineage:
        lineage = [target]

    for jid in reversed(lineage):
        ctx = await get_context(
            user_id=str(user_id),
            conversation_id=str(conversation_id),
            extraction_job_id=str(jid),
        )
        if ctx and ctx.mapping:
            return ctx, str(jid)
    return None
