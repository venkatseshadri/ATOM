"""Module 16 — Config & ParameterSet tests (Phase 4 acceptance T4.4 frozen config)."""
import os

import pytest

from atom.config_freeze import ConfigFreezeStore, SCHEMA_BOUNDS, validate


@pytest.fixture
def store(tmp_path):
    return ConfigFreezeStore(str(tmp_path / "params.sqlite"))


CLEAN = {"risk.deploy.inr": 200000, "risk.sl.pct": 35, "strategy.lot.size": 65}


# ---- 16.2 Validation -----------------------------------------------------------

def test_clean_params_pass():
    assert validate(CLEAN) == []


def test_out_of_bounds_rejected():
    v = validate({"risk.sl.pct": 500})
    assert any("OUT_OF_BOUNDS" in r for r in v)


def test_boundary_inclusive_accepted():
    lo, hi = SCHEMA_BOUNDS["risk.sl.pct"]
    assert validate({"risk.sl.pct": lo}) == []
    assert validate({"risk.sl.pct": hi}) == []


def test_type_error_rejected():
    v = validate({"risk.sl.pct": "35"})
    assert any("TYPE_ERROR" in r for r in v)


def test_nan_rejected():
    v = validate({"risk.sl.pct": float("nan")})
    assert any("NAN_OR_INF" in r for r in v)


def test_inf_rejected():
    v = validate({"risk.sl.pct": float("inf")})
    assert any("NAN_OR_INF" in r for r in v)


def test_multiple_violations_all_reported():
    """16.9.1 — report ALL violations, not just the first."""
    v = validate({"risk.sl.pct": 500, "risk.tp.pct": -5})
    assert len(v) == 2


def test_unlisted_key_passes_through_unchecked():
    assert validate({"some.cosmetic.setting": "anything"}) == []


# ---- 16.8 Secrets defense -------------------------------------------------------

def test_secret_shaped_key_rejected():
    for key in ("api_token", "broker_secret", "password", "auth_key", "credential_id"):
        v = validate({key: "xyz"})
        assert any("SECRET_SHAPED_KEY" in r for r in v), f"{key} should be rejected"


def test_secret_never_persisted_to_history(store):
    ps, violations = store.freeze_for_session("2026-07-08", {"api_token": "sekrit123"},
                                              "2026-07-08T09:15:00")
    assert ps is None
    history = store.history()
    assert "sekrit123" not in str(history)   # 16.8.1: secret value never lands in history


# ---- 16.3 Freeze + idempotency (the real bug fix) -------------------------------

def test_freeze_is_idempotent_same_day(store):
    """The actual bug found: config must NOT change mid-day even if the underlying
    file/dict is edited between calls."""
    ps1, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    edited = {**CLEAN, "risk.deploy.inr": 999999}
    ps2, _ = store.freeze_for_session("2026-07-06", edited, "2026-07-06T10:30:00")
    assert ps1.version == ps2.version
    assert ps2.params["risk.deploy.inr"] == 200000   # the ORIGINAL value, edit ignored


def test_freeze_different_days_get_different_versions(store):
    ps1, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    ps2, _ = store.freeze_for_session("2026-07-07", CLEAN, "2026-07-07T09:15:00")
    assert ps1.version != ps2.version


def test_bad_candidate_never_freezes(store):
    ps, violations = store.freeze_for_session("2026-07-06", {"risk.sl.pct": 500},
                                              "2026-07-06T09:15:00")
    assert ps is None and violations


def test_frozen_params_are_structurally_immutable(store):
    """16.3.2 — a consumer mutating its returned copy must not affect another
    consumer's read (MappingProxyType blocks in-place mutation entirely)."""
    ps, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    with pytest.raises(TypeError):
        ps.params["risk.sl.pct"] = 999


def test_two_consumers_see_identical_values_and_version(store):
    ps_a, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    ps_b, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T14:30:00")
    assert ps_a.version == ps_b.version
    assert dict(ps_a.params) == dict(ps_b.params)


# ---- 16.4 Versioning + history ---------------------------------------------------

def test_versions_are_monotonic(store):
    ps1, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    ps2, _ = store.freeze_for_session("2026-07-07", CLEAN, "2026-07-07T09:15:00")
    v1 = int(ps1.version.split(":")[0][1:])
    v2 = int(ps2.version.split(":")[0][1:])
    assert v2 > v1


def test_rejected_sets_appear_in_history_with_reasons(store):
    store.freeze_for_session("2026-07-06", {"risk.sl.pct": 500}, "2026-07-06T09:15:00")
    hist = store.history("2026-07-06")
    assert len(hist) == 1
    assert hist[0]["approval_state"] == "REJECTED"
    assert "OUT_OF_BOUNDS" in hist[0]["rejected_reasons"]


def test_history_reconstructs_any_past_session(store):
    store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    hist = store.history("2026-07-06")
    assert hist[0]["frozen_at"] == "2026-07-06T09:15:00"


# ---- 16.4.3 / 16.6 Last-known-good + rollback -----------------------------------

def test_no_last_known_good_reports_none_explicitly(store):
    assert store.last_known_good() is None


def test_last_known_good_promotion_and_lookup(store):
    ps, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    version = int(ps.version.split(":")[0][1:])
    store.mark_last_known_good(version)
    lkg = store.last_known_good()
    assert lkg.version == ps.version


def test_rollback_without_last_known_good_escalates(store):
    ps, reason = store.rollback("2026-07-07", "2026-07-07T09:15:00")
    assert ps is None and reason == "NO_LAST_KNOWN_GOOD"


def test_rollback_uses_last_known_good_not_latest(store):
    """A newer set failed, but last-known-good still points at the prior healthy one."""
    ps_good, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    v_good = int(ps_good.version.split(":")[0][1:])
    store.mark_last_known_good(v_good)
    store.freeze_for_session("2026-07-07", {"risk.sl.pct": 500}, "2026-07-07T09:15:00")  # fails

    rolled, reason = store.rollback("2026-07-08", "2026-07-08T09:15:00")
    assert reason is None
    assert dict(rolled.params) == CLEAN


def test_rollback_is_tagged_and_reusable_via_normal_freeze_path(store):
    ps_good, _ = store.freeze_for_session("2026-07-06", CLEAN, "2026-07-06T09:15:00")
    store.mark_last_known_good(int(ps_good.version.split(":")[0][1:]))
    rolled, _ = store.rollback("2026-07-08", "2026-07-08T09:15:00")
    hist = store.history("2026-07-08")
    assert hist[0]["rolled_back"] == 1
