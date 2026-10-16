"""Connector and entity labeling (schema bootstrap + background jobs)."""

from services.labeling.config import LabelingConfig
from services.labeling.schedule import schedule_entity_labeling

__all__ = ["LabelingConfig", "schedule_entity_labeling"]
