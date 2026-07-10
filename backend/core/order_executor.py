"""
order_executor.py — Places and squares off option orders via Zerodha Kite.

Paper mode:  fills at the real option LTP (Kite quote), writes to DB.
Live mode:   kite.place_order() (NFO, MARKET, MIS intraday) then writes to DB.

Rules:
  - Feed disconnected -> reject (no blind trades).
  - MARKET orders only (option spreads are wide).
  - Option BUYING only (BUY to enter, SELL to exit). No shorting.
  - Real option premiums via Kite quote() — no more estimates.
"""

import os, sys
from datetime import date, datetime, timedelta
from typing import Optional, Tuple
from backend.core.clock import now_ist

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.database import (
    Session, Trade, TradeEnv, TradeDirection, TradeStatus
)
from backend.core.market_state     import market
from backend.core.settings_manager import get_settings
from backend.core.session_manager  import get_or_create_session, update_portfolio_balance
from backend.core.stock_universe   import load_instrument_cache, get_meta
from backend.core.broker           import broker


# ── Quantity ──────────────────────────────────────────────────────────────────
def calc_quantity(available_funds: float, premium: float, lot_size: int) -> int:
    budget    = available_funds * 0.5
    cost_1lot = premium * lot_size
    if cost_1lot <= 0:
        return 1
    return max(int(budget // cost_1lot), 1)


# ── Real option premium (Kite quote works with this key) ──────────────────────
def option_premium(opt_token: str, opt_symbol: str) -> Optional[float]:
    if not opt_symbol:
        return None
    # Prefer the live-subscribed tick
    p = market.get_ltp(opt_token) if opt_token else None
    if p:
        return p
    # Fallback: direct Kite quote
    try:
        key = f"NFO:{opt_symbol}"
        q   = broker.kite().quote([key])
        return float(q[key]["last_price"])
    except Exception:
        return None


def current_premium(trade) -> Optional[float]:
    """Current premium of an OPEN trade's option (NOT the underlying)."""
    from backend.core.stock_universe import get_option_token
    tok = get_option_token(trade.option_symbol)
    return option_premium(tok, trade.option_symbol)


# ── Liquidity guard ─────────────────────────────────────────────────────────
MIN_OPTION_VOLUME_LOTS = 3   # require >=3 lots traded in the last LIQUIDITY_LOOKBACK_MIN minutes
LIQUIDITY_LOOKBACK_MIN = 10
MAX_SPREAD_PCT         = 20.0  # max bid-ask spread as % of mid — wider = can't exit cleanly
MIN_OI                 = 300   # minimum open interest contracts — below this, one trade moves the market

def _has_liquidity(opt_token: str, opt_symbol: str, lot_size: int) -> bool:
    """Reject contracts where entry or exit would be dangerously illiquid.

    Three checks:
    1. Recent volume  — at least 3 lots traded in the last 10 min (rules out stale
       markets where volume=0 and one odd-lot print re-prices the LTP by 30%+,
       as happened with KAYNES26JUN3200CE on 2026-06-16).
    2. Bid-ask spread — max 20% of mid. A wide spread means the market-maker is
       pricing in low conviction; exiting at SL will cost 10%+ on the spread alone.
    3. Open Interest  — min 300 contracts. Below this the book is thin enough that
       our own lot can move the price against us on entry or block us on exit.
    """
    kite = broker.kite()

    # ── 1. Recent volume (last 10 min candles) ────────────────────────────────
    try:
        end   = now_ist()
        start = end - timedelta(minutes=LIQUIDITY_LOOKBACK_MIN)
        candles   = kite.historical_data(int(opt_token), start, end, "minute")
        total_vol = sum(c.get("volume", 0) for c in candles)
        min_req   = MIN_OPTION_VOLUME_LOTS * max(lot_size, 1)
        if total_vol < min_req:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | "
                  f"vol={total_vol} last {LIQUIDITY_LOOKBACK_MIN}m < {min_req} (stale market)")
            return False
    except Exception as e:
        # Fail-closed: if the volume check itself fails we cannot verify liquidity.
        print(f"[ORDER] Liquidity FAIL {opt_symbol} | volume check error: {e}")
        return False

    # ── 2. Bid-ask spread + OI from live quote ────────────────────────────────
    try:
        key  = f"NFO:{opt_symbol}"
        q    = kite.quote([key])
        data = q.get(key, {})
        oi   = data.get("oi", 0)
        depth     = data.get("depth", {})
        best_bid  = (depth.get("buy",  [{}]) or [{}])[0].get("price", 0)
        best_ask  = (depth.get("sell", [{}]) or [{}])[0].get("price", 0)

        # OI check: below 300 contracts the book is too thin
        if oi < MIN_OI:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | OI={oi} < {MIN_OI} (thin market)")
            return False

        # No bids at all = no exit possible
        if not best_bid or best_bid <= 0:
            print(f"[ORDER] Liquidity FAIL {opt_symbol} | no bids (cannot sell — avoid)")
            return False

        # Spread check: wide spread means exiting will bleed us dry
        if best_ask and best_ask > best_bid:
            mid        = (best_bid + best_ask) / 2
            spread_pct = (best_ask - best_bid) / mid * 100
            if spread_pct > MAX_SPREAD_PCT:
                print(f"[ORDER] Liquidity FAIL {opt_symbol} | "
                      f"spread {spread_pct:.1f}% > {MAX_SPREAD_PCT}% (wide — exit risk)")
                return False

    except Exception as e:
        print(f"[ORDER] Spread/OI check error {opt_symbol}: {e} — allowing (fail-open)")

    return True


# ── ATM option selection (nearest expiry, strike closest to LTP) ──────────────
def select_option(symbol: str, ltp: float, direction: str
                  ) -> Tuple[Optional[str], Optional[str], float, str]:
    """Returns (token, tradingsymbol, strike, expiry_iso)."""
    cache       = load_instrument_cache()
    option_type = "CE" if direction == "call" else "PE"
    today       = date.today()

    contracts = [(t, m) for t, m in cache.items()
                 if m.get("name") == symbol.upper()
                 and m.get("instrument_type") == option_type]
    if not contracts:
        return None, None, 0.0, ""

    # nearest non-expired expiry
    def exp_date(m):
        try:    return date.fromisoformat(m["expiry"])
        except Exception: return date(2099, 1, 1)
    future = [(t, m) for t, m in contracts if exp_date(m) >= today]
    if not future:
        return None, None, 0.0, ""
    nearest = min(exp_date(m) for _, m in future)
    same    = [(t, m) for t, m in future if exp_date(m) == nearest]

    # ATM = strike closest to LTP
    tok, m = min(same, key=lambda x: abs(float(x[1]["strike"]) - ltp))

    # subscribe this option so its live LTP flows into market_state
    try:
        from backend.core.tick_engine import tick_engine
        tick_engine.subscribe_options([{"token": tok,
                                         "tradingsymbol": m["tradingsymbol"],
                                         "name": m["name"]}])
    except Exception:
        pass
    return tok, m["tradingsymbol"], float(m["strike"]), m["expiry"]


# ── Entry ─────────────────────────────────────────────────────────────────────
def place_entry_order(env, symbol, token, direction, session_id, entry_logic,
                      indicators, sl_pct_override=None, target_pct_override=None) -> Optional[Trade]:
    if not market.is_feed_connected():
        print(f"[ORDER] REJECTED — feed disconnected ({symbol} {direction}).")
        return None
    if market.is_trading_halted():
        print(f"[ORDER] REJECTED — trading halted: {market.get_halt_reason()}")
        return None

    settings        = get_settings()
    available_funds = settings.get("available_funds") or 500_000
    trade_sl_pct    = sl_pct_override     if sl_pct_override     is not None else settings.get("trade_sl_pct", 5.0)
    target_pct      = target_pct_override if target_pct_override is not None else settings.get("target_profit_pct", 0.0)

    ltp = market.get_ltp(token)
    if not ltp:
        print(f"[ORDER] REJECTED — no LTP for {symbol}")
        return None

    opt_token, opt_symbol, strike, expiry = select_option(symbol, ltp, direction)
    if not opt_token or not opt_symbol:
        print(f"[ORDER] REJECTED — no ATM option found for {symbol}")
        return None

    premium = option_premium(opt_token, opt_symbol)
    if not premium:
        print(f"[ORDER] REJECTED — could not read {opt_symbol} premium")
        return None

    meta     = get_meta(token)
    lot_size = meta.get("lot_size", 1) or 1

    if not _has_liquidity(opt_token, opt_symbol, lot_size):
        print(f"[ORDER] REJECTED — {opt_symbol} too illiquid to trade safely")
        return None

    qty      = calc_quantity(available_funds, premium, lot_size)
    trade_sl_price = round(premium * (1 - trade_sl_pct / 100), 2)
    target_price   = round(premium * (1 + target_pct / 100), 2) if target_pct > 0 else None

    if env == TradeEnv.PAPER:
        entry_price = premium
        print(f"[PAPER] BUY {qty}x {opt_symbol} @ ₹{entry_price:.2f} | SL ₹{trade_sl_price:.2f}")
    else:
        try:
            kite = broker.kite()
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR, exchange=kite.EXCHANGE_NFO,
                tradingsymbol=opt_symbol, transaction_type=kite.TRANSACTION_TYPE_BUY,
                quantity=qty * lot_size, product=kite.PRODUCT_MIS,
                order_type=kite.ORDER_TYPE_MARKET,
            )
            entry_price = premium
            print(f"[LIVE] BUY {opt_symbol} qty {qty*lot_size} | order {order_id}")
        except Exception as e:
            print(f"[ORDER] LIVE entry failed: {e}")
            return None

    db = Session()
    try:
        trade = Trade(
            session_id=session_id, env=env, status=TradeStatus.OPEN,
            direction=TradeDirection(direction), symbol=symbol,
            option_symbol=opt_symbol, strike=strike, expiry=expiry,
            option_type="CE" if direction == "call" else "PE",
            entry_price=entry_price, quantity=qty, lot_size=lot_size,
            trade_sl_pct=trade_sl_pct, trade_sl_price=trade_sl_price,
            target_price=target_price, entry_logic=entry_logic,
            indicators_snapshot=indicators, entered_at=now_ist(),
        )
        db.add(trade); db.commit(); db.refresh(trade)
        print(f"[ORDER] Trade #{trade.id} | {symbol} {direction.upper()} {opt_symbol} "
              f"@ ₹{entry_price:.2f} | {env}")
        return trade
    except Exception as e:
        print(f"[ORDER] DB write error: {e}")
        return None
    finally:
        db.close()


# ── Exit ──────────────────────────────────────────────────────────────────────
def place_exit_order(trade_id: int, exit_price: float, reason: str, env: TradeEnv) -> bool:
    if env == TradeEnv.LIVE:
        db = Session()
        try:
            trade = db.query(Trade).filter(Trade.id == trade_id).first()
            if not trade or not trade.option_symbol:
                return False
            kite = broker.kite()
            order_id = kite.place_order(
                variety=kite.VARIETY_REGULAR, exchange=kite.EXCHANGE_NFO,
                tradingsymbol=trade.option_symbol,
                transaction_type=kite.TRANSACTION_TYPE_SELL,
                quantity=trade.quantity * trade.lot_size,
                product=kite.PRODUCT_MIS, order_type=kite.ORDER_TYPE_MARKET,
            )
            print(f"[LIVE] SELL {trade.option_symbol} | {reason} | order {order_id}")
            return True
        except Exception as e:
            print(f"[ORDER] LIVE exit failed: {e}")
            return False
        finally:
            db.close()
    print(f"[PAPER] EXIT trade #{trade_id} @ ₹{exit_price:.2f} | {reason}")
    return True
