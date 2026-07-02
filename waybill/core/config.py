"""Configuration for Waybill.

Single settings object read from environment variables, so the same code runs
locally, in Docker Compose, and on EKS (where these come from ConfigMaps and
Secrets). Nothing here is Ollama-specific beyond defaults — swapping the model
provider later is a config change, not a code change.
"""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    # --- LLM provider (Ollama by default) ---
    ollama_host: str = os.getenv("OLLAMA_HOST", "http://localhost:11434")
    ollama_model: str = os.getenv("OLLAMA_MODEL", "llama3.1:8b")
    llm_timeout_s: float = float(os.getenv("LLM_TIMEOUT_S", "30"))

    # --- Agent behaviour ---
    # Below this confidence the agent escalates to a human instead of acting.
    # This single threshold is the heart of the human-in-the-loop design.
    escalation_threshold: float = float(os.getenv("ESCALATION_THRESHOLD", "0.75"))
    # Exceptions at or above this severity always get a human, regardless of
    # confidence — you don't let the agent silently auto-handle a critical.
    always_escalate_severity: str = os.getenv("ALWAYS_ESCALATE_SEVERITY", "critical")

    # --- Queue / infra (used from Phase 2 onward) ---
    queue_url: str = os.getenv("QUEUE_URL", "memory://local")

    # --- Data ---
    random_seed: int = int(os.getenv("RANDOM_SEED", "42"))


settings = Settings()
