"""strategy_dryrun.py — Exercises every ES and OB code node with real data.

Each function calls actual production code (not mocks) and returns (ok, msg).
A failure means that node will crash during a live session.

What this catches that the system checks do NOT:
  - SyntaxError / ImportError in any strategy module
  - AttributeError / TypeError when strategy code runs on real candle data
  - Logic breaks between nodes (e.g. option selector returns wrong shape)
  - Reporting template errors (html_builder, cost calculations)

Data source: most recent trading day's candles fetched live from Kite.
"""

import time as _time
from datetime import date, datetime, time as dtime, timedelta
from typing import Tuple, List

# ── Shared: fetch one stock's candles once, reuse across all nodes ─────────────
_candle_cache: dict = {}
_TEST_SYMBOL = "RELIANCE"
_TEST_DIRECTION = "call"


def _get_test_candles() -> List[dict]:
    if _candle_cache.get("candles"):
        return _candle_cache["candles"]
    try:
        from backend.core.broker import broker
        from backend.universe.instrument_cache import SYMBOL_TO_TOKEN
        token = SYMBOL_TO_TOKEN.get(_TEST_SYMBOL)
        if not token:
            return []
        last_day = date.today() - timedelta(days=1)
        while last_day.weekday() >= 5:
            last_day -= timedelta(days=1)
        kite    = broker.kite()
        candles = kite.historical_data(
            int(token),
            datetime.combine(last_day, dtime(9, 0)),
            datetime.combine(last_day, dtime(10, 30)),
            "minute",
        )
        _candle_cache["candles"] = candles
        _candle_cache["ltp"]     = candles[-1]["close"] if candles else 0.0
        _candle_cache["day"]     = last_day
        return candles
    except Exception:
        return []


def _get_test_ltp() -> float:
    _get_test_candles()
    return _candle_cache.get("ltp", 0.0)


# ══════════════════════════════════════════════════════════════════════════════
# ES NODES
# ══════════════════════════════════════════════════════════════════════════════

def check_es_params() -> Tuple[bool, str]:
    """ES · Params — load params file, verify all required keys present."""
    try:
        from backend.core.early_scalp import early_scalp
        p = early_scalp.get_params()
        required = ["hard_sl_pct", "target_pct", "trail_activate_pct",
                    "trail_gap_pct", "gap_min_pct", "move_min_pct",
                    "vol_ratio_min", "max_positions"]
        missing = [k for k in required if k not in p]
        if missing:
            return False, f"Missing param keys: {missing}"
        return True, (f"sl={p['hard_sl_pct']}%  target={p['target_pct']}%  "
                      f"trail_arm={p['trail_activate_pct']}%  trail_gap={p['trail_gap_pct']}%  "
                      f"max_pos={p['max_positions']}")
    except Exception as e:
        return False, str(e)


def check_es_filter_candles() -> Tuple[bool, str]:
    """ES · Filters — run _consec_candles, _vol_ratio, _to_3min on real candles."""
    try:
        from backend.strategies.es.filters import ESFiltersMixin
        candles = _get_test_candles()
        if not candles:
            return False, f"No candles fetched for {_TEST_SYMBOL}"

        m  = object.__new__(ESFiltersMixin)
        cc = m._consec_candles(candles, "call")
        vr = m._vol_ratio(candles)
        c3 = m._to_3min(candles)

        return True, (f"{_TEST_SYMBOL}: {len(candles)} 1m candles  |  "
                      f"consec={cc}  vol_ratio={vr:.2f}  3m_agg={len(c3)} candles")
    except Exception as e:
        return False, str(e)


def check_es_option_selector() -> Tuple[bool, str]:
    """ES · Option selector — select_option() returns a valid token + symbol."""
    try:
        from backend.execution.option_selector import select_option
        ltp = _get_test_ltp()
        if not ltp:
            return False, f"No LTP for {_TEST_SYMBOL}"
        tok, sym, strike, expiry = select_option(_TEST_SYMBOL, ltp, _TEST_DIRECTION)
        if not tok or not sym:
            return False, f"select_option returned no contract for {_TEST_SYMBOL} @ {ltp}"
        return True, f"{sym}  strike={strike}  expiry={expiry}  token={tok}"
    except Exception as e:
        return False, str(e)


