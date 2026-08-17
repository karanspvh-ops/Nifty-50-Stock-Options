"""order_executor.py — Places and squares off option orders via Zerodha Kite.

Paper mode:  fills at the real option LTP (Kite quote), writes to DB.
Live mode:   kite.place_order() (NFO, MARKET, MIS intraday) then writes to DB.

Rules:
  - Feed disconnected -> reject.
  - MARKET orders only (option spreads are wide).
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

from backend.execution.quantity        import calc_quantity
from backend.execution.option_selector import select_option, option_premium
from backend.execution.liquidity       import has_liquidity


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
    if premium < 3.0:
        print(f"[ORDER] REJECTED — {opt_symbol} premium ₹{premium:.2f} below ₹3 minimum (untradeable tick size in live)")
        return None

    meta     = get_meta(token)
    lot_size = meta.get("lot_size", 1) or 1

    if not has_liquidity(opt_token, opt_symbol, lot_size, symbol=symbol):
        print(f"[ORDER] REJECTED — {opt_symbol} too illiquid to trade safely")
        return None

    qty            = calc_quantity(available_funds, premium, lot_size, max_positions)
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
