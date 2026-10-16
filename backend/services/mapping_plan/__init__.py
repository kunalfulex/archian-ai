"""Mapping plan pipeline: parse user instruction, resolve destination target, execute map."""

from services.mapping.service import execute_mapping

__all__ = ["execute_mapping"]