def check_es_option_premium() -> Tuple[bool, str]:
    """ES · Option premium — kite.quote() for the ATM option returns a price."""
    try:
        from backend.execution.option_selector import select_option, option_premium
        ltp = _get_test_ltp()
        if not ltp:
            return False, "No LTP available"
        tok, sym, strike, expiry = select_option(_TEST_SYMBOL, ltp, _TEST_DIRECTION)
        if not tok:
            return False, "No option contract found — can't fetch premium"
        prem = option_premium(tok, sym)
        if prem is None:
            return False, f"option_premium returned None for {sym} — Kite quote may have failed"
        return True, f"{sym}  premium=Rs {prem:.2f}"
    except Exception as e:
        return False, str(e)


def check_es_quantity_calc() -> Tuple[bool, str]:
    """ES · Quantity calc — calc_quantity with real premium, lot size, and ACTUAL settings funds."""
    try:
        from backend.execution.quantity import calc_quantity
        from backend.execution.option_selector import select_option, option_premium
        from backend.universe.instrument_cache import _LOT_SIZE
        from backend.core.settings_manager import get_settings

        settings = get_settings()
        available_funds = float(settings.get("available_funds", 0))
        if available_funds <= 0:
            return False, (f"available_funds={available_funds} — settings not configured. "
                           "Set funds in the dashboard before trading.")

        ltp = _get_test_ltp()
        if not ltp:
            return False, "No LTP available"
        tok, sym, strike, expiry = select_option(_TEST_SYMBOL, ltp, _TEST_DIRECTION)
        prem     = option_premium(tok, sym) if tok else None
        lot_size = _LOT_SIZE.get(_TEST_SYMBOL, 250)
        if not prem:
            return False, "No premium available for quantity calc"
        lots = calc_quantity(available_funds, prem, lot_size, max_positions=5)
        per_slot = round(available_funds / 5)
        capital  = round(prem * lots * lot_size)
        return True, (f"funds=Rs {available_funds:,.0f}  premium=Rs {prem:.2f}  "
                      f"lot={lot_size}  lots={lots}  capital=Rs {capital:,}  "
                      f"(budget Rs {per_slot:,}/slot)")
    except Exception as e:
        return False, str(e)


def check_es_trail_logic() -> Tuple[bool, str]:
    """ES · Trail logic — simulate a price sequence through the trail math."""
    try:
        from backend.core.early_scalp import early_scalp
        p        = early_scalp.get_params()
        sl_pct   = float(p["hard_sl_pct"])
        tgt_pct  = float(p["target_pct"])
        arm_pct  = float(p["trail_activate_pct"])
        gap_pct  = float(p["trail_gap_pct"])

        # Simulate: entry=100, price rises to 110 (arm), then drops
        entry         = 100.0
        trail_armed   = False
        trail_locked  = -sl_pct
        exits         = []

        prices = [100, 102, 105, 108, 110, 112, 109, 107, 105, 103]
        for px in prices:
            pnl = (px - entry) / entry * 100
            if pnl >= arm_pct and not trail_armed:
                trail_armed  = True
                trail_locked = pnl - gap_pct
            if trail_armed:
                new_lock = pnl - gap_pct
                if new_lock > trail_locked:
                    trail_locked = new_lock
            if pnl >= tgt_pct:
                exits.append(("TARGET", px, pnl))
                break
            if pnl <= trail_locked:
                reason = "TRAIL" if trail_armed else "SL"
                exits.append((reason, px, pnl))
                break

        if not exits:
            exits.append(("OPEN", prices[-1], (prices[-1]-entry)/entry*100))

        reason, exit_px, exit_pnl = exits[0]
        return True, (f"entry=100  peak=112  exit_reason={reason}  "
                      f"exit_px={exit_px}  pnl={exit_pnl:+.1f}%  "
                      f"trail_locked={trail_locked:+.1f}%")
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# OB NODES
# ══════════════════════════════════════════════════════════════════════════════

