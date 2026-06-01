"""
order_executor.py — Places and squares off option orders.

Paper mode:  simulates execution at current LTP, writes directly to DB.
Live mode:   calls Angel One SmartAPI placeOrder() then writes to DB.

Critical rules:
  - If feed is disconnected: REJECT order (no blind trades).
  - All orders are MARKET orders (no limit — options spread is wide).
  - Only OPTION BUYING (BUY to enter, SELL to exit). No shorting.
  - Quantity is computed from available_funds and estimated premium.
"""

import json, os, sys
from datetime import datetime
from typing import Optional, Tuple

import pyotp
from SmartApi import SmartConnect

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

from backend.database import (
    Session, Trade, TradingSession, TradingSession,
    TradeEnv, TradeDirection, TradeStatus
)
from backend.core.market_state     import market
from backend.core.settings_manager import get_settings
from backend.core.session_manager  import get_or_create_session, update_portfolio_balance
from backend.core.stock_universe   import find_option_token, get_meta


# ── Angel One session cache (reuse within the day) ────────────────────────────
_smart_session: Optional[SmartConnect] = None

def _get_smart() -> SmartConnect:
    global _smart_session
    if _smart_session:
        return _smart_session
    cfg  = json.load(open(os.path.join(ROOT, "config.json")))
    totp = pyotp.TOTP(cfg["totp_secret"]).now()
    obj  = SmartConnect(api_key=cfg["api_key"])
    data = obj.generateSession(cfg["client_id"], cfg["mpin"], totp)
    if data["status"]:
        _smart_session = obj
        return obj
    raise ConnectionError(f"Angel One login failed: {data['message']}")


# ── Quantity calculator ────────────────────────────────────────────────────────

