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
    initial_sl:          Optional[float] = None
    initial_tp:          Optional[float] = None

# ── EA syncs trade data (open + closed) ──
def _get_session(dt_str: str) -> str:
    if not dt_str: return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(dt_str).replace("Z","").replace("+00:00",""))
        h  = dt.hour
        if 0  <= h < 7:  return "Asia"
        if 7  <= h < 12: return "London"
        if 12 <= h < 17: return "London/US Overlap"
        if 17 <= h < 21: return "US"
        return "Asia"
    except: return ""

def _get_risk_tag(margin_level: float) -> str:
    if margin_level <= 0:   return ""
    if margin_level > 1000: return "No Risk"
    if margin_level > 700:  return "Balanced Risk"
    if margin_level > 300:  return "Elevated Risk"
    return "Aggressive Risk"

def _get_result_tag(outcome, entry, close, sl, tp, tick_size, bias):
    return ""


def _get_sl_direction(initial_sl, sl_chain, bias):
    chain = [s for s in sl_chain if s and s > 0]
    if not initial_sl or not chain:
        return "none"
    all_vals = [initial_sl] + chain
    widened = False
    trailed = False
    for i in range(1, len(all_vals)):
        prev = all_vals[i-1]
        curr = all_vals[i]
        if bias == "BUY":
            if curr > prev: trailed = True
            if curr < prev: widened = True
        else:
            if curr < prev: trailed = True
            if curr > prev: widened = True
    if widened: return "widened"
    if trailed: return "trailing"
    return "none"


def _compute_exit_behaviour(outcome, bias, entry, close, sl, tp, tick_size, initial_sl, sl_chain, initial_tp):
    oc = str(outcome).upper()
    threshold = 5 * tick_size if tick_size > 0 else 0
    is_win = close > entry if bias == "BUY" else close < entry
    near_tp = tp > 0 and abs(close - tp) <= threshold
    near_sl = sl > 0 and abs(close - sl) <= threshold
    sl_dir = _get_sl_direction(initial_sl, sl_chain, bias)

    if not sl and not tp:
        return "Lucky" if is_win else "Avoidable"
    if initial_sl and not sl and not is_win:
        return "Panic"
    if sl_dir == "widened":
        return "Lucky" if is_win else "Fear"
    if sl_dir == "trailing":
        if near_tp: return "Calm"
        if near_sl: return "Disciplined"
        if is_win:
            if tp and entry:
                total = abs(tp - entry)
                achieved = (close - entry) if bias == "BUY" else (entry - close)
                pct = (achieved / total * 100) if total > 0 else 0
                if pct >= 80: return "Patient"
                if pct >= 50: return "Conservative"
                return "Impatient"
            return "Conservative"
        return "Fear"
    if near_tp: return "Calm"
    if near_sl: return "Disciplined"
    if is_win:
        if tp and entry:
            total = abs(tp - entry)
            achieved = (close - entry) if bias == "BUY" else (entry - close)
            pct = (achieved / total * 100) if total > 0 else 0
            if pct >= 80: return "Patient"
            if pct >= 50: return "Conservative"
            return "Impatient"
        return "Conservative"
    return "Fear"


