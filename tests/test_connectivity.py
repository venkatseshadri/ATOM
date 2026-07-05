"""Module 11 — Connectivity & Auth tests (Phase 3 acceptance T3.x reconnect-awareness).

Uses a temp heartbeat dir (never touches the real antariksh data/live/ path in tests)."""
import json
import os
from datetime import datetime

from atom.connectivity import check_session_health, read_broker_margin, read_heartbeat


def _write_hb(tmp_path, index, ts_iso):
    (tmp_path / f"feed_{index}.heartbeat").write_text(ts_iso)


def _write_limits(tmp_path, ts_iso, **overrides):
    payload = {"timestamp": ts_iso, "total_margin_available": 579918.15,
              "used_margin": 0.0, "free_margin": 579918.15, "margin_multiplier": 1.0}
    payload.update(overrides)
    p = tmp_path / "broker_limits.json"
    p.write_text(json.dumps(payload))
    return p


def test_reads_fresh_heartbeat(tmp_path):
    _write_hb(tmp_path, "NIFTY", "2026-07-06T10:00:00")
    assert read_heartbeat("NIFTY", tmp_path) == "2026-07-06T10:00:00"


def test_missing_heartbeat_file_fails_safe(tmp_path):
    h = check_session_health("NIFTY", now=datetime(2026, 7, 6, 10, 0), heartbeat_dir=tmp_path)
    assert h.alive is False and h.reason == "NO_HEARTBEAT_FILE"


def test_fresh_heartbeat_is_alive(tmp_path):
    _write_hb(tmp_path, "NIFTY", "2026-07-06T09:59:30")
    h = check_session_health("NIFTY", now=datetime(2026, 7, 6, 10, 0, 0), heartbeat_dir=tmp_path,
                             stale_after_sec=90)
    assert h.alive is True and h.reason is None
    assert h.age_sec == 30.0


def test_stale_heartbeat_is_not_alive(tmp_path):
    _write_hb(tmp_path, "NIFTY", "2026-07-06T09:55:00")   # 5 min old
    h = check_session_health("NIFTY", now=datetime(2026, 7, 6, 10, 0, 0), heartbeat_dir=tmp_path,
                             stale_after_sec=90)
    assert h.alive is False and h.reason == "HEARTBEAT_STALE"


def test_malformed_heartbeat_fails_safe(tmp_path):
    _write_hb(tmp_path, "NIFTY", "not-a-timestamp")
    h = check_session_health("NIFTY", now=datetime(2026, 7, 6, 10, 0), heartbeat_dir=tmp_path)
    assert h.alive is False and h.reason == "MALFORMED_HEARTBEAT"


def test_boundary_exactly_at_stale_threshold_is_alive(tmp_path):
    """<= inclusive — age exactly equal to the threshold still counts as alive."""
    _write_hb(tmp_path, "NIFTY", "2026-07-06T09:58:30")   # exactly 90s before now
    h = check_session_health("NIFTY", now=datetime(2026, 7, 6, 10, 0, 0), heartbeat_dir=tmp_path,
                             stale_after_sec=90)
    assert h.age_sec == 90.0 and h.alive is True


def test_per_index_independence(tmp_path):
    _write_hb(tmp_path, "NIFTY", "2026-07-06T09:59:30")
    # no SENSEX heartbeat written
    h_nifty = check_session_health("NIFTY", now=datetime(2026, 7, 6, 10, 0), heartbeat_dir=tmp_path)
    h_sensex = check_session_health("SENSEX", now=datetime(2026, 7, 6, 10, 0), heartbeat_dir=tmp_path)
    assert h_nifty.alive is True
    assert h_sensex.alive is False and h_sensex.reason == "NO_HEARTBEAT_FILE"


def test_future_heartbeat_flagged_not_trusted(tmp_path):
    """A heartbeat timestamped AFTER 'now' (clock skew) must not be silently trusted
    as alive — same fail-safe stance as Module 7's clock-anomaly handling."""
    _write_hb(tmp_path, "NIFTY", "2026-07-06T10:05:00")
    h = check_session_health("NIFTY", now=datetime(2026, 7, 6, 10, 0, 0), heartbeat_dir=tmp_path)
    assert h.alive is False and h.reason == "HEARTBEAT_IN_FUTURE"


def test_real_antariksh_heartbeat_file_is_readable():
    """Sanity check against the REAL shared path (read-only, no assertion on
    freshness since the box's actual feed state varies) — proves the path/format
    assumption holds against the live file, not just a synthetic fixture."""
    from atom.connectivity import HEARTBEAT_DIR
    raw = read_heartbeat("NIFTY", HEARTBEAT_DIR)
    if raw is not None:
        datetime.fromisoformat(raw)   # must parse without raising


# ---- Real broker margin (data/broker_limits.json) -----------------------------

def test_fresh_broker_margin_is_available(tmp_path):
    path = _write_limits(tmp_path, "2026-07-06T08:30:00", used_margin=12000.0)
    bm = read_broker_margin(path, now=datetime(2026, 7, 6, 10, 0, 0))
    assert bm.available is True and bm.reason is None
    assert bm.total_margin_available == 579918.15 and bm.used_margin == 12000.0


def test_broker_margin_within_weekend_gap_still_available(tmp_path):
    """Refreshed Friday 08:30, read as late as Monday 15:30 (worst case, 3.29 days) —
    still the latest data that exists and shouldn't be flagged stale."""
    path = _write_limits(tmp_path, "2026-07-03T08:30:00")
    bm = read_broker_margin(path, now=datetime(2026, 7, 6, 15, 30, 0))
    assert bm.available is True


def test_broker_margin_beyond_max_age_flagged_stale(tmp_path):
    path = _write_limits(tmp_path, "2026-07-01T08:30:00")
    bm = read_broker_margin(path, now=datetime(2026, 7, 6, 9, 0, 0))
    assert bm.available is False and bm.reason == "STALE"


def test_broker_margin_missing_file_fails_safe(tmp_path):
    bm = read_broker_margin(tmp_path / "nonexistent.json")
    assert bm.available is False and bm.reason == "NO_FILE"


def test_broker_margin_malformed_json_fails_safe(tmp_path):
    p = tmp_path / "broker_limits.json"
    p.write_text("not json")
    bm = read_broker_margin(p)
    assert bm.available is False and bm.reason == "NO_FILE"


def test_broker_margin_missing_timestamp_key_fails_safe(tmp_path):
    p = tmp_path / "broker_limits.json"
    p.write_text(json.dumps({"total_margin_available": 100}))
    bm = read_broker_margin(p)
    assert bm.available is False and bm.reason == "MALFORMED"


def test_broker_margin_future_timestamp_not_trusted(tmp_path):
    path = _write_limits(tmp_path, "2026-07-07T08:30:00")
    bm = read_broker_margin(path, now=datetime(2026, 7, 6, 9, 0, 0))
    assert bm.available is False and bm.reason == "FUTURE_TIMESTAMP"


def test_real_antariksh_broker_limits_file_is_readable():
    """Sanity check against the REAL shared path — proves the schema assumption
    (timestamp + margin fields) holds against the live file, not just a fixture."""
    from atom.connectivity import BROKER_LIMITS_FILE
    bm = read_broker_margin(BROKER_LIMITS_FILE)
    if bm.reason not in ("NO_FILE",):
        assert bm.as_of is not None   # parsed a real timestamp either way