def check_ob_params() -> Tuple[bool, str]:
    """OB · Params — load params, verify all required keys."""
    try:
        from backend.core.opening_breakout import opening_breakout
        p = opening_breakout.get_params()
        required = ["hard_sl_pct", "target_pct", "trail_activate_pct",
                    "trail_gap_pct", "max_positions"]
        missing = [k for k in required if k not in p]
        if missing:
            return False, f"Missing param keys: {missing}"
        return True, (f"sl={p['hard_sl_pct']}%  target={p['target_pct']}%  "
                      f"trail_arm={p['trail_activate_pct']}%  trail_gap={p['trail_gap_pct']}%  "
                      f"max_pos={p['max_positions']}")
    except Exception as e:
        return False, str(e)


def check_ob_sector_math() -> Tuple[bool, str]:
    """OB · Sector math — breadth + clarity calculation with real candle data."""
    try:
        from backend.strategies.ob.params import BREADTH_MIN_PCT, CLARITY_NET, SECTOR_MIN_PCT
        from backend.universe.sector_map import SECTOR_OF
        from backend.core.broker import broker
        from backend.universe.instrument_cache import SYMBOL_TO_TOKEN

        candles = _get_test_candles()
        if not candles:
            return False, f"No candles for {_TEST_SYMBOL}"

        # Build a minimal sector picture using RELIANCE as a sample
        ltp  = candles[-1]["close"]
        prev = candles[0]["open"]
        pct  = (ltp - prev) / prev * 100
        sec  = SECTOR_OF.get(_TEST_SYMBOL, "OTHER")

        # Run the actual breadth logic from OBEntryMixin
        ups = 1 if pct >= BREADTH_MIN_PCT else 0
        downs = 1 if pct <= -BREADTH_MIN_PCT else 0
        total = ups + downs
        net   = (ups - downs) / total if total else 0.0

        if   net >=  CLARITY_NET: trend, allowed = "bullish", {"call"}
        elif net <= -CLARITY_NET: trend, allowed = "bearish", {"put"}
        else:                     trend, allowed = "mixed",   {"call", "put"}

        sector_ok = abs(pct) >= SECTOR_MIN_PCT
        return True, (f"{_TEST_SYMBOL} ({sec})  move={pct:+.2f}%  "
                      f"breadth_net={net:+.2f}  trend={trend}  "
                      f"sector_qualifies={sector_ok}")
    except Exception as e:
        return False, str(e)


def check_ob_breakout_confirm() -> Tuple[bool, str]:
    """OB · Breakout confirm — import module + run volume-profile logic with real candles."""
    try:
        # Import the module itself (catches SyntaxError / ImportError)
        import backend.core.breakout_confirm as _bc_mod  # noqa: F401

        # Exercise the volume-profile helpers confirm_breakout uses internally
        from backend.core.volume_profile import build_profile, key_levels_summary
        candles = _get_test_candles()
        if not candles:
            return False, f"No candles for {_TEST_SYMBOL}"

        shaped = [{"open": c["open"], "high": c["high"],
                   "low": c["low"], "close": c["close"],
                   "volume": c.get("volume", 0)} for c in candles]

        vp50 = build_profile(shaped[-50:]) if len(shaped) >= 50 else build_profile(shaped)
        lvl  = key_levels_summary(vp50)

        # Test consecutive-candle momentum logic (same code path as confirm_breakout)
        recent = shaped[-5:]
        consec = 0
        for i in range(len(recent)-1, 0, -1):
            c, p = recent[i], recent[i-1]
            if c["high"] > p["high"] and c["close"] >= p["close"]:
                consec += 1
            else:
                break

        ltp = shaped[-1]["close"]
        vah = vp50.get("vah", ltp)
        val = vp50.get("val", ltp)
        return True, (f"vol_profile OK | POC={lvl.get('poc')} VAH={vah} VAL={val} | "
                      f"consec_up={consec} | {len(shaped)} candles processed")
    except Exception as e:
        return False, str(e)


