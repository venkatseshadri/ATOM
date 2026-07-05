# ATOM — Gate Tracker

Each phase ends with a Board gate. **Phase N+1 must not start until GATE N is signed.**
Claude builds and reports; the Board signs. Record sign-offs here.

| Gate | Phase | Criteria (see phases/phase-N/testcases.md) | Status | Signed by / date |
|------|-------|--------------------------------------------|--------|------------------|
| GATE 0 | 0 Skeleton | contracts frozen, 16 stubs, one full pass, 16/16 traces, tests green | ✅ **signed** | Board, 2026-06-30 |
| GATE 1 | 1 Regime + Signal | real Penguin snapshot, 7-family regime, FSM entry, real-premium paper order; freshness gate; 27 tests | ✅ **signed** | Board (Venkat), 2026-07-05 |
| GATE 2 | 2 Strike + Structure | expiry/strike/symbol resolution, concrete legs (paper) | ⬜ | — |
| GATE 3 | 3 Risk + Execution | risk invariants (no breach), stops trail, paper fills, EOD square-off | ⬜ | — |
| GATE 4 | 4 Ledger + Monitor | P&L accuracy, audit reconstruction, frozen config, restart recovery | ⬜ | — |
| GATE 5 | 5 Research Loop | post-mortem → ParameterSet → backtest → approval; AI out of loop | ⬜ | — |
| GATE 6 | 6 Validation | positive drawdown-adjusted expectancy; PORCUPINE green; promotion bar | ⬜ **GO/NO-GO** | — |
| GATE 7 | 7 Live | shadow matches paper; small live monitored; rollback proven | ⬜ | — |

## How to sign
Review the phase's `testcases.md` DoD + the code, then record name + date in the row above
(or tell Claude to). GATE 6 is the real-money GO/NO-GO.
