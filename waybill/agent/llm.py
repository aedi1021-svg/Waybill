"""Thin Ollama client.

Wraps the local Ollama HTTP API and always returns text. Two design choices
that matter for a production-minded portfolio project:

1. Timeouts and errors never crash the pipeline — the caller decides how to
   handle a degraded LLM (the agent treats an LLM failure as low confidence and
   escalates, matching the fail-safe philosophy from the trading-bot advisory
   layer).
2. `available()` lets tests and CI run without a live model, so the eval harness
   and unit tests don't hard-depend on Ollama being up.
"""
from __future__ import annotations

import json
from typing import Optional

import httpx

from waybill.core.config import settings
from waybill.obs.tracing import get_tracer

_tracer = get_tracer("waybill.llm")


class LLMUnavailable(RuntimeError):
    pass


class OllamaClient:
    def __init__(self, host: Optional[str] = None, model: Optional[str] = None) -> None:
        self.host = (host or settings.ollama_host).rstrip("/")
        self.model = model or settings.ollama_model
        self._timeout = settings.llm_timeout_s

    def available(self) -> bool:
        try:
            r = httpx.get(f"{self.host}/api/tags", timeout=2.0)
            return r.status_code == 200
        except Exception:
            return False

    def generate(self, prompt: str, *, system: str = "", json_mode: bool = False) -> str:
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "system": system,
            "stream": False,
        }
        if json_mode:
            payload["format"] = "json"
        with _tracer.start_as_current_span("llm.generate") as span:
            span.set_attribute("llm.model", self.model)
            span.set_attribute("llm.json_mode", json_mode)
            try:
                r = httpx.post(
                    f"{self.host}/api/generate", json=payload, timeout=self._timeout
                )
                r.raise_for_status()
                return r.json().get("response", "")
            except Exception as exc:  # network, timeout, bad status
                span.set_attribute("llm.error", str(exc)[:200])
                raise LLMUnavailable(str(exc)) from exc

    def generate_json(self, prompt: str, *, system: str = "") -> dict:
        raw = self.generate(prompt, system=system, json_mode=True)
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Best-effort salvage: find the first {...} block.
            start, end = raw.find("{"), raw.rfind("}")
            if start != -1 and end != -1:
                return json.loads(raw[start : end + 1])
            raise
