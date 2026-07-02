from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class ModelConfig:
    classify: str
    draft: str
    review: str


def load_model_config() -> ModelConfig:
    return ModelConfig(
        classify=os.getenv("OR_CLASSIFY_MODEL", "mistralai/mistral-small-3.2-24b-instruct"),
        draft=os.getenv("OR_DRAFT_MODEL", "google/gemini-2.5-flash"),
        review=os.getenv("OR_REVIEW_MODEL", "google/gemini-2.5-flash-lite"),
    )

