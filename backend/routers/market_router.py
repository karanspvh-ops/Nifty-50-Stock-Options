"""
market_router.py — REST + WebSocket endpoints for all market data.

WebSocket /ws/market streams:
  - All stock ticks (LTP, % move)
  - Sector aggregates
  - Feed health
  - Trading halt status

REST endpoints for on-demand data.
"""

import asyncio, json
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query
from backend.core.market_state  import market
from backend.core.tick_engine   import tick_engine
from backend.core.stock_universe import get_stocks_for_index, get_stocks_in_sector
from backend.core.settings_manager import get_active_index

router = APIRouter(prefix="/api/market", tags=["market"])


# ── REST ─────────────────────────────────────────────────────────────────────

@router.get("/status")
def feed_status():
    return tick_engine.get_status()


@router.get("/ticks")
def all_ticks():
    """Snapshot of all current LTPs."""
    return market.get_all_ticks()


@router.get("/moves")
def stock_moves():
    """% moves for all tracked stocks."""
    return market.get_all_stock_moves()


@router.get("/sectors")
def sector_moves():
    return market.get_sector_moves()


@router.get("/sectors/top")
def top_sector():
    top = market.get_top_sector()
    if not top:
        return {"sector": None, "data": {}}
    return {"sector": top[0], "data": top[1]}


@router.get("/candles/{token}")
def get_candles(token: str, n: int = Query(50, le=100)):
    return market.get_candles(token, n)


@router.get("/stocks")
def list_stocks(index: Optional[str] = Query(None)):
    stocks  = get_stocks_for_index("FNO", fno_only=True)        # combined F&O universe (~211)
    moves   = market.get_all_stock_moves()
    result  = []
    for s in stocks:
        move = moves.get(s["token"], {})
        result.append({
            "token":      s["token"],
            "symbol":     s["symbol"],
            "sector":     s["sector"],
            "ltp":        move.get("ltp"),
            "pct_change": move.get("pct_change"),
            "direction":  move.get("direction"),
        })
    return sorted(result, key=lambda x: abs(x.get("pct_change") or 0), reverse=True)


@router.get("/ranking")
def stock_ranking(
    index: Optional[str] = Query(None),
    order: str           = Query("most_likely"),  # most_likely | least_likely
):
    """
    Rank every stock in the index sector-wise.

    R-Factor = stock's |move| relative to its sector's average |move|.
      > 1.0  → stronger than its sector (leader)
      < 1.0  → weaker than its sector (laggard)

    Score   = |pct_change| * (1 + r_factor * 0.25)   → "tradability"
    Signal  = up / down (direction of the move)

    order="most_likely"  → strongest movers first (best to trade)
    order="least_likely" → weakest / flattest first
    """
    stocks  = get_stocks_for_index("FNO", fno_only=True)        # combined F&O universe (~211)
    moves   = market.get_all_stock_moves()
    sectors_live = market.get_sector_moves()

    # Group rows by sector with computed score
    by_sector: dict = {}
    for s in stocks:
        move = moves.get(s["token"])
        if not move:
            continue
        sector  = s["sector"]
        pct     = move.get("pct_change", 0) or 0
        sec_avg = abs(sectors_live.get(sector, {}).get("pct_change", 0)) or 0.1
        r_factor = round(abs(pct) / sec_avg, 2)
        score    = round(abs(pct) * (1 + r_factor * 0.25), 3)
        row = {
            "token":      s["token"],
            "symbol":     s["symbol"],
            "ltp":        move.get("ltp"),
            "pct_change": pct,
            "r_factor":   r_factor,
            "score":      score,
            "signal":     "up" if pct > 0 else ("down" if pct < 0 else "flat"),
        }
        by_sector.setdefault(sector, []).append(row)

    reverse = (order != "least_likely")
    result  = []
    for sector, rows in by_sector.items():
        rows.sort(key=lambda x: x["score"], reverse=reverse)
        sec_data = sectors_live.get(sector, {})
        result.append({
            "sector":      sector,
            "sector_pct":  sec_data.get("pct_change", 0),
            "sector_dir":  sec_data.get("direction", "flat"),
            "up_count":    sum(1 for r in rows if r["signal"] == "up"),
            "down_count":  sum(1 for r in rows if r["signal"] == "down"),
            "stocks":      rows,
        })

    # Order sectors by absolute momentum (strongest sector first)
    result.sort(key=lambda x: abs(x["sector_pct"]), reverse=True)
    return result


@router.post("/engine/start")
def start_engine():
    tick_engine.start()
    return {"status": "started"}


@router.post("/engine/stop")
def stop_engine():
    tick_engine.stop()
    return {"status": "stopped"}


# ── WebSocket broadcast ───────────────────────────────────────────────────────

class ConnectionManager:
    def __init__(self):
        self.active: list[WebSocket] = []

    async def connect(self, ws: WebSocket):
        await ws.accept()
        self.active.append(ws)

    def disconnect(self, ws: WebSocket):
        if ws in self.active:
            self.active.remove(ws)

    async def broadcast(self, data: dict):
        dead = []
        for ws in self.active:
            try:
                await ws.send_json(data)
            except:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)


ws_manager = ConnectionManager()


@router.websocket("/ws/market")
async def market_ws(websocket: WebSocket):
    """
    Streams real-time market state to frontend every second.
    Payload includes: ticks, sector moves, feed health, halt status.
    """
    await ws_manager.connect(websocket)
    try:
        while True:
            payload = {
                "type":        "market_update",
                "ts":          datetime.now().isoformat(),
                "feed_health": market.get_feed_health(),
                "halt_status": {
                    "halted": market.is_trading_halted(),
                    "reason": market.get_halt_reason(),
                },
                "sector_moves": market.get_sector_moves(),
                "stock_moves":  market.get_all_stock_moves(),
            }
            await websocket.send_json(payload)
            await asyncio.sleep(1)          # 1-second push cadence
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
    except Exception as e:
        ws_manager.disconnect(websocket)
        print(f"[WS] Client error: {e}")


@router.websocket("/ws/candles/{token}")
async def candle_ws(websocket: WebSocket, token: str):
    """
    Streams latest candle + current tick for a single stock.
    Used by the 5-min chart component on the frontend.
    """
    await websocket.accept()
    try:
        while True:
            candle = market.get_latest_candle(token)
            tick   = market.get_tick(token)
            await websocket.send_json({
                "type":   "candle_update",
                "token":  token,
                "ts":     datetime.now().isoformat(),
                "candle": candle,
                "tick":   tick,
            })
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        pass
    except Exception as e:
        print(f"[WS-CANDLE] Error: {e}")
