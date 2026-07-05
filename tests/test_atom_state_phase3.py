"""AtomState Phase 3 extensions: index persistence, TSL ratchet state, account
derivation for Module 5's risk gate."""
from atom import phase1
from atom.atom_state import AtomState


def _order(structure="bull_put_spread", index="NIFTY", max_loss=9630.0):
    return phase1.PaperOrder(structure,
                             (("BUY", 23750, "PE", 233.75), ("SELL", 23950, "PE", 305.35)),
                             5370.0, max_loss, 75, "28-JUL-2026", (), index)


def test_index_persisted_and_reloaded(tmp_path):
    state = AtomState(str(tmp_path / "s.sqlite"))
    state.checkpoint_and_record("SINGLE_SPREAD", "2026-07-08T09:44:00",
                                _order(index="SENSEX"), "2026-07-08T09:45:00",
                                {"regime": "TREND_UP", "confidence": 0.75})
    pos = state.last_open_position()
    assert pos["index"] == "SENSEX"


def test_legacy_row_without_index_defaults_nifty(tmp_path):
    """A row written before the index_name column existed (NULL) must default to
    NIFTY, not crash or silently misroute SENSEX logic."""
    state = AtomState(str(tmp_path / "s.sqlite"))
    c = state._c()
    c.execute("INSERT INTO paper_trades (ts,bar_ts,structure,net_credit,max_loss,lot,legs) "
             "VALUES(?,?,?,?,?,?,?)",
             ("2026-07-01T09:45:00", "2026-07-01T09:44:00", "bull_put_spread", 5370.0,
              9630.0, 75, "[]"))
    c.commit(); c.close()
    pos = state.last_open_position()
    assert pos["index"] == "NIFTY"
    assert pos["tsl"] is None and pos["tsl_armed"] is False


def test_update_stop_state_persists_ratchet(tmp_path):
    state = AtomState(str(tmp_path / "s.sqlite"))
    state.checkpoint_and_record("SINGLE_SPREAD", "2026-07-01T09:44:00", _order(),
                                "2026-07-01T09:45:00", {"regime": "TREND_UP", "confidence": 0.75})
    pos = state.last_open_position()
    assert pos["tsl"] is None and pos["tsl_armed"] is False

    state.update_stop_state(pos["ts"], tsl=1500.0, tsl_armed=True, high_water_pnl=3200.0)
    reloaded = state.last_open_position()
    assert reloaded["tsl"] == 1500.0
    assert reloaded["tsl_armed"] is True
    assert reloaded["high_water_pnl"] == 3200.0


def test_derive_account_clean_slate(tmp_path):
    state = AtomState(str(tmp_path / "s.sqlite"))
    acct = state.derive_account(capital=200000, today="2026-07-06")
    assert acct["realized_pnl_today"] == 0
    assert acct["open_count"] == 0
    assert acct["trades_today"] == 0
    assert acct["reentries_today_by_family"] == {}
    assert acct["current_equity"] == 200000
    assert acct["deployed"] == 0.0


def test_derive_account_reflects_open_position_and_reentry_count(tmp_path):
    state = AtomState(str(tmp_path / "s.sqlite"))
    state.checkpoint_and_record("SINGLE_SPREAD", "2026-07-06T09:44:00", _order(),
                                "2026-07-06T09:45:00", {"regime": "TREND_UP", "confidence": 0.75})
    acct = state.derive_account(capital=200000, today="2026-07-06")
    assert acct["open_count"] == 1
    assert acct["reserved_risk_open"] == 9630.0
    assert acct["reentries_today_by_family"] == {"bull_put_spread": 1}
    assert acct["trades_today"] == 1


def test_derive_account_reflects_realized_loss_today(tmp_path):
    state = AtomState(str(tmp_path / "s.sqlite"))
    state.checkpoint_and_record("SINGLE_SPREAD", "2026-07-06T09:44:00", _order(),
                                "2026-07-06T09:45:00", {"regime": "TREND_UP", "confidence": 0.75})
    pos = state.last_open_position()
    state.record_exit_and_checkpoint(pos["ts"], "2026-07-06T10:00:00", "SL", -3370.5,
                                     {"hedge_ltp": 10.0, "short_ltp": 20.0}, "2026-07-06T10:00:00")
    acct = state.derive_account(capital=200000, today="2026-07-06")
    assert acct["realized_pnl_today"] == -3370.5
    assert acct["current_equity"] == 200000 - 3370.5
    assert acct["open_count"] == 0   # position closed


def test_derive_account_includes_real_broker_margin_fields(tmp_path):
    """derive_account() folds in antariksh's real broker_limits.json (or a
    fail-safe 'unavailable' reading if the box has none) — either way, the keys
    risk.py's gate checks for must always be present."""
    state = AtomState(str(tmp_path / "s.sqlite"))
    acct = state.derive_account(capital=200000, today="2026-07-06")
    assert "broker_margin_available" in acct
    assert "broker_free_margin" in acct
    assert "broker_margin_reason" in acct
