"""Minimal DeepSeek JSON client used only by explicit paid evaluation configs."""

from __future__ import annotations

import asyncio
import json
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from opspilot.models import RootCauseType


class DeepSeekSecrets(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    deepseek_api_key: SecretStr


class TokenUsage(BaseModel):
    model_config = ConfigDict(extra="ignore")

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)
    prompt_cache_hit_tokens: int = Field(default=0, ge=0)
    prompt_cache_miss_tokens: int = Field(default=0, ge=0)


class DeepSeekDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    candidate_types: list[RootCauseType] = Field(min_length=1, max_length=3)
    evidence_types: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1, max_length=1000)


class DeepSeekResult(BaseModel):
    decision: DeepSeekDecision
    usage: TokenUsage
    model: str
    finish_reason: str


ROOT_CAUSE_GUIDE = {
    RootCauseType.DB_REPLICATION_LAG.value: "database replica lag",
    RootCauseType.DB_SLOW_QUERY.value: "slow database queries",
    RootCauseType.DB_CONNECTION_EXHAUSTED.value: "database connection pool exhaustion",
    RootCauseType.REDIS_MEMORY_PRESSURE.value: "Redis memory pressure",
    RootCauseType.REDIS_LOW_HIT_RATE.value: "low Redis cache hit rate",
    RootCauseType.KAFKA_CONSUMER_LAG.value: "Kafka consumer lag",
    RootCauseType.RPC_TIMEOUT.value: "downstream RPC timeout or extreme latency",
    RootCauseType.RPC_ERROR_RATE.value: "downstream RPC error rate",
    RootCauseType.BAD_DEPLOYMENT.value: "recent risky deployment",
    RootCauseType.RESOURCE_SATURATION.value: "CPU or memory saturation",
    RootCauseType.OOM_RESTART.value: "out-of-memory restart",
    RootCauseType.NO_FAULT.value: "normal or noisy alert without material fault",
}


class DeepSeekRCAClient:
    def __init__(
        self,
        *,
        api_key: SecretStr | str,
        model: str,
        base_url: str = "https://api.deepseek.com",
        timeout_seconds: float = 60,
        max_tokens: int = 500,
        max_attempts: int = 3,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._api_key = api_key if isinstance(api_key, SecretStr) else SecretStr(api_key)
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_tokens = max_tokens
        self.max_attempts = max_attempts
        self.transport = transport

    @classmethod
    def from_config(cls, config: dict[str, Any]) -> DeepSeekRCAClient:
        model_config = config.get("model", {})
        secrets = DeepSeekSecrets()
        return cls(
            api_key=secrets.deepseek_api_key,
            model=model_config.get("name", "deepseek-v4-flash"),
            base_url=model_config.get("base_url", "https://api.deepseek.com"),
            timeout_seconds=float(model_config.get("timeout_seconds", 60)),
            max_tokens=int(model_config.get("max_tokens", 500)),
            max_attempts=int(model_config.get("max_attempts", 3)),
        )

    async def diagnose(
        self,
        *,
        alert: dict[str, Any],
        tool_observations: dict[str, Any] | None = None,
        evidence: list[dict[str, Any]] | None = None,
        allowed_candidates: list[str] | None = None,
    ) -> DeepSeekResult:
        allowed = allowed_candidates or list(ROOT_CAUSE_GUIDE)
        guide = {name: ROOT_CAUSE_GUIDE[name] for name in allowed}
        payload = {
            "alert": alert,
            "tool_observations": tool_observations,
            "deterministic_evidence": evidence,
            "allowed_root_causes": guide,
        }
        messages = [
            {
                "role": "system",
                "content": (
                    "You are an RCA classifier. Use only the supplied facts. Return JSON with exactly "
                    "candidate_types (1-3 ordered allowed_root_causes keys), evidence_types (observed stable "
                    "evidence labels when available), and rationale. Use no_fault when evidence is insufficient."
                ),
            },
            {"role": "user", "content": "Diagnose this incident and return JSON:\n" + json.dumps(payload)},
        ]
        body = {
            "model": self.model,
            "messages": messages,
            "response_format": {"type": "json_object"},
            "thinking": {"type": "disabled"},
            "temperature": 0,
            "max_tokens": self.max_tokens,
        }
        response_data = await self._post(body)
        choice = response_data["choices"][0]
        content = choice["message"].get("content")
        raw_decision = json.loads(content)
        # Ranking is deterministic when the RCA engine supplies one allowed
        # candidate. Some models wrap that single value in an object despite
        # JSON instructions; normalize only this no-choice case.
        if len(allowed) == 1:
            raw_decision["candidate_types"] = allowed
        decision = DeepSeekDecision.model_validate(raw_decision)
        invalid = [candidate.value for candidate in decision.candidate_types if candidate.value not in allowed]
        if invalid:
            raise ValueError(f"DeepSeek returned candidates outside deterministic constraint: {invalid}")
        deduplicated = list(dict.fromkeys(decision.candidate_types))
        decision = decision.model_copy(update={"candidate_types": deduplicated})
        return DeepSeekResult(
            decision=decision,
            usage=TokenUsage.model_validate(response_data["usage"]),
            model=response_data.get("model", self.model),
            finish_reason=choice["finish_reason"],
        )

    async def _post(self, body: dict[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self._api_key.get_secret_value()}",
            "Content-Type": "application/json",
        }
        async with httpx.AsyncClient(
            base_url=self.base_url,
            headers=headers,
            timeout=self.timeout_seconds,
            transport=self.transport,
        ) as client:
            for attempt in range(1, self.max_attempts + 1):
                try:
                    response = await client.post("/chat/completions", json=body)
                    if response.status_code < 400:
                        response_data = response.json()
                        content = response_data.get("choices", [{}])[0].get("message", {}).get("content")
                        if content:
                            return response_data
                    elif response.status_code not in {429, 500, 502, 503, 504}:
                        raise RuntimeError(f"DeepSeek API failed with HTTP {response.status_code}")
                except httpx.TransportError as exc:
                    if attempt == self.max_attempts:
                        raise RuntimeError(f"DeepSeek transport failed after {attempt} attempts") from exc
                if attempt == self.max_attempts:
                    raise RuntimeError("DeepSeek API returned empty content after retries")
                await asyncio.sleep(0.5 * attempt)
        raise RuntimeError("DeepSeek API retry loop exhausted")  # pragma: no cover
