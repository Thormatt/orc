from orc.eval.policy import TieredPolicy, load_policy, save_policy
from orc.storage import workspace as ws_module


def test_load_policy_is_none_before_calibration(orc_home) -> None:
    ws_module.create("demo")
    assert load_policy("demo") is None


def test_save_then_load_policy_roundtrip(orc_home) -> None:
    ws_module.create("demo")
    save_policy(
        "demo",
        tier1_model="claude-haiku-4-5",
        tier2_model="claude-sonnet-4-6",
        top_judge_model="gpt-4o",
        escalation_threshold=0.92,
        target=0.95,
        calibrated_against_eval_id="01EVAL",
        n_gold=40,
    )
    p = load_policy("demo")
    assert isinstance(p, TieredPolicy)
    assert p.escalation_threshold == 0.92
    assert p.top_judge_model == "gpt-4o"
    assert p.n_gold == 40


def test_save_policy_replaces_prior(orc_home) -> None:
    ws_module.create("demo")
    save_policy("demo", tier1_model="h", tier2_model="s", top_judge_model=None,
                escalation_threshold=0.8, target=0.9, calibrated_against_eval_id=None, n_gold=1)
    save_policy("demo", tier1_model="h", tier2_model="s", top_judge_model=None,
                escalation_threshold=0.95, target=0.99, calibrated_against_eval_id=None, n_gold=2)
    p = load_policy("demo")
    assert p.escalation_threshold == 0.95
    assert p.n_gold == 2
