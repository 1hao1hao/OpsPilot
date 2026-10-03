from opspilot.evaluation.dataset import load_dataset
from opspilot.evaluation.systems import OpsPilotHybridSystem


async def test_transient_metric_spike_does_not_force_a_fault_root_cause():
    case = next(
        item
        for item in load_dataset("benchmarks/datasets/rca/v1").cases("dev")
        if item.case_id == "normal-dev-02"
    )
    prediction = await OpsPilotHybridSystem().predict(case.alert, case.case_id)
    assert prediction["candidate_types"][0] == "no_fault"


async def test_domain_only_snapshot_does_not_leak_into_fallback():
    """Without L1 domain clues the fallback cannot read private backend snapshots."""
    case = next(
        item
        for item in load_dataset("benchmarks/datasets/rca/v1").cases("dev")
        if item.case_id == "db-dev-02"
    )
    prediction = await OpsPilotHybridSystem().predict(case.alert, case.case_id)
    assert prediction["candidate_types"][0] == "no_fault"
    assert not prediction["investigation"]["invoked_experts"]