def calc_quantity(available_funds: float, premium: float, lot_size: int) -> int:
    """
    How many lots can we buy?
    Use at most 50% of available funds per trade.
    Minimum 1 lot.
    """
    budget    = available_funds * 0.5
    cost_1lot = premium * lot_size
    if cost_1lot <= 0:
        return 1
    lots = int(budget // cost_1lot)
    return max(lots, 1)


# ── Option selection ───────────────────────────────────────────────────────────

def select_option(
    symbol:    str,
    ltp:       float,
    direction: str,   # "call" or "put"
) -> Tuple[Optional[str], Optional[str], float, str]:
    """
    Returns (option_token, option_symbol, strike, expiry).
    Picks ATM strike, nearest weekly expiry.
    Returns (None, None, 0, '') on failure.
    """
    from backend.core.stock_universe import load_instrument_cache
    import math

    # Round to nearest 50 (standard Nifty options strike increment)
    meta      = get_meta(symbol) if isinstance(symbol, str) else {}
    # For individual stocks the increment is typically 50 or 100
    # Use 50 as default
    increment = 50
    atm_strike = round(ltp / increment) * increment

    option_type = "CE" if direction == "call" else "PE"

    cache = load_instrument_cache()
    # Find contracts matching symbol, option_type, expiry closest to today
    from datetime import date
    today = date.today()

    best_token  = None
    best_symbol = None
    best_expiry = None
    min_days    = 9999

    for token, meta_i in cache.items():
        if (meta_i.get("name",     "").upper() == symbol.upper() and
            meta_i.get("instrumenttype", "").upper() == option_type and
            str(int(float(meta_i.get("strike", 0)))) == str(int(atm_strike))):
            try:
                # expiry format: DDMMMYYYY e.g. 26JUN2025
                exp_str = meta_i.get("expiry", "")
                exp_dt  = datetime.strptime(exp_str, "%d%b%Y").date()
                days    = (exp_dt - today).days
                if 0 <= days < min_days:
                    min_days    = days
                    best_token  = token
                    best_symbol = meta_i.get("symbol", "")
                    best_expiry = exp_str
            except Exception:
                continue

    strike = atm_strike
    return best_token, best_symbol, strike, best_expiry or ""


# ── Entry order ───────────────────────────────────────────────────────────────

def place_entry_order(
    env:          TradeEnv,
    symbol:       str,
    token:        str,
    direction:    str,
    session_id:   int,
    entry_logic:  str,
    indicators:   dict,
) -> Optional[Trade]:
    """
    Place an entry (BUY) order.
    Returns the Trade record on success, None on failure.
    """
    # ── Feed check ────────────────────────────────────────────────────────────
    if not market.is_feed_connected():
        print(f"[ORDER] REJECTED — feed disconnected. Cannot place {symbol} {direction} entry.")
        return None

    if market.is_trading_halted():
        print(f"[ORDER] REJECTED — trading halted: {market.get_halt_reason()}")
        return None

    settings       = get_settings()
    available_funds = settings.get("available_funds", 0)
    trade_sl_pct   = settings.get("trade_sl_pct", 5.0)
    target_pct     = settings.get("target_profit_pct", 0.0)

    # Get stock LTP
    ltp = market.get_ltp(token)
    if not ltp:
        print(f"[ORDER] REJECTED — no LTP for {symbol}")
        return None

    # Select option
    opt_token, opt_symbol, strike, expiry = select_option(symbol, ltp, direction)

    # Premium: use option LTP if available, else estimate
    premium = market.get_ltp(opt_token) if opt_token else None
    if not premium:
        premium = round(ltp * 0.015, 2)     # fallback estimate

    meta     = get_meta(token)
    lot_size = meta.get("lot_size", 1)
    qty      = calc_quantity(available_funds, premium, lot_size)

    trade_sl_price  = round(premium * (1 - trade_sl_pct / 100), 2)
    target_price    = round(premium * (1 + target_pct / 100), 2) if target_pct > 0 else None

    # ── Paper mode ───────────────────────────────────────────────────────────
    if env == TradeEnv.PAPER:
        entry_price = premium    # paper fills at estimated premium
        order_id    = f"PAPER-{datetime.now().strftime('%H%M%S%f')}"
        print(f"[PAPER] BUY {qty}x {opt_symbol or symbol+' '+direction.upper()} "
              f"@ ₹{entry_price:.2f} | SL: ₹{trade_sl_price:.2f}")

    # ── Live mode ─────────────────────────────────────────────────────────────
    else:
        if not opt_token or not opt_symbol:
            print(f"[ORDER] REJECTED — could not find option token for {symbol}")
            return None
        try:
            smart  = _get_smart()
            resp   = smart.placeOrder({
                "variety":         "NORMAL",
                "tradingsymbol":   opt_symbol,
                "symboltoken":     opt_token,
                "transactiontype": "BUY",
                "exchange":        "NFO",
                "ordertype":       "MARKET",
                "producttype":     "INTRADAY",
                "duration":        "DAY",
                "price":           "0",
                "squareoff":       "0",
                "stoploss":        "0",
                "quantity":        str(qty * lot_size),
            })
            if not resp.get("status"):
                print(f"[ORDER] LIVE order FAILED: {resp.get('message')}")
                return None
            order_id    = resp["data"]["orderid"]
            entry_price = premium    # will be updated by order book later
            print(f"[LIVE] BUY order placed | {opt_symbol} | Qty: {qty*lot_size} | "
                  f"OrderID: {order_id}")
        except Exception as e:
            print(f"[ORDER] Exception placing live order: {e}")
            return None

    # ── Write to DB ───────────────────────────────────────────────────────────
    db = Session()
    try:
        trade = Trade(
            session_id          = session_id,
            env                 = env,
            status              = TradeStatus.OPEN,
            direction           = TradeDirection(direction),
            symbol              = symbol,
            option_symbol       = opt_symbol or f"{symbol}-{direction.upper()}",
            strike              = strike,
            expiry              = expiry,
            option_type         = "CE" if direction == "call" else "PE",
            entry_price         = entry_price,
            quantity            = qty,
            lot_size            = lot_size,
            trade_sl_pct        = trade_sl_pct,
            trade_sl_price      = trade_sl_price,
            target_price        = target_price,
            entry_logic         = entry_logic,
            indicators_snapshot = indicators,
            entered_at          = datetime.utcnow(),
        )
        db.add(trade)
        db.commit()
        db.refresh(trade)
        print(f"[ORDER] Trade #{trade.id} recorded | {symbol} {direction.upper()} "
              f"| Entry: ₹{entry_price:.2f} | Env: {env}")
        return trade
    except Exception as e:
        print(f"[ORDER] DB write error: {e}")
        return None
    finally:
        db.close()


# ── Exit order ────────────────────────────────────────────────────────────────

def place_exit_order(
    trade_id:   int,
    exit_price: float,
    reason:     str,
    env:        TradeEnv,
) -> bool:
    """
    Called by RiskEngine exit callback.
    In live mode: places SELL order on Angel One.
    In paper mode: nothing extra (DB already updated by RiskEngine).
    """
    if env == TradeEnv.LIVE:
        db = Session()
        try:
            trade = db.query(Trade).filter(Trade.id == trade_id).first()
            if not trade or not trade.option_symbol:
                return False
            # Find option token
            from backend.core.stock_universe import load_instrument_cache
            cache = load_instrument_cache()
            opt_token = next(
                (t for t, m in cache.items()
                 if m.get("symbol", "").upper() == trade.option_symbol.upper()),
                None
            )
            if not opt_token:
                print(f"[ORDER] EXIT: cannot find token for {trade.option_symbol}")
                return False
            smart = _get_smart()
            resp  = smart.placeOrder({
                "variety":         "NORMAL",
                "tradingsymbol":   trade.option_symbol,
                "symboltoken":     opt_token,
                "transactiontype": "SELL",
                "exchange":        "NFO",
                "ordertype":       "MARKET",
                "producttype":     "INTRADAY",
                "duration":        "DAY",
                "price":           "0",
                "squareoff":       "0",
                "stoploss":        "0",
                "quantity":        str(trade.quantity * trade.lot_size),
            })
            if resp.get("status"):
                print(f"[LIVE] SELL order placed | {trade.option_symbol} | "
                      f"Reason: {reason} | OrderID: {resp['data']['orderid']}")
                return True
            else:
                print(f"[ORDER] EXIT order failed: {resp.get('message')}")
                return False
        except Exception as e:
            print(f"[ORDER] EXIT exception: {e}")
            return False
        finally:
            db.close()

    # Paper mode — DB already updated by risk engine
    print(f"[PAPER] EXIT trade #{trade_id} @ ₹{exit_price:.2f} | {reason}")
    return True
