# ES Strategy — Edit Log

All confirmed code changes to the Early Scalp (ES) strategy since inception.
Changes are sourced from session transcripts and conversation history.
Entries marked `[time unknown]` were made in prior sessions where exact time was not recorded.

---

## 2026-07-27

### [ES-001] Vol ratio added as 6th hard gate in `confirmed` check
- **File:** `backend/strategies/es/entry.py`
- **Time:** [time unknown — prior session]
- **Type:** Logic change — new entry gate
- **Trigger:** TIINDIA trade on Jul 27 entered with `vol_ratio = 0.00×`. Low volume meant no real momentum behind the gap. Trade resulted in a loss. Root cause: `vol_ratio` was only used as a score multiplier, not as a gate — any stock regardless of volume could be confirmed.

**Before:**
```python
confirmed = (
    abs(op_move) >= move_min and
    abs(gap)     >= gap_min  and
    consec_1m >= 2           and
    consec_3m >= 1           and
    not oi_against
)
```

**After:**
```python
confirmed = (
    abs(op_move) >= move_min and
    abs(gap)     >= gap_min  and
    consec_1m >= 2           and
    consec_3m >= 1           and
    not oi_against           and
    vr >= vr_min             # ← NEW: hard gate at 1.3×
)
```

**Also added to skip_reason block:**
```python
if vr < vr_min: reasons.append(f"vol_ratio={vr:.2f}<{vr_min}")
```

- **Threshold used:** `vr_min = 1.3` (from `backend/strategies/es/params.py`)
- **Impact:** Any stock with `vol_ratio < 1.3×` blocked from entry regardless of all other gates passing.

---

## 2026-07-31

### [ES-002] Vol ratio hard gate reverted — back to ranking-only
- **File:** `backend/strategies/es/entry.py`
- **Time:** ~16:30 IST
- **Type:** Logic revert
- **Trigger:** User flagged that the Jul 27 gate was added without confirmation and likely caused missed trades on Jul 29–31. Nifty volume feed shows near-zero `vol_ratio` for most stocks as baseline — the 1.3× threshold was effectively blocking almost all candidates.

**Removed from `confirmed`:** `vr >= vr_min`

**Removed from skip_reason:** `if vr < vr_min: reasons.append(...)`

**After revert (current state):**
```python
confirmed = (
    abs(op_move) >= move_min and
    abs(gap)     >= gap_min  and
    consec_1m >= 2           and
    consec_3m >= 1           and
    not oi_against
    # vol_ratio is NOT a gate — ranking only
)
```

`vol_ratio` continues to influence ranking via score formula:
```python
score = round(abs(gap) * abs(op_move) * max(vr, 0.5) * ...)
```
Higher vol_ratio → higher score → traded first when slots are limited. The `max(vr, 0.5)` floor means vol_ratio below 0.5× is treated as 0.5× in scoring.

- **`vr_min = 1.3` param** remains in `params.py` for reference — not used in gate, available if gate is re-introduced later.
- **Backend restarted** after this change to reload updated `entry.py` into memory (old PID 15688 → new PID 3068).

---

### [ES-003] Brokerage charges corrected in reporting
- **File:** `backend/reporting/costs.py`
- **Time:** ~17:00 IST
- **Type:** Data fix — charge calculation
- **Trigger:** User shared Zerodha contract note for BAJFINANCE Jul 31 trade. Actual charges were ₹301.10 vs system-calculated ₹289.09 — two rate mismatches identified.

| Charge | Old rate | New rate | Reason |
|--------|----------|----------|--------|
| STT (sell side) | 0.125% (`0.00125`) | **0.15%** (`0.0015`) | Budget 2025-26 revision |
| NSE exchange txn | 0.053% (`0.00053`) | **0.0355%** (`0.000355`) | NSE revised rate |

**Verification (BAJFINANCE Jul 31):**
- Old calculation: ₹289.09
- New calculation: ₹301.20
- Actual contract note: ₹301.10
- Difference: ₹0.10 (rounding only)

**Downstream impact:** `html_builder.py` and `strategy_dryrun.py` both import `calc_statutory()` from `costs.py` — charge figures in all HTML trade reports and backtest simulations now reflect corrected rates automatically.

