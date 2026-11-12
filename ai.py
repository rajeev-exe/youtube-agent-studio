"""AI access layer with JSON extraction and llm-style validation."""
from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from typing import Callable

import requests

from config import Config
from storage import Database

log = logging.getLogger("yta.ai")


def extract_json(text: str) -> dict:
    t = text.strip()
    t = re.sub(r"^```(?:json)?\\s*|\\s*```$", "", t, flags=re.S)
    try:
        value = json.loads(t)
    except ValueError:
        a, b = t.find("{"), t.rfind("}")
        if a < 0 or b <= a:
            raise ValueError("response contains no JSON object")
        value = json.loads(t[a:b + 1])
    if not isinstance(value, dict):
        raise ValueError("response JSON is not an object")
    return value


class AIClient:
    def __init__(self, cfg: Config, db: Database | None = None):
        self.cfg = cfg
        self.db = db
        self.session = requests.Session()
        self.checked = False
        self.usable = {"ollama": False, "openrouter": False}
        self.detail = {}

    def check(self, timeout: float | None = None) -> dict:
        """Check configured providers within a shared time budget."""
        deadline = time.monotonic() + (
            timeout if timeout is not None else self.cfg.ai_timeout_seconds
        )

        def remaining(limit: float) -> float:
            return min(limit, deadline - time.monotonic())

        try:
            budget = remaining(2.0)
            if budget <= 0:
                raise requests.Timeout("AI provider check timed out")
            r = self.session.get(
                f"{self.cfg.ollama_base_url}/api/tags", timeout=budget)
            r.raise_for_status()
            names = {m.get("name", "") for m in r.json().get("models", [])}
            want = self.cfg.ollama_model
            ok = (
                want in names
                or f"{want}:latest" in names
                or any(n.split(":")[0] == want for n in names)
            )
            self.usable["ollama"] = ok
            self.detail["ollama"] = f"reachable, model {want} ready" if ok else f"not ready: {want}"
        except Exception as exc:
            self.usable["ollama"] = False
            self.detail["ollama"] = f"not reachable: {exc}"

        if self.cfg.openrouter_api_key and time.monotonic() < deadline:
            try:
                budget = remaining(2.0)
                if budget <= 0:
                    raise requests.Timeout("AI provider check timed out")
                r = self.session.get(
                    f"{self.cfg.openrouter_base_url}/key",
                    headers={
                        "Authorization": f"Bearer {self.cfg.openrouter_api_key}"},
                    timeout=budget,
                )
                r.raise_for_status()
                self.usable["openrouter"] = True
                self.detail["openrouter"] = f"reachable, key accepted for {self.cfg.openrouter_model}"
            except requests.RequestException as exc:
                self.usable["openrouter"] = False
                self.detail["openrouter"] = f"not available: {exc}"
        else:
            self.usable["openrouter"] = False
            self.detail["openrouter"] = (
                "no API key configured" if not self.cfg.openrouter_api_key
                else "not checked: AI timeout budget exhausted"
            )

        self.checked = True
        return {"ok": any(self.usable.values()), "providers": dict(self.usable), "detail": dict(self.detail)}

    @property
    def available(self) -> bool:
        return any(self.usable.values())

    def _call(self, provider: str, system: str, user: str, timeout: float | None = None) -> str:
        request_timeout = timeout if timeout is not None else self.cfg.ai_timeout_seconds
        if provider == "ollama":
            r = self.session.post(
                f"{self.cfg.ollama_base_url}/api/chat",
                json={
                    "model": self.cfg.ollama_model,
                    "stream": False,
                    "format": "json",
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                },
                timeout=request_timeout,
            )
            r.raise_for_status()
            return r.json()["message"]["content"]

        r = self.session.post(
            f"{self.cfg.openrouter_base_url}/chat/completions",
            headers={"Authorization": f"Bearer {self.cfg.openrouter_api_key}"},
            json={
                "model": self.cfg.openrouter_model,
                "temperature": 0.2,
                "messages": [
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
            },
            timeout=request_timeout,
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]

    def _order(self) -> list[str]:
        first = self.cfg.ai_provider
        return [p for p in ([first] + [x for x in ("ollama", "openrouter") if x != first]) if self.usable.get(p)]

    def chat_json(self, system: str, user: str, validator: Callable[[dict], dict], namespace: str) -> tuple[dict, str] | None:
        deadline = time.monotonic() + self.cfg.ai_timeout_seconds
        key = hashlib.sha256(
            f"{namespace}\\n{system}\\n{user}".encode()).hexdigest()
        if self.db:
            hit = self.db.cache_get(key)
            if hit:
                try:
                    return validator(json.loads(hit[0])), hit[1]
                except Exception:
                    pass

        if not self.checked:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.check(timeout=remaining)

        for provider in self._order():
            prompt = user
            for attempt in range(1, self.cfg.max_retries + 1):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log.warning("AI classification timed out before provider %s completed", provider)
                    return None
                try:
                    raw = self._call(provider, system, prompt, timeout=remaining)
                    data = validator(extract_json(raw))
                except Exception as exc:
                    msg = str(exc)[:200]
                    log.warning(
                        "AI (%s) invalid response attempt %d: %s", provider, attempt, msg)
                    prompt = user + \
                        f"\\n\\nPrevious answer rejected: {msg}. Return ONLY valid JSON."
                    if attempt >= self.cfg.max_retries:
                        break
                    pause = min(2 ** attempt, 20, max(0.0, deadline - time.monotonic()))
                    if pause > 0:
                        time.sleep(pause)
                    continue

                if self.db:
                    self.db.cache_put(key, json.dumps(data), provider)
                return data, provider
        return None
