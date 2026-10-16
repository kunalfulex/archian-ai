"""Salesforce org sync-status and schema browse endpoints."""

import traceback
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse

from db.connection import database
from routes.auth import get_current_user

router = APIRouter()


@router.get("/api/salesforce-orgs/{org_id}/sync-status")
async def get_salesforce_sync_status(
    org_id: str,
    user_id: UUID = Depends(get_current_user)
):
    """Check if Salesforce connector agent readiness is complete for a specific org."""
    try:
        org_id = await _assert_user_salesforce_org(user_id, org_id)
        from services.connectors.salesforce_ref import resolve_salesforce_token
        from services.labeling.readiness_service import get_readiness_state

        token = await resolve_salesforce_token(str(user_id), org_id)
        if not token:
            raise HTTPException(status_code=404, detail="Salesforce org not found")

        connector_ref = {
            "connector_id": token["connector_id"],
            "org_id": token["org_id"],
        }
        state = await get_readiness_state(
            user_id=str(user_id),
            connector_type="salesforce",
            connector_instance_id=token["connector_id"],
            connector_ref=connector_ref,
        )

        result = await database.fetch_one(
            """
            SELECT 
                COUNT(*) as total_objects,
                COUNT(category) FILTER (WHERE category IS NOT NULL) as categorized_objects
            FROM salesforce_objects 
            WHERE org_id = :org_id AND user_id = :user_id
            """,
            values={"org_id": org_id, "user_id": str(user_id)}
        )
        
        total_objects = result["total_objects"] if result else 0
        categorized_objects = result["categorized_objects"] if result else 0
        categorization_percentage = (categorized_objects / total_objects * 100) if total_objects > 0 else 0
        
        return JSONResponse(
            content={
                "status": "success",
                "sync_complete": state["is_ready"],
                "phase": state["phase"],
                "display_name": state["display_name"],
                "entity_labeling_stats": state["entity_labeling_stats"],
                "total_objects": total_objects,
                "categorized_objects": categorized_objects,
                "categorization_percentage": round(categorization_percentage, 2),
                "org_id": org_id,
                "connector_id": token["connector_id"],
            }
        )
        
    except Exception as e:
        print(f"❌ Error checking Salesforce sync status: {e}")
        import traceback
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=f"Error checking Salesforce sync status: {str(e)}")


async def _resolve_user_salesforce_org_id(user_id: UUID, connector_ref_or_id: str) -> str:
    from services.connectors.salesforce_ref import resolve_salesforce_org_id

    org_id = await resolve_salesforce_org_id(str(user_id), connector_ref_or_id)
    if not org_id:
        raise HTTPException(status_code=404, detail="Salesforce org not found or access denied")
    return org_id


async def _assert_user_salesforce_org(user_id: UUID, connector_ref_or_id: str) -> str:
    """Resolve token UUID or org_id to org_id for schema metadata queries."""
    return await _resolve_user_salesforce_org_id(user_id, connector_ref_or_id)


def _iso_utc(dt) -> Optional[str]:
    if dt is None:
        return None
    if hasattr(dt, "isoformat"):
        s = dt.isoformat()
        if hasattr(dt, "tzinfo") and dt.tzinfo is None:
            return s + "Z"
        return s
    return str(dt)


