"""Concurrency and batch limits for the labeling system."""
from __future__ import annotations

import os


class LabelingConfig:
    @staticmethod
    def max_concurrency() -> int:
        try:
            return max(1, min(20, int(os.getenv("LABELING_MAX_CONCURRENCY", "5"))))
        except ValueError:
            return 5

    @staticmethod
    def batch_size() -> int:
        try:
            return max(1, min(100, int(os.getenv("LABELING_BATCH_SIZE", "10"))))
        except ValueError:
            return 10

    @staticmethod
    def profile_rollup_min_ratio() -> float:
        try:
            return max(0.0, min(1.0, float(os.getenv("LABELING_PROFILE_ROLLUP_MIN", "0.8"))))
        except ValueError:
            return 0.8

    @staticmethod
    def refinement_debounce_sec() -> int:
        try:
            return max(5, min(600, int(os.getenv("LABELING_REFINEMENT_DEBOUNCE_SEC", "60"))))
        except ValueError:
            return 60
