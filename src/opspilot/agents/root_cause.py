"""Evidence-driven root-cause ranking with a replaceable summary model."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Awaitable, Callable

from opspilot.config import RuntimeSettings
from opspilot.llm import DeepSeekRCAClient, DeepSeekSecrets
from opspilot.models import AlertEvent, Evidence, RootCauseCandidate, RootCauseType

SUMMARIES = {
    RootCauseType.DB_REPLICATION_LAG: "Database replication lag is delaying read requests",
    RootCauseType.DB_SLOW_QUERY: "Slow database queries are increasing request latency",
    RootCauseType.DB_CONNECTION_EXHAUSTED: "The database connection pool is exhausted",
    RootCauseType.REDIS_MEMORY_PRESSURE: "Redis memory pressure is degrading cache behavior",
    RootCauseType.REDIS_LOW_HIT_RATE: "A low Redis hit rate is increasing backend load",
    RootCauseType.KAFKA_CONSUMER_LAG: "Kafka consumer lag is delaying message processing",
    RootCauseType.RPC_TIMEOUT: "A downstream RPC dependency is timing out",
    RootCauseType.RPC_ERROR_RATE: "A downstream RPC dependency has an elevated error rate",
    RootCauseType.BAD_DEPLOYMENT: "A recent deployment correlates with the incident",
    RootCauseType.RESOURCE_SATURATION: "Service compute resources are saturated",
    RootCauseType.OOM_RESTART: "The service was restarted after an out-of-memory failure",
    RootCauseType.NO_FAULT: "No material fault was found in the collected evidence",
}


class FakeSummarizer:
    """Deterministic test double; no network or paid model access."""

    def __call__(self, candidate: RootCauseCandidate, evidence: list[Evidence]) -> str:
        return f"{candidate.summary}; supported by {len(candidate.evidence_ids)} evidence item(s)."


class RootCauseAgent:
    def __init__(
        self,
        summarizer: Callable[[RootCauseCandidate, list[Evidence]], str] | None = None,
        async_summarizer: Callable[[AlertEvent, RootCauseCandidate, list[Evidence]], Awaitable[str]] | None = None,
    ) -> None:
        self.summarizer = summarizer or FakeSummarizer()
        self.async_summarizer = async_summarizer

    def diagnose(self, alert: AlertEvent, evidence: list[Evidence]) -> tuple[list[RootCauseCandidate], str]:
        scores: dict[RootCauseType, float] = defaultdict(float)
        ids: dict[RootCauseType, list[str]] = defaultdict(list)
        for item in evidence:
            for cause in item.supports:
                scores[cause] += item.confidence
                ids[cause].append(item.evidence_id)
            for cause in item.contradicts:
                scores[cause] -= item.confidence

        if not scores:
            candidates = [
                RootCauseCandidate(
                    rank=1,
                    root_cause_type=RootCauseType.NO_FAULT,
                    summary=SUMMARIES[RootCauseType.NO_FAULT],
                    confidence=0.8,
                )
            ]
        else:
            ordered = sorted(scores, key=lambda cause: (-scores[cause], cause.value))[:3]
            candidates = [
                RootCauseCandidate(
                    rank=rank,
                    root_cause_type=cause,
                    summary=SUMMARIES[cause],
                    confidence=min(0.5 + scores[cause] / 3, 0.98),
                    evidence_ids=sorted(set(ids[cause])),
                )
                for rank, cause in enumerate(ordered, start=1)
            ]
        try:
            rationale = self.summarizer(candidates[0], evidence)
        except Exception:  # noqa: BLE001 - external summary models are optional
            rationale = (
                f"Deterministic fallback selected {candidates[0].root_cause_type.value} "
                f"from {len(candidates[0].evidence_ids)} evidence item(s)."
            )
        return candidates, rationale

    async def diagnose_with_optional_llm(
        self,
        alert: AlertEvent,
        evidence: list[Evidence],
    ) -> tuple[list[RootCauseCandidate], str, bool]:
        candidates, rationale = self.diagnose(alert, evidence)
        if self.async_summarizer is None:
            return candidates, rationale, False
        try:
            rationale = await self.async_summarizer(alert, candidates[0], evidence)
            return candidates, rationale, True
        except Exception:  # noqa: BLE001 - the deterministic decision must survive LLM failure
            return candidates, f"Deterministic fallback: {rationale}", False


class DeepSeekEvidenceSummarizer:
    """Explain one deterministic candidate; it cannot alter candidate ranking."""

    def __init__(self, client: DeepSeekRCAClient) -> None:
        self.client = client

    async def __call__(
        self,
        alert: AlertEvent,
        candidate: RootCauseCandidate,
        evidence: list[Evidence],
    ) -> str:
        result = await self.client.diagnose(
            alert=alert.model_dump(mode="json", exclude={"signals"}),
            evidence=[item.model_dump(mode="json") for item in evidence],
            allowed_candidates=[candidate.root_cause_type.value],
        )
        return result.decision.rationale


def build_runtime_root_cause_agent(settings: RuntimeSettings | None = None) -> RootCauseAgent:
    settings = settings or RuntimeSettings()
    if not settings.llm_enabled:
        return RootCauseAgent()
    secrets = DeepSeekSecrets()
    client = DeepSeekRCAClient(
        api_key=secrets.deepseek_api_key,
        model=settings.llm_model,
        base_url=settings.llm_base_url,
        timeout_seconds=settings.llm_timeout_seconds,
    )
    return RootCauseAgent(async_summarizer=DeepSeekEvidenceSummarizer(client))
