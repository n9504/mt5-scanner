from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from datetime import date
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/trades", tags=["trades"])

class TradeSync(BaseModel):
    ticket:              int
    account_id:          str
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
    tick_value:          Optional[float] = None
    tick_size:           Optional[float] = None
    margin_level:        Optional[float] = None

# ── EA syncs trade data (open + closed) ──
@router.post("/sync")
async def sync_trades(
    trades:    List[TradeSync],
    bg:        BackgroundTasks,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    updated = 0
    inserted = 0
    for t in trades:
      try:
        # Check if trade exists
        existing = supabase_admin.table("trades")\
            .select("id, status")\
            .eq("tenant_id", tenant_id)\
            .eq("ticket", t.ticket)\
            .execute()

        data = {
            "tenant_id":         tenant_id,
            "account_id":        t.account_id,
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
        if t.post_exit_high is not None:    data["post_exit_high"]    = t.post_exit_high
        if t.post_exit_low  is not None:    data["post_exit_low"]     = t.post_exit_low
        if t.post_exit_tracked is not None: data["post_exit_tracked"] = t.post_exit_tracked
        if t.tick_value is not None:        data["tick_value"]        = t.tick_value
        if t.tick_size  is not None:        data["tick_size"]         = t.tick_size
        if t.margin_level is not None:    data["margin_level"]      = t.margin_level

        # Calculate exit quality if we have post-exit data
        if t.post_exit_high and t.post_exit_low and t.close_price and t.bias:
            if t.bias == "BUY":
                missed    = t.post_exit_high - float(t.close_price)
                gave_back = float(t.close_price) - t.post_exit_low
            else:
                missed    = float(t.close_price) - t.post_exit_low
                gave_back = t.post_exit_high - float(t.close_price)
            if missed > gave_back * 2:    data["exit_quality"] = "EARLY"
            elif gave_back > missed * 2:  data["exit_quality"] = "PERFECT"
            else:                         data["exit_quality"] = "GOOD"

        if existing.data:
            # Always update if incoming status is CLOSED (close beats open)
            if t.status == "CLOSED" or existing.data[0]["status"] != "CLOSED":
                supabase_admin.table("trades")\
                    .update(data)\
                    .eq("id", existing.data[0]["id"])\
                    .execute()
                updated += 1
        else:
            supabase_admin.table("trades").insert(data).execute()
            inserted += 1
            # Only trigger AI analysis for OPEN trades (live) not historical
            if t.status == "OPEN":
                bg.add_task(_trigger_entry_analysis, existing_id=None,
                           tenant_id=tenant_id, ticket=t.ticket)

      except Exception as e:
        import traceback
        print(f"[TradeSync ERROR] {e}\n{traceback.format_exc()}")
        raise HTTPException(500, f"Trade sync error: {str(e)}")

    return {"inserted": inserted, "updated": updated}

async def _trigger_entry_analysis(existing_id, tenant_id, ticket):
    """Placeholder - actual analysis triggered when screenshot arrives."""
    pass

# ── Dashboard reads trades ──
@router.get("")
async def list_trades(
    status:    Optional[str] = None,
    scanner:   Optional[str] = None,
    symbol:    Optional[str] = None,
    period:    Optional[str] = "today",
    tenant_id: str = Depends(get_current_tenant)
):
    # Exclude screenshot fields - they are large and slow down list loading
    # Screenshots loaded separately when trade is expanded
    FIELDS = (
        "id,tenant_id,account_id,ticket,scanner,symbol,bias,lot,"
        "entry_price,sl,tp,close_price,rr_target,rr_actual,open_time,close_time,"
        "gross_pnl,commission,swap,net_pnl,execution_outcome,status,"
        "session,tags,notes,ai_analysis,entry_analysis,exit_analysis,"
        "post_exit_tracked,post_exit_high,post_exit_low,exit_quality,tick_value,tick_size,margin_level,created_at"
    )
    # No limit for all-time, 200 for filtered periods
    row_limit = 2000 if period == "all" else 200
    query = supabase_admin.table("trades")\
        .select(FIELDS)\
        .eq("tenant_id", tenant_id)\
        .order("open_time", desc=True)\
        .limit(row_limit)

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

# ── Delete all trades for tenant ──
@router.delete("/all")
async def delete_all_trades(tenant_id: str = Depends(get_current_tenant)):
    supabase_admin.table("trades").delete().eq("tenant_id", tenant_id).execute()
    return {"status": "ok", "message": "All trades deleted"}

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

# ── Get screenshots for a specific trade (lazy load) ──
@router.get("/{trade_id}/screenshots")
async def get_screenshots(
    trade_id:  str,
    tenant_id: str = Depends(get_current_tenant)
):
    res = supabase_admin.table("trades")\
        .select("id,screenshot_entry,screenshot_exit,screenshot_h1_entry,screenshot_h1_exit")\
        .eq("id", trade_id)\
        .eq("tenant_id", tenant_id)\
        .limit(1).execute()
    if not res.data:
        return {}
    return res.data[0]
