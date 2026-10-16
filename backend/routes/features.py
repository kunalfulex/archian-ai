"""Edition/feature flags for signed-in users so the frontend can hide unavailable features."""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends

from routes.auth import get_current_user
from services.entitlements import get_entitlements

router = APIRouter(tags=["features"])


@router.get("/features")
async def get_features(user_id: UUID = Depends(get_current_user)):
    entitlements = get_entitlements()
    return {
        "edition": entitlements.edition,
        "billing": entitlements.billing_enabled,
    }