**Updated net PnL figures (Jul 31 ES trades):**

| Trade | Gross | Charges | Net |
|-------|-------|---------|-----|
| ABCAPITAL | +₹3,720 | ₹271.20 | +₹3,448.80 |
| CGPOWER | +₹11,730 | ₹298.20 | +₹11,431.80 |
| BAJFINANCE | +₹11,850 | ₹301.20 | +₹11,548.80 |
| **Total** | **+₹27,300** | **₹870.60** | **+₹26,429.40** |

---

### [ES-004] Tick-level peak tracking + ratcheting trail SL
- **Files:** `backend/strategies/es/manage.py`, `backend/strategies/es/strategy.py`, `backend/core/market_state.py`
- **Time:** ~14:00 IST
- **Git commit:** `bab7b69`
- **Type:** Architecture change — trail SL accuracy
- **Trigger:** Analysis of 104-trade dataset showed 9 Cat B trades peaked between 5–8% (above the old 8% arm threshold at the time), held the move for some time, then reversed. Because the 10-second manage loop polled LTP only every 10s, intrabar spikes that briefly crossed the peak and then reversed were completely missed — the loop would never see the highest price. This meant the trail never armed or locked at the true peak. Estimated impact: ~9 trades, ~₹99,571 PnL left on the table.

**Architecture added:**

`market_state.py` — tick callback registry:
```python
def register_tick_callback(self, token: str, fn) -> None
def unregister_tick_callback(self, token: str) -> None
# update_tick() calls fn(token, ltp) outside the lock on every tick
```

`manage.py` — per-option tick callback:
```python
def _on_option_tick(self, token: str, ltp: float):
    # Runs in Zerodha WebSocket thread — lightweight
    tid = self._option_token_to_trade.get(token)
    if ltp > self._trail_peak.get(tid, 0.0):
        self._trail_peak[tid] = ltp
```

`strategy.py` — new in-memory dicts:
```python
self._trail_peak:            Dict[int, float] = {}  # tid → highest seen at tick resolution
self._option_token_to_trade: Dict[str, int]  = {}   # option_token → tid
self._trade_to_option_token: Dict[int, str]  = {}   # tid → option_token
```

**Trail ratchet:** The 10-second `_manage()` loop reads `_trail_peak[tid]` (updated at tick speed) instead of live premium for all arm/lock decisions. Lock ratchets continuously — every new peak raises the floor, never lowers it:
```python
tick_peak = max(self._trail_peak.get(tid, entry), premium)
peak_pct  = (tick_peak - entry) / entry * 100
new_lock  = peak_pct - gap_pct
if new_lock > self._trail_locked[tid]:
    self._trail_locked[tid] = new_lock
```

- **Callback registered:** on first sight of trade in `_manage()` loop
- **Callback unregistered:** on exit (force_exit, trail, target, square-off, daily reset)

---

### [ES-005] Trail activation threshold lowered: 8% → 6%
- **File:** `backend/strategies/es/params.py`
- **Time:** ~14:30 IST
- **Git commit:** `f994952`
- **Type:** Parameter change
- **Trigger:** Dataset analysis showed 9 trades peaked between 5–8% — these would have been captured at 6% arm but not at 8%. With 6% arm and 5% gap, trail locks at minimum +1% (locks at +1% when peak exactly hits +6%). Eliminates full losses on trades that briefly moved in favour before reversing.

```python
# Before
"trail_activate_pct": 8.0,
# After
"trail_activate_pct": 6.0,
```

No `es_params.json` override file exists — the default in `params.py` is the live value.

---

## 2026-07-31 (continued)

### [ES-006] Tick-level SL exit + live trades WebSocket for dashboard
- **Files:** `backend/strategies/es/manage.py`, `backend/strategies/es/strategy.py`, `backend/core/market_state.py`, `backend/routers/market_router.py`, `frontend/src/store/marketStore.ts`, `frontend/src/components/dashboard/OpenTradesPanel.tsx`
- **Time:** ~15:00 IST
- **Git commit:** `35416fa`
- **Type:** Architecture change — SL execution speed + dashboard real-time

