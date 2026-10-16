from __future__ import annotations

from services.connectors.registry import get_connector


def get_adapter(connector_type: str, user_id: str):
    spec = get_connector(connector_type)
    if spec is None or spec.assessment_adapter is None:
        raise ValueError(f"Unsupported connector type: {connector_type}")
    return spec.assessment_adapter(user_id)
