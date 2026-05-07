from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/trades", tags=["trades"])

class TradeSync(BaseModel):
    ticket:              int
    account_id:          str
    signal_id:           Optional[str]   = None
    scanner:             str             = "MANUAL"
    symbol:              str
    bias:                str
    lot:                 float
    entry_price:         float
    sl:                  Optional[float] = None
    tp:                  Optional[float] = None
    close_price:         Optional[float] = None
    rr_target:           Optional[float] = None
    rr_actual:           Optional[float] = None
    open_time:           Optional[str]   = None
    close_time:          Optional[str]   = None
    gross_pnl:           Optional[float] = None
    commission:          Optional[float] = 0
    swap:                Optional[float] = 0
    net_pnl:             Optional[float] = None
    peak_progress:       Optional[float] = 0
    execution_outcome:   Optional[str]   = None
    status:              str             = "OPEN"
    post_exit_high:      Optional[float] = None
    post_exit_low:       Optional[float] = None
    post_exit_tracked:   Optional[bool]  = None

# ── EA syncs trade data (open + closed) ──
@router.post("/sync")
async def sync_trades(
    trades:    List[TradeSync],
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    updated = 0
    inserted = 0
    for t in trades:
        # Check if trade exists
        existing = supabase_admin.table("trades")\
            .select("id, status")\
            .eq("tenant_id", tenant_id)\
            .eq("ticket", t.ticket)\
            .execute()

        data = {
            "tenant_id":         tenant_id,
            "account_id":        t.account_id,
            "signal_id":         t.signal_id,
            "ticket":            t.ticket,
            "scanner":           t.scanner,
            "symbol":            t.symbol,
            "bias":              t.bias,
            "lot":               t.lot,
            "entry_price":       t.entry_price,
            "sl":                t.sl,
            "tp":                t.tp,
            "close_price":       t.close_price,
            "rr_target":         t.rr_target,
            "rr_actual":         t.rr_actual,
            "open_time":         t.open_time,
            "close_time":        t.close_time,
            "gross_pnl":         t.gross_pnl,
            "commission":        t.commission,
            "swap":              t.swap,
            "net_pnl":           t.net_pnl,
            "peak_progress":     t.peak_progress,
            "execution_outcome": t.execution_outcome,
            "status":            t.status,
        }
        if t.post_exit_high is not None:   data["post_exit_high"]    = t.post_exit_high
        if t.post_exit_low  is not None:   data["post_exit_low"]     = t.post_exit_low
        if t.post_exit_tracked is not None: data["post_exit_tracked"] = t.post_exit_tracked
        # Calculate exit quality if we have post-exit data
        if t.post_exit_high and t.post_exit_low and t.close_price and t.bias:
            if t.bias == "BUY":
                missed = t.post_exit_high - float(t.close_price)
                gave_back = float(t.close_price) - t.post_exit_low
                if missed > gave_back * 2: data["exit_quality"] = "EARLY"
                elif gave_back > missed * 2: data["exit_quality"] = "PERFECT"
                else: data["exit_quality"] = "GOOD"
            else:
                missed = float(t.close_price) - t.post_exit_low
                gave_back = t.post_exit_high - float(t.close_price)
                if missed > gave_back * 2: data["exit_quality"] = "EARLY"
                elif gave_back > missed * 2: data["exit_quality"] = "PERFECT"
                else: data["exit_quality"] = "GOOD"
        dummy = None

        if existing.data:
            # Only update if not already closed
            if existing.data[0]["status"] != "CLOSED":
                supabase_admin.table("trades")\
                    .update(data)\
                    .eq("id", existing.data[0]["id"])\
                    .execute()
                updated += 1
        else:
            supabase_admin.table("trades").insert(data).execute()
            inserted += 1

    return {"inserted": inserted, "updated": updated}

# ── Dashboard reads trades ──
@router.get("")
async def list_trades(
    status:    Optional[str] = None,
    scanner:   Optional[str] = None,
    symbol:    Optional[str] = None,
    period:    Optional[str] = "today",
    tenant_id: str = Depends(get_current_tenant)
):
    query = supabase_admin.table("trades")\
        .select("*")\
        .eq("tenant_id", tenant_id)\
        .order("open_time", desc=True)\
        .limit(200)

    if status:  query = query.eq("status", status)
    if scanner and scanner != "all": query = query.eq("scanner", scanner)
    if symbol:  query = query.eq("symbol", symbol)

    today = str(date.today())
    # Only apply period filter for closed trades
    if status != "OPEN":
        if period == "today":
            query = query.gte("close_time", f"{today}T00:00:00")
        elif period == "week":
            from datetime import timedelta, datetime
            mon = (datetime.utcnow() - timedelta(days=datetime.utcnow().weekday())).strftime("%Y-%m-%d")
            query = query.gte("close_time", f"{mon}T00:00:00")
        elif period == "month":
            from datetime import datetime
            mon = datetime.utcnow().strftime("%Y-%m")
            query = query.like("close_time", f"{mon}%")

    res = query.execute()
    return res.data or []

# ── Performance summary ──
@router.get("/performance")
async def performance(
    tenant_id: str = Depends(get_current_tenant)
):
    today = str(date.today())
    res = supabase_admin.table("trades")\
        .select("scanner, symbol, net_pnl, execution_outcome, rr_actual, session, s3_condition")\
        .eq("tenant_id", tenant_id)\
        .eq("status", "CLOSED")\
        .gte("close_time", f"{today}T00:00:00")\
        .execute()

    trades = res.data or []
    total  = len(trades)
    wins   = sum(1 for t in trades if (t.get("execution_outcome") or "").startswith("WIN"))
    net    = round(sum(float(t.get("net_pnl") or 0) for t in trades), 2)

    return {
        "today": {
            "trades":   total,
            "wins":     wins,
            "losses":   total - wins,
            "win_rate": round(wins/total*100, 1) if total else 0,
            "net_pnl":  net,
        },
        "trades": trades,
    }
