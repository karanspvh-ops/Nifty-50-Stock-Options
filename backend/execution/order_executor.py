"""order_executor.py — Places and squares off option orders via Zerodha Kite.

Paper mode:  fills at the real option LTP (Kite quote), writes to DB.
Live mode:   live_orders.buy() (NFO, marketable LIMIT, MIS intraday), waits for
             the fill, then writes the real fill price and quantity to DB.

Rules:
  - Feed disconnected -> reject.
  - Marketable LIMIT orders with a capped buffer (option spreads are wide).
  - Option BUYING only (BUY to enter, SELL to exit). No shorting.
"""

from typing import Optional

from backend.core.clock          import now_ist
from backend.core.market_state   import market
from backend.core.settings_manager import get_settings
from backend.core.session_manager  import update_portfolio_balance
from backend.core.stock_universe   import get_meta
from backend.core.broker           import broker

from backend.database import Session, Trade, TradeEnv, TradeDirection, TradeStatus

from backend.execution.quantity        import calc_quantity, trading_capital
from backend.execution.option_selector import select_option, option_premium, option_bid_ask
from backend.execution.liquidity       import has_liquidity
from backend.execution                 import live_orders


def place_entry_order(env, symbol, token, direction, session_id, entry_logic,
                      indicators, sl_pct_override=None, target_pct_override=None,
                      max_positions: int = 5) -> Optional[Trade]:
    if not market.is_feed_connected():
        print(f"[ORDER] REJECTED — feed disconnected ({symbol} {direction}).")
        return None
    if market.is_trading_halted():
        print(f"[ORDER] REJECTED — trading halted: {market.get_halt_reason()}")
        return None

    settings        = get_settings()
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
    bid_entry, ask_entry, spread_pct_entry = option_bid_ask(opt_token, opt_symbol)
    if premium < 3.0:
        print(f"[ORDER] REJECTED — {opt_symbol} premium ₹{premium:.2f} below ₹3 minimum (untradeable tick size in live)")
        return None

    meta     = get_meta(token)
    lot_size = meta.get("lot_size", 1) or 1

    if not has_liquidity(opt_token, opt_symbol, lot_size, symbol=symbol):
        print(f"[ORDER] REJECTED — {opt_symbol} too illiquid to trade safely")
        return None

    capital, why_cap = trading_capital(live=(env == TradeEnv.LIVE))
    if capital is None:
        print(f"[ORDER] REJECTED — {symbol}: can't size the trade ({why_cap})")
        return None
    qty = calc_quantity(capital, premium, lot_size, max_positions)
    if qty <= 0:
        print(f"[ORDER] REJECTED — {opt_symbol}: one lot ₹{premium * lot_size:,.0f} exceeds the "
              f"per-trade budget ₹{capital / max(max_positions, 1):,.0f} ({why_cap})")
        return None
    if env == TradeEnv.LIVE:
        # Never send an order the account can't pay for right now
        from backend.execution.quantity import live_funds
        acct = live_funds()
        cash_now = float(acct.get("live_balance") or 0) if acct else 0.0
        afford = int(cash_now // (premium * lot_size * 1.03)) if premium > 0 else 0
        if afford < qty:
            print(f"[ORDER] {opt_symbol}: cutting {qty} lots to {afford} (cash now ₹{cash_now:,.0f})")
            qty = afford
        if qty <= 0:
            print(f"[ORDER] REJECTED — {opt_symbol}: not enough cash in Zerodha right now (₹{cash_now:,.0f})")
            return None
    trade_sl_price = round(premium * (1 - trade_sl_pct / 100), 2)
    target_price   = round(premium * (1 + target_pct / 100), 2) if target_pct > 0 else None

    if env == TradeEnv.PAPER:
        entry_price = premium
        print(f"[PAPER] BUY {qty}x {opt_symbol} @ ₹{entry_price:.2f} | SL ₹{trade_sl_price:.2f}")
    else:
        # Real order: record what Zerodha actually filled, at the real average
        # price. A rejected or unfilled BUY must never become an OPEN trade.
        try:
            fill = live_orders.buy(opt_symbol, qty * lot_size)
        except Exception as e:
            print(f"[ORDER] LIVE entry failed: {e}")
            return None
        qty = fill.filled_qty // lot_size
        if qty <= 0:
            print(f"[ORDER] LIVE entry filled {fill.filled_qty} units, below one lot of {lot_size}")
            return None
        entry_price    = fill.avg_price
        trade_sl_price = round(entry_price * (1 - trade_sl_pct / 100), 2)
        target_price   = round(entry_price * (1 + target_pct / 100), 2) if target_pct > 0 else None
        print(f"[LIVE] BOUGHT {qty}x{lot_size} {opt_symbol} @ ₹{entry_price:.2f} | order {fill.order_id}")

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
            bid_at_entry=bid_entry, ask_at_entry=ask_entry,
            spread_pct_at_entry=spread_pct_entry,
        )
        db.add(trade); db.commit(); db.refresh(trade)
        if env == TradeEnv.LIVE:
            from backend.execution import stop_orders
            stop_orders.place(trade.id, opt_symbol, qty * lot_size, trade_sl_price)
        market.push_trade_update({
            "type":          "trade_opened",
            "trade_id":      trade.id,
            "symbol":        symbol,
            "option_symbol": opt_symbol,
            "direction":     direction,
            "env":           env.value,
            "entry":         entry_price,
            "hard_sl":       trade_sl_price,
            "target":        target_price,
            "quantity":      qty,
            "lot_size":      lot_size,
            "entered_at":    trade.entered_at.isoformat(),
            "entry_logic":   entry_logic,
        })
        print(f"[ORDER] Trade #{trade.id} | {symbol} {direction.upper()} {opt_symbol} "
              f"@ ₹{entry_price:.2f} | {env}")
        return trade
    except Exception as e:
        print(f"[ORDER] DB write error: {e}")
        return None
    finally:
        db.close()


def place_exit_order(trade_id: int, exit_price: float, reason: str, env: TradeEnv) -> bool:
    """Post-exit notification. The live SELL itself is sent by
    RiskExitMixin._trigger_exit *before* the trade is closed in the DB, so a
    failed SELL leaves the trade OPEN and gets retried. Nothing to send here."""
    tag = "LIVE" if env == TradeEnv.LIVE else "PAPER"
    print(f"[{tag}] EXIT trade #{trade_id} @ ₹{exit_price:.2f} | {reason}")
    return True
