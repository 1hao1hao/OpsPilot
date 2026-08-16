import json

import httpx
import pytest

from opspilot.evaluation.deepseek import DeepSeekRCAClient


def _response(content):
    return {
        "choices": [{"finish_reason": "stop", "message": {"content": content}}],
        "model": "deepseek-v4-flash",
        "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15},
    }


@pytest.mark.asyncio
async def test_empty_response_retries_and_single_allowed_candidate_is_normalized():
    attempts = 0

    def handler(_request):
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            return httpx.Response(200, json=_response(""))
        malformed = json.dumps(
            {
                "candidate_types": [{"allowed_root_causes": ["no_fault"]}],
                "evidence_types": [],
                "rationale": "No material fault evidence.",
            }
        )
        return httpx.Response(200, json=_response(malformed))

    client = DeepSeekRCAClient(
        api_key="test",
        model="deepseek-v4-flash",
        max_attempts=2,
        transport=httpx.MockTransport(handler),
    )
    result = await client.diagnose(alert={"description": "normal"}, allowed_candidates=["no_fault"])

    assert attempts == 2
    assert [item.value for item in result.decision.candidate_types] == ["no_fault"]