**Problem:** Even after ES-004, the actual SL *exit* still only fired every 10 seconds (the `_manage()` loop). A tick that breached SL would not trigger an exit until the next loop cycle — up to 10 seconds of slippage.

**SL exit at tick level (`manage.py`):**

`_on_option_tick` now checks SL breach on every tick and spawns an immediate exit thread:
```python
if pnl_pct <= locked and tid not in self._pending_exit:
    self._pending_exit.add(tid)
    reason = f"ES Hard SL / Trail SL [tick-level]"
    threading.Thread(target=self._tick_sl_exit, args=(tid, reason), daemon=True).start()
```

`_tick_sl_exit` calls `risk_engine.force_exit_trade()` in a daemon thread — exit fires within milliseconds of the breach tick.

Race condition fixed: first-sight init now sets `_trail_armed`, `_trail_locked`, `_entry_cache` **before** calling `register_tick_callback` — no window where a tick fires before state is ready.

**Live dashboard WebSocket (`market_router.py` + `market_state.py`):**

New `/api/market/ws/trades` endpoint — push-based, fires on every option tick (throttled to 10 Hz per trade). Falls back to a full snapshot heartbeat every 2 seconds when no ticks flow.

Bridge between sync tick thread and async FastAPI WebSocket:
```python
# market_state.push_trade_update() — called from tick callback
loop.call_soon_threadsafe(q.put_nowait, payload)
```

**Frontend (`OpenTradesPanel.tsx`):**

Replaced 2-second HTTP poll with WebSocket to `/ws/trades`:
- `snapshot` message → replaces full trades list
- `trade_tick` message → surgical single-row update via `updateTradeTick()` (only changed row re-renders)

New dicts added to `strategy.py`: `_entry_cache`, `_symbol_cache`, `_pending_exit`, `_last_push` — all cleared on daily reset and exit cleanup.

---

## Current State of ES Strategy (as of 2026-07-31)

**File:** `backend/strategies/es/entry.py`

**5 hard gates (all must pass):**
1. `abs(op_move) >= 1.0%` — opening move from 9:15 open
2. `abs(gap) >= 0.5%` — overnight gap from prev close
3. `consec_1m >= 2` — at least 2 consecutive 1-min candles in direction
4. `consec_3m >= 1` — at least 1 consecutive 3-min candle in direction
5. `not oi_against` — OI signal must not oppose direction

**1 ranking factor (soft):**
- `vol_ratio` — affects score via `max(vr, 0.5)` multiplier; higher vol = higher rank

**Active params (`backend/strategies/es/params.py`):**

| Param | Value | Last changed |
|-------|-------|-------------|
| `gap_min_pct` | 0.5% | — |
| `move_min_pct` | 1.0% | — |
| `vol_ratio_min` | 1.3 (params only — NOT used as gate) | ES-001/ES-002 |
| `max_positions` | 5 | — |
| `hard_sl_pct` | 5.0% | — |
| `target_pct` | 12.0% | — |
| `trail_activate_pct` | **6.0%** | ES-005 (was 8.0%) |
| `trail_gap_pct` | 5.0% | — |

**SL execution:**
- Tick-level: fires within milliseconds of breach (ES-006)
- Loop fallback: `_manage()` every 10 seconds for target exits and restart recovery

**Dashboard data:**
- Open positions panel: WebSocket push on every option tick (≤100ms latency), 2s heartbeat fallback

---

---

## 2026-08-03

### [ES-007] Real-time 1-min candle builder — replaced 60s Kite polling
- **Files:** `backend/core/market_state.py`, `backend/strategies/es/filters.py`, `backend/strategies/es/entry.py`, `backend/strategies/es/strategy.py`
- **Time:** ~11:00 IST
- **Git commit:** `523ee39`
- **Type:** Architecture change — candle data source

**Root cause identified:** ITC trade (03 Aug, ID=181, 09:27:33) entered with `1m-consec=5` despite the chart showing a clear red slide from 9:21 to 9:26. The old `_maybe_refresh_candles` only refreshed candles for the **top 20 stocks by pct_change**. During ITC's pullback from +3.2% to +2.1%, other stocks with larger moves displaced ITC from the top-20 list, so ITC's candle cache was never updated after the initial surge. `_build_plan` and `_revalidate` both read stale pre-slide candles showing 5 consecutive green candles.