@router.get("/api/salesforce-orgs/{org_id}/schema/summary")
async def get_salesforce_schema_summary(
    org_id: str,
    user_id: UUID = Depends(get_current_user),
):
    """Total synced objects and last metadata refresh time (from stored object rows)."""
    try:
        org_id = await _assert_user_salesforce_org(user_id, org_id)
        total_row = await database.fetch_one(
            """
            SELECT COUNT(*)::int AS c
            FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id
            """,
            values={"user_id": str(user_id), "org_id": org_id},
        )
        last_row = await database.fetch_one(
            """
            SELECT MAX(created_at) AS ts
            FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id
            """,
            values={"user_id": str(user_id), "org_id": org_id},
        )
        total = int(total_row["c"]) if total_row else 0
        ts = last_row["ts"] if last_row else None
        return JSONResponse(
            content={
                "status": "success",
                "org_id": org_id,
                "total_objects": total,
                "last_refreshed_at": _iso_utc(ts),
                "schema_ready": total > 0,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ schema summary: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/salesforce-orgs/{org_id}/schema/objects")
async def list_salesforce_schema_objects(
    org_id: str,
    page: int = Query(1, ge=1),
    page_size: int = Query(25, ge=1, le=100),
    q: Optional[str] = Query(None),
    user_id: UUID = Depends(get_current_user),
):
    """Paginated, searchable list of Salesforce object API names and labels from synced schema."""
    try:
        org_id = await _assert_user_salesforce_org(user_id, org_id)
        page_size = min(max(page_size, 1), 100)
        offset = (page - 1) * page_size
        uid = str(user_id)
        search = (q or "").strip()
        if search:
            like = f"%{search}%"
            count_row = await database.fetch_one(
                """
                SELECT COUNT(*)::int AS c
                FROM salesforce_objects
                WHERE user_id = :user_id AND org_id = :org_id
                  AND (object_api_name ILIKE :like OR COALESCE(label, '') ILIKE :like)
                """,
                values={"user_id": uid, "org_id": org_id, "like": like},
            )
            rows = await database.fetch_all(
                """
                SELECT object_api_name, label
                FROM salesforce_objects
                WHERE user_id = :user_id AND org_id = :org_id
                  AND (object_api_name ILIKE :like OR COALESCE(label, '') ILIKE :like)
                ORDER BY object_api_name ASC
                LIMIT :limit OFFSET :offset
                """,
                values={
                    "user_id": uid,
                    "org_id": org_id,
                    "like": like,
                    "limit": page_size,
                    "offset": offset,
                },
            )
        else:
            count_row = await database.fetch_one(
                """
                SELECT COUNT(*)::int AS c
                FROM salesforce_objects
                WHERE user_id = :user_id AND org_id = :org_id
                """,
                values={"user_id": uid, "org_id": org_id},
            )
            rows = await database.fetch_all(
                """
                SELECT object_api_name, label
                FROM salesforce_objects
                WHERE user_id = :user_id AND org_id = :org_id
                ORDER BY object_api_name ASC
                LIMIT :limit OFFSET :offset
                """,
                values={
                    "user_id": uid,
                    "org_id": org_id,
                    "limit": page_size,
                    "offset": offset,
                },
            )
        total = int(count_row["c"]) if count_row else 0
        items = [
            {"object_api_name": r["object_api_name"], "label": r["label"] or ""}
            for r in (rows or [])
        ]
        return JSONResponse(
            content={
                "status": "success",
                "org_id": org_id,
                "items": items,
                "total": total,
                "page": page,
                "page_size": page_size,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ schema objects list: {e}")
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/api/salesforce-orgs/{org_id}/schema/objects/{object_api_name}/fields")
async def list_salesforce_schema_object_fields(
    org_id: str,
    object_api_name: str,
    user_id: UUID = Depends(get_current_user),
):
    """Field API names and data types for one object (from synced describe)."""
    try:
        org_id = await _assert_user_salesforce_org(user_id, org_id)
        obj_check = await database.fetch_one(
            """
            SELECT label FROM salesforce_objects
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :object_api_name
            LIMIT 1
            """,
            values={
                "user_id": str(user_id),
                "org_id": org_id,
                "object_api_name": object_api_name,
            },
        )
        if not obj_check:
            raise HTTPException(status_code=404, detail="Object not found in synced schema for this org")
        rows = await database.fetch_all(
            """
            SELECT field_api_name, data_type
            FROM salesforce_object_fields
            WHERE user_id = :user_id AND org_id = :org_id AND object_api_name = :object_api_name
            ORDER BY field_api_name ASC
            """,
            values={
                "user_id": str(user_id),
                "org_id": org_id,
                "object_api_name": object_api_name,
            },
        )
        fields = [
            {"api_name": r["field_api_name"], "type": r["data_type"] or ""}
            for r in (rows or [])
        ]
        return JSONResponse(
            content={
                "status": "success",
                "org_id": org_id,
                "object_api_name": object_api_name,
                "object_label": obj_check["label"] or object_api_name,
                "fields": fields,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        print(f"❌ schema fields: {e}")
        raise HTTPException(status_code=500, detail=str(e))
