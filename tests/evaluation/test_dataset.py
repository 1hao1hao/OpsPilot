from collections import Counter

import pytest

from opspilot.evaluation.dataset import load_dataset


def test_v1_dataset_has_37_unique_cases_and_frozen_split():
    dataset = load_dataset("benchmarks/datasets/rca/v1")
    dev = dataset.cases("dev")
    test = dataset.cases("test")
    assert len(dev) == 25
    assert len(test) == 12
    assert {case.case_id for case in dev}.isdisjoint({case.case_id for case in test})
    assert Counter(case.category for case in dev) == {
        "db": 5,
        "redis": 4,
        "kafka": 4,
        "rpc": 4,
        "deploy_resource": 4,
        "normal_noise": 4,
    }
    assert sum(case.category == "normal_noise" for case in dev + test) == 6
    assert dataset.manifest.frozen_test is True


def test_unknown_split_fails_explicitly():
    with pytest.raises(ValueError, match="unknown split"):
        load_dataset("benchmarks/datasets/rca/v1").cases("train")
