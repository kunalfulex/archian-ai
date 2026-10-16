"""Planner rollout helpers."""

import os

from services.planning import rollout


def test_deployment_stage_defaults_development(monkeypatch):
    monkeypatch.delenv("DEPLOYMENT_STAGE", raising=False)
    monkeypatch.delenv("RENDER_SERVICE_NAME", raising=False)
    assert rollout.deployment_stage() == "development"


def test_deployment_stage_production(monkeypatch):
    monkeypatch.setenv("DEPLOYMENT_STAGE", "production")
    assert rollout.deployment_stage() == "production"


def test_capabilities_always_on():
    caps = rollout.current_planner_capabilities()
    assert caps["planning_pipeline"] == "on"
    assert caps["plan_approval"] == "client_ask_act"


def test_validate_planner_rollout_no_warnings():
    assert rollout.validate_planner_rollout() == []