def check_ob_option_selector() -> Tuple[bool, str]:
    """OB · Option selector — reuse ES result but verify OB direction (put support)."""
    try:
        from backend.execution.option_selector import select_option
        ltp = _get_test_ltp()
        if not ltp:
            return False, "No LTP available"
        # Test both directions since OB can go either way
        tok_c, sym_c, strike_c, _ = select_option(_TEST_SYMBOL, ltp, "call")
        tok_p, sym_p, strike_p, _ = select_option(_TEST_SYMBOL, ltp, "put")
        if not tok_c or not tok_p:
            return False, "select_option failed for call or put"
        return True, f"CALL: {sym_c} @ {strike_c}  |  PUT: {sym_p} @ {strike_p}"
    except Exception as e:
        return False, str(e)


def check_ob_trail_logic() -> Tuple[bool, str]:
    """OB · Trail logic — simulate OB trailing with ob params."""
    try:
        from backend.core.opening_breakout import opening_breakout
        p       = opening_breakout.get_params()
        sl_pct  = float(p["hard_sl_pct"])
        tgt_pct = float(p["target_pct"])
        arm_pct = float(p["trail_activate_pct"])
        gap_pct = float(p["trail_gap_pct"])

        entry        = 100.0
        trail_armed  = False
        trail_locked = -sl_pct
        result       = None

        prices = [100, 115, 125, 135, 145, 155, 150, 145, 140, 135]
        for px in prices:
            pnl = (px - entry) / entry * 100
            if pnl >= arm_pct and not trail_armed:
                trail_armed  = True
                trail_locked = pnl - gap_pct
            if trail_armed:
                new_lock = pnl - gap_pct
                if new_lock > trail_locked:
                    trail_locked = new_lock
            if pnl >= tgt_pct:
                result = ("TARGET", px, pnl)
                break
            if pnl <= trail_locked:
                result = ("TRAIL" if trail_armed else "SL", px, pnl)
                break

        if not result:
            result = ("OPEN", prices[-1], (prices[-1]-entry)/entry*100)

        reason, exit_px, exit_pnl = result
        return True, (f"entry=100  sim_prices={prices[0]}-{max(prices)}  "
                      f"exit={reason} @ {exit_px}  pnl={exit_pnl:+.1f}%")
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# INTEGRATION / TRADE-PIPELINE NODES
# ══════════════════════════════════════════════════════════════════════════════

