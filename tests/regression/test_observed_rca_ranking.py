"""Preserve deterministic diagnosis quality with the same complete observations."""

import pytest

from opspilot.evaluation.dataset import load_dataset
from opspilot.evaluation.systems import FixedPlannerSystem


@pytest.mark.asyncio
@pytest.mark.parametrize("split", ["dev", "test"])
async def test_all_observed_cases_keep_correct_top_k(split):
    system = FixedPlannerSystem()
    for case in load_dataset("benchmarks/datasets/rca/v1").cases(split):
        prediction = await system.predict(case.alert, case.case_id)
        assert prediction["candidate_types"][0] == case.expected_root_cause_type.value, case.case_id