def _get_plan_tag(sl: float, tp: float, entry: float, bias: str) -> str:
    has_sl = sl and sl > 0
    has_tp = tp and tp > 0
    if not has_sl and not has_tp: return "Reckless"
    if has_sl and not has_tp:     return "Forcing Trade"
    if not has_sl and has_tp:     return "Gamble"
    # Both set — compute RR
    try:
        risk   = abs(float(entry) - float(sl))
        reward = abs(float(tp)    - float(entry))
        rr     = reward / risk if risk > 0 else 0
        if rr >= 0.7: return "Clarity"
        return "Desperate"
    except: return "Clarity"

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
            .select("id, status, tags, entry_tags, exit_tags, initial_sl, initial_tp, sl_1, sl_2, sl_3, sl_4, sl_5")\
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
            ex = existing.data[0]
            # Apply exit tags when trade first closes
            if t.status == "CLOSED" and ex.get("status") != "CLOSED":
                exit_session = _get_session(t.close_time or t.open_time or "")

                # Get SL movement chain from existing trade record
                sl_chain = [
                    ex.get("sl_1"), ex.get("sl_2"), ex.get("sl_3"),
                    ex.get("sl_4"), ex.get("sl_5")
                ]
                sl_chain = [s for s in sl_chain if s]

                entry_f    = float(t.entry_price or 0)
                close_f    = float(t.close_price or 0)
                sl_f       = float(t.sl or 0)
                tp_f       = float(t.tp or 0)
                tick_size  = float(t.tick_size or 0)
                initial_sl = float(ex.get("initial_sl") or 0)
                initial_tp = float(ex.get("initial_tp") or 0)
                bias       = t.bias or "BUY"
                outcome    = t.execution_outcome or ""

                behaviour_tag = _compute_exit_behaviour(
                    outcome, bias, entry_f, close_f, sl_f, tp_f,
                    tick_size, initial_sl, sl_chain, initial_tp
                )
                result_tag = _get_result_tag(
                    outcome, entry_f, close_f, sl_f, tp_f, tick_size, bias
                )

                new_exit_tags = []
                if exit_session:  new_exit_tags.append(exit_session)
                if behaviour_tag: new_exit_tags.append(behaviour_tag)
                if result_tag:    new_exit_tags.append(result_tag)

                if new_exit_tags:
                    data["exit_tags"] = new_exit_tags
                    flat = list(ex.get("tags") or [])
                    for tg in new_exit_tags:
                        if tg not in flat: flat.append(tg)
                    data["tags"] = flat
                    print(f"[Exit Tags] {t.symbol} → {new_exit_tags}")

            # Always update if incoming status is CLOSED
            if t.status == "CLOSED" or ex["status"] != "CLOSED":
                supabase_admin.table("trades")\
                    .update(data)\
                    .eq("id", ex["id"])\
                    .execute()
                updated += 1
        else:
            # New trade — compute entry tags
            if t.status == "OPEN":
                entry_session = _get_session(t.open_time or "")
                plan_tag      = _get_plan_tag(
                    float(t.sl or 0), float(t.tp or 0),
                    float(t.entry_price or 0), t.bias or "BUY"
                )
                risk_tag = _get_risk_tag(float(t.margin_level or 0))

                # Build entry_tags list
                entry_tags = []
                if entry_session: entry_tags.append(entry_session)
                if plan_tag:      entry_tags.append(plan_tag)

                data["entry_tags"]  = entry_tags
                data["exit_tags"]   = []

                # Risk tag in its own category (store in tags for now, separate column later)
                existing_tags = data.get("tags") or []
                if risk_tag and risk_tag not in existing_tags:
                    data["tags"] = existing_tags + [risk_tag]

                # Record initial SL/TP — never overwrite these
                if t.sl: data["initial_sl"] = t.sl
                if t.tp: data["initial_tp"] = t.tp

                print(f"[Entry Tags] {t.symbol} → {entry_tags} risk={risk_tag}")

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
        "entry_tags,exit_tags,"
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
class SlMovedBody(BaseModel):
    ticket:     int
    account_id: str
    sl_moved:   float

class TpMovedBody(BaseModel):
    ticket:     int
    account_id: str
    tp_moved:   float

@router.post("/sl_moved")
async def record_sl_moved(body: SlMovedBody, tenant_id: str = Depends(get_tenant_by_api_key)):
    res = supabase_admin.table("trades").select("id,sl_mod_count,sl_1,sl_2,sl_3,sl_4,sl_5")        .eq("tenant_id", tenant_id).eq("ticket", body.ticket).limit(1).execute()
    if not res.data: return {"status": "not_found"}
    t   = res.data[0]
    n   = int(t.get("sl_mod_count") or 0) + 1
    col = f"sl_{min(n, 5)}"
    supabase_admin.table("trades").update({
        col: body.sl_moved,
        "sl_mod_count": n,
    }).eq("id", t["id"]).execute()
    print(f"[SL Move] ticket={body.ticket} → {col}={body.sl_moved} count={n}")
    return {"status": "ok", "column": col}

@router.post("/tp_moved")
async def record_tp_moved(body: TpMovedBody, tenant_id: str = Depends(get_tenant_by_api_key)):
    res = supabase_admin.table("trades").select("id,tp_mod_count,tp_1,tp_2,tp_3,tp_4,tp_5")        .eq("tenant_id", tenant_id).eq("ticket", body.ticket).limit(1).execute()
    if not res.data: return {"status": "not_found"}
    t   = res.data[0]
    n   = int(t.get("tp_mod_count") or 0) + 1
    col = f"tp_{min(n, 5)}"
    supabase_admin.table("trades").update({
        col: body.tp_moved,
        "tp_mod_count": n,
    }).eq("id", t["id"]).execute()
    print(f"[TP Move] ticket={body.ticket} → {col}={body.tp_moved} count={n}")
    return {"status": "ok", "column": col}

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