**Architecture change:**

`market_state.py` — new real-time 1-min candle builder:
```python
def _update_1m_candle(self, token, ltp, vol_cumulative, ts):
    # Called inside update_tick() on every WebSocket tick (under _lock)
    # Closes the forming bar on minute boundary using cumulative volume delta
```
```python
def get_1m_candles(self, token, include_forming=True) -> list:
    # Returns today's closed 1-min candles + the live forming candle
    # Always current to the last tick (~ms latency)
```
```python
def seed_1m_candles(self, token, candles):
    # One-time Kite backfill for stocks with no tick history (backend started late)
    # No-op if tick data already exists for this token
```

`filters.py` — `_maybe_refresh_candles` no longer polls Kite for candle data. Now runs every 60s to:
1. Print the top-20 scan list: `[ES] Top-20 (HH:MM:SS): [SYM1, SYM2, ...]`
2. Seed Kite data once for any stock with no tick history yet

`entry.py` — `_build_plan` and `_revalidate` now call `market.get_1m_candles(token)` instead of `self._candle_cache.get(symbol, [])`. Every consec and vol check uses live tick data.

`strategy.py` — `self._candle_cache` dict removed.

**Latency improvement:**

| Before | After |
|--------|-------|
| Up to 60s stale (Kite poll throttle) | ~1 tick latency (Zerodha WebSocket) |
| Only top-20 stocks refreshed | All subscribed stocks (~211) built in real time |
| Top-20 exclusion caused ITC-style stale entries | Every stock's candles always current |

**Volume calculation:** Uses `volume_traded` (cumulative day volume from Zerodha). Candle volume = `vol_cumulative_at_close − vol_cumulative_at_open`. The forming candle's volume is the partial volume accumulated so far in the current minute (used as-is for vol_ratio ranking — acceptable since vol_ratio is a soft ranking factor, not a gate).

---

---

### [ES-009] Candidate detection loop reduced: 10s → 1s
- **File:** `backend/strategies/es/params.py`
- **Time:** ~14:00 IST
- **Type:** Parameter change — loop cadence
- **Trigger:** ES-008 moved order *execution* to tick speed (<100ms), but `_build_plan` (which decides which stocks are confirmed and arms callbacks) still ran every 10 seconds. A stock crossing the consec threshold mid-loop had up to 10 seconds of blind spot before being detected and armed.

**Before:**
```python
LOOP_SEC = 10
```

**After:**
```python
LOOP_SEC = 1
```

**Impact:** `_build_plan` now runs every ~1 second. All operations inside are in-memory (stock moves, tick-built candles, consec checks). OI fetches are cached for 120s — no additional Kite API calls. Candidate detection lag: ≤1s. Combined with ES-008 tick entry, total worst-case lag from a stock meeting criteria to order placement: ~1 second.

---

---

### [ES-010] Fix race condition — duplicate entry for same symbol
- **File:** `backend/strategies/es/entry.py`
- **Time:** ~14:30 IST
- **Type:** Bug fix — race condition
- **Trigger:** NAM-INDIA traded twice on 2026-08-04 (ID=184 and ID=185, both CALL, 440ms apart). ID=184 was loop-level (`_enter_positions`), ID=185 was tick-level (`_tick_entry`). Both fired because `_enter_positions` never added the token to `_entry_pending`, so the tick callback had no signal that an order was already in flight. Both paths checked `_entered_symbols(DB)` before either order committed — race window caused two orders for the same symbol.

**Root cause:** `_entry_pending` was only populated by the tick path (`_on_stock_tick`). The loop path (`_enter_positions`) bypassed it entirely.

**Fix:** Added `_entry_pending.add(c.token)` at the start of each loop-level order attempt, with `_entry_pending.discard(c.token)` in a `finally` block. If the tick callback fires during the loop-level Kite API call, it sees the token in `_entry_pending` and returns immediately.

**In live mode:** this would have placed 2× lot size — double the intended capital at risk.

---

*Log maintained in session transcripts. Last updated: 2026-08-04.*