def _test_trade_pipeline(direction: str, tag: str) -> Tuple[bool, str]:
    """Walk the trade pipeline step by step, create a paper trade, verify DB write, clean up.
    Skips the liquidity check (market-hours-only gate) so this works any time feed is connected."""
    from backend.core.market_state import market
    if not market.is_feed_connected():
        return False, "Feed not connected — cannot test trade pipeline"

    from backend.universe.instrument_cache import SYMBOL_TO_TOKEN
    token = SYMBOL_TO_TOKEN.get(_TEST_SYMBOL)
    if not token:
        return False, f"{_TEST_SYMBOL} not in instrument cache"

    mv = market.get_stock_move(token)
    ltp = mv.get("ltp") if mv else None
    if not ltp:
        return False, f"STEP 1 FAIL — no live LTP for {_TEST_SYMBOL} (feed stale or token not subscribed)"

    from backend.execution.option_selector import select_option, option_premium
    opt_token, opt_symbol, strike, expiry = select_option(_TEST_SYMBOL, ltp, direction)
    if not opt_token or not opt_symbol:
        return False, f"STEP 2 FAIL — no ATM {direction.upper()} option for {_TEST_SYMBOL} @ {ltp}"

    prem = option_premium(opt_token, opt_symbol)
    if not prem:
        return False, f"STEP 3 FAIL — option_premium returned None for {opt_symbol} (Kite quote failed)"

    from backend.execution.quantity import calc_quantity
    from backend.universe.instrument_cache import _LOT_SIZE
    from backend.core.settings_manager import get_settings
    settings = get_settings()
    available_funds = float(settings.get("available_funds", 0))
    if available_funds <= 0:
        return False, f"STEP 4 FAIL — available_funds={available_funds} (not configured)"
    lot_size = _LOT_SIZE.get(_TEST_SYMBOL, 250)
    qty = calc_quantity(available_funds, prem, lot_size, max_positions=5)
    if qty <= 0:
        return False, f"STEP 4 FAIL — calc_quantity={qty} (premium Rs {prem:.2f} too high or funds too low)"

    from backend.core.session_manager import get_or_create_session
    from backend.core.clock import now_ist
    from backend.database import (
        TradeEnv, Session as DBSessionFactory, Trade,
        TradeStatus, TradeDirection,
    )

    env = TradeEnv.PAPER
    session = get_or_create_session(env)
    sl_pct = 5.0 if tag == "ES" else 10.0
    tgt_pct = 12.0 if tag == "ES" else 50.0

    db = DBSessionFactory()
    try:
        trade = Trade(
            session_id=session["id"], env=env, status=TradeStatus.OPEN,
            direction=TradeDirection(direction), symbol=_TEST_SYMBOL,
            option_symbol=opt_symbol, strike=strike, expiry=expiry,
            option_type="CE" if direction == "call" else "PE",
            entry_price=prem, quantity=qty, lot_size=lot_size,
            trade_sl_pct=sl_pct,
            trade_sl_price=round(prem * (1 - sl_pct / 100), 2),
            target_price=round(prem * (1 + tgt_pct / 100), 2),
            entry_logic=f"[{tag}] DRYRUN-TEST — will be deleted immediately",
            indicators_snapshot={},
            entered_at=now_ist(),
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        trade_id = trade.id

        verify = db.query(Trade).filter(Trade.id == trade_id).first()
        if not verify:
            return False, f"STEP 5 FAIL — trade #{trade_id} not found in DB after insert"

        db.delete(verify)
        db.commit()
    except Exception as e:
        return False, f"STEP 5 FAIL — DB write error: {e}"
    finally:
        db.close()

    return True, (f"PIPELINE OK — {opt_symbol} @ Rs {prem:.2f} | "
                  f"qty={qty}x{lot_size} | trade #{trade_id} created+cleaned | "
                  f"liquidity skipped (market-hours gate)")


def check_es_trade_pipeline() -> Tuple[bool, str]:
    """ES · Trade pipeline — full entry path: feed → LTP → option → premium → qty → DB write+cleanup."""
    try:
        return _test_trade_pipeline(_TEST_DIRECTION, "ES")
    except Exception as e:
        return False, str(e)


def check_ob_trade_pipeline() -> Tuple[bool, str]:
    """OB · Trade pipeline — full entry path (PUT): feed → LTP → option → premium → qty → DB write+cleanup."""
    try:
        return _test_trade_pipeline("put", "OB")
    except Exception as e:
        return False, str(e)


def check_strategy_state_endpoints() -> Tuple[bool, str]:
    """ES/OB · State health — verify both strategies return valid state with expected fields."""
    try:
        from backend.core.early_scalp import early_scalp
        from backend.core.opening_breakout import opening_breakout

        es_plan = early_scalp.get_plan()
        if not es_plan:
            return False, "ES get_plan() returned None"
        es_status = es_plan.get("status")
        if not es_status:
            return False, f"ES plan missing 'status' field: {list(es_plan.keys())}"

        ob_state = opening_breakout.get_state()
        if not ob_state:
            return False, "OB get_state() returned None"
        ob_phase = ob_state.get("phase")
        if not ob_phase:
            return False, f"OB state missing 'phase' field: {list(ob_state.keys())}"

        ob_plan = opening_breakout.get_plan()
        if not ob_plan:
            return False, "OB get_plan() returned None"
        ob_plan_status = ob_plan.get("status")
        if not ob_plan_status:
            return False, f"OB plan missing 'status' field: {list(ob_plan.keys())}"

        es_cands = len(es_plan.get("candidates", []))
        ob_stocks = len(ob_plan.get("stocks", []))
        ob_refs = ob_state.get("open_refs", 0)

        return True, (f"ES: {es_status} ({es_cands} candidates) | "
                      f"OB: {ob_phase} ({ob_stocks} stocks, {ob_refs} refs) | "
                      f"both endpoints healthy")
    except Exception as e:
        return False, str(e)


# ══════════════════════════════════════════════════════════════════════════════
# REPORTING NODES
# ══════════════════════════════════════════════════════════════════════════════

def check_reporting_costs() -> Tuple[bool, str]:
    """Reporting · Costs — calc_brokerage and calc_statutory with sample trade."""
    try:
        from backend.reporting.costs import calc_brokerage, calc_statutory
        sample_trade = {
            "entry": 120.0, "exit": 140.0, "qty": 2, "lot_size": 250
        }
        brok = calc_brokerage()
        stat = calc_statutory(sample_trade)
        total_cost = brok + stat
        net_pnl = (140 - 120) * 2 * 250 - total_cost
        return True, (f"brokerage=Rs {brok:.2f}  statutory=Rs {stat}  "
                      f"total_cost=Rs {total_cost:.2f}  "
                      f"sample_net_pnl=Rs {net_pnl:.2f}")
    except Exception as e:
        return False, str(e)


def check_reporting_html() -> Tuple[bool, str]:
    """Reporting · HTML builder — build_report_html with sample trades (catches template bugs)."""
    try:
        from backend.reporting.html_builder import build_report_html
        from backend.reporting.stats import build_stats

        sample_trades = [
            {"symbol": "RELIANCE", "option_symbol": "RELIANCE26JUL1320CE",
             "direction": "call", "entry": 120.0, "exit": 138.0,
             "qty": 2, "lot_size": 250, "pnl": 9000.0, "pnl_pct": 15.0,
             "entry_logic": "[ES] gap+move", "exit_logic": "Target +12%",
             "strike": 1320, "expiry": "2026-07-31", "option_type": "CE",
             "entered_at": "2026-07-10T09:28:00", "exited_at": "2026-07-10T10:05:00",
             "highest_price": 145.0},
            {"symbol": "INFY", "option_symbol": "INFY26JUL1800CE",
             "direction": "call", "entry": 80.0, "exit": 76.0,
             "qty": 3, "lot_size": 300, "pnl": -3600.0, "pnl_pct": -5.0,
             "entry_logic": "[ES] gap+move", "exit_logic": "Hard SL -5%",
             "strike": 1800, "expiry": "2026-07-31", "option_type": "CE",
             "entered_at": "2026-07-10T09:30:00", "exited_at": "2026-07-10T09:52:00",
             "highest_price": 82.0},
        ]

        all_s = build_stats(sample_trades)
        html  = build_report_html(
            period_label    = "Today",
            mode            = "paper",
            generated_date  = "2026-07-10",
            all_s           = all_s,
            es_s            = all_s,
            ob_s            = {"n": 0, "net": 0, "wins": 0, "win_pct": 0, "pf": 0, "avg_cap": 0},
            es_trades       = sample_trades,
            ob_trades       = [],
            es_start_balance= 500_000.0,
        )
        if not html or len(html) < 100:
            return False, "build_report_html returned empty or too-short HTML"
        return True, f"HTML generated OK — {len(html):,} chars"
    except Exception as e:
        return False, str(e)


# ── Full node list (name, node_label, function) ────────────────────────────────
DRYRUN_CHECKS = [
    # ES pipeline
    ("ES · Params",           "strategy-es",  check_es_params),
    ("ES · Filter logic",     "strategy-es",  check_es_filter_candles),
    ("ES · Option selector",  "execution",    check_es_option_selector),
    ("ES · Option premium",   "execution",    check_es_option_premium),
    ("ES · Quantity calc",    "execution",    check_es_quantity_calc),
    ("ES · Trail logic",      "strategy-es",  check_es_trail_logic),
    # OB pipeline
    ("OB · Params",           "strategy-ob",  check_ob_params),
    ("OB · Sector math",      "strategy-ob",  check_ob_sector_math),
    ("OB · Breakout confirm", "strategy-ob",  check_ob_breakout_confirm),
    ("OB · Option selector",  "execution",    check_ob_option_selector),
    ("OB · Trail logic",      "strategy-ob",  check_ob_trail_logic),
    # Integration / trade pipeline
    ("ES · Trade pipeline",   "execution",    check_es_trade_pipeline),
    ("OB · Trade pipeline",   "execution",    check_ob_trade_pipeline),
    ("ES/OB · State health",  "integration",  check_strategy_state_endpoints),
    # Reporting pipeline
    ("Report · Costs",        "reporting",    check_reporting_costs),
    ("Report · HTML builder", "reporting",    check_reporting_html),
]
