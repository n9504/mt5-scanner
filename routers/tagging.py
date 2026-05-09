"""
routers/tagging.py
Scenario-based behaviour tag engine.
Called after trade closes to apply behaviour tags based on:
- SL/TP movements during trade
- Outcome vs plan
- Post-exit 60min price movement
- Day context (consecutive losses, daily target progress)
"""
from fastapi import APIRouter, Depends, BackgroundTasks
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin
from datetime import datetime, date
from typing import Optional

router = APIRouter(prefix="/api/v1/tagging", tags=["tagging"])


def _get_day_context(tenant_id: str, account_id: str, trade_close_time: str) -> dict:
    """Get context about the trading day at time of trade close."""
    try:
        close_dt = datetime.fromisoformat(str(trade_close_time).replace("Z","").replace("+00:00",""))
        day_str   = close_dt.strftime("%Y-%m-%d")

        # Get all trades that closed before this trade today
        res = supabase_admin.table("trades")\
            .select("net_pnl,execution_outcome,close_time")\
            .eq("tenant_id", tenant_id)\
            .eq("account_id", account_id)\
            .eq("status", "CLOSED")\
            .gte("close_time", f"{day_str}T00:00:00")\
            .lt("close_time", trade_close_time)\
            .order("close_time").execute()
        trades = res.data or []

        # Account setup for daily target
        acc_res = supabase_admin.table("accounts")\
            .select("daily_profit_target,daily_loss_cap,account_type")\
            .eq("id", account_id).limit(1).execute()
        acc = (acc_res.data or [{}])[0]
        daily_target = float(acc.get("daily_profit_target") or 0)

        day_pnl = sum(float(t.get("net_pnl",0) or 0) for t in trades)
        consecutive_losses = 0
        for t in reversed(trades):
            if (t.get("execution_outcome","")).startswith("LOSS"):
                consecutive_losses += 1
            else:
                break

        return {
            "day_pnl":            day_pnl,
            "day_trades":         len(trades),
            "consecutive_losses": consecutive_losses,
            "daily_target":       daily_target,
            "target_achieved":    daily_target > 0 and day_pnl >= daily_target,
            "in_red":             day_pnl < 0,
        }
    except Exception as e:
        print(f"[Tagging] Day context error: {e}")
        return {"day_pnl":0,"day_trades":0,"consecutive_losses":0,
                "daily_target":0,"target_achieved":False,"in_red":False}


def apply_scenario_tags(trade: dict, tenant_id: str) -> list:
    """
    Apply behaviour tags based on scenario table.
    Returns list of tag strings.
    """
    tags     = list(trade.get("tags") or [])
    outcome  = str(trade.get("execution_outcome","")).upper()
    is_win   = outcome.startswith("WIN")
    is_loss  = outcome.startswith("LOSS")
    is_manual= "MANUAL" in outcome or (
        not outcome.startswith("WIN_TP") and not outcome.startswith("LOSS_SL")
    )

    entry      = float(trade.get("entry_price") or 0)
    close_p    = float(trade.get("close_price") or 0)
    sl         = float(trade.get("sl") or 0)
    tp         = float(trade.get("tp") or 0)
    bias       = str(trade.get("bias","BUY"))
    post_high  = float(trade.get("post_exit_high") or 0)
    post_low   = float(trade.get("post_exit_low") or 0)

    # SL/TP movement events stored in alerts table
    sl_events = []
    tp_events = []
    try:
        ticket = trade.get("ticket")
        if ticket:
            evts = supabase_admin.table("alerts")\
                .select("type,data")\
                .eq("tenant_id", tenant_id)\
                .in_("type", ["SL_MOVED","TP_MOVED"])\
                .execute()
            for e in (evts.data or []):
                d = e.get("data") or {}
                if str(d.get("ticket","")) == str(ticket):
                    if e["type"] == "SL_MOVED": sl_events.append(d)
                    else:                        tp_events.append(d)
    except Exception: pass

    sl_moved  = len(sl_events) > 0
    tp_moved  = len(tp_events) > 0
    sl_dir    = sl_events[0].get("direction","") if sl_events else ""
    tp_dir    = tp_events[0].get("direction","") if tp_events else ""

    # Post-exit analysis
    post_favourable = False
    post_unfavourable = False
    if post_high and post_low and close_p:
        if bias == "BUY":
            post_favourable   = post_high > close_p + (close_p - post_low) * 0.5
            post_unfavourable = post_low  < close_p
        else:
            post_favourable   = post_low  < close_p - (post_high - close_p) * 0.5
            post_unfavourable = post_high > close_p

    # Day context
    ctx = _get_day_context(tenant_id, trade.get("account_id",""),
                           trade.get("close_time",""))

    new_tags = []

    # ── GROUP A: No SL/TP changes ──
    if not sl_moved and not tp_moved:
        # A1-A4: All disciplined execution
        new_tags.append("Disciplined")

    # ── GROUP B: SL moved ──
    elif sl_moved and not tp_moved:
        if sl_dir == "tightened":
            # B1, B2
            new_tags.extend(["Analysis Incomplete", "Trade to Avoid"])
        elif sl_dir == "widened":
            if is_win:
                # B4
                new_tags.extend(["Lucky", "Risky"])
            else:
                # B3
                if ctx["target_achieved"]:
                    new_tags.append("Greed")
                elif ctx["in_red"] and ctx["consecutive_losses"] >= 1:
                    new_tags.append("Fear")
                else:
                    new_tags.append("Undisciplined")
        elif sl_dir == "removed":
            if is_win:
                new_tags.extend(["Reckless", "Got Lucky"])
            else:
                new_tags.extend(["Panic", "Undisciplined"])

    # ── GROUP C: TP moved ──
    elif tp_moved and not sl_moved:
        if tp_dir == "reduced":
            if is_win and post_favourable:
                # C1 - price continued, reduced TP cost money
                new_tags.append("Strategic" if ctx["target_achieved"] else "Impatient")
            elif is_win and not post_favourable:
                # C2 - smart exit
                new_tags.append("Smart")
            else:
                new_tags.append("Impatient")
        elif tp_dir == "extended":
            if is_win:
                # C3
                new_tags.append("Patient")
            else:
                # C4
                new_tags.extend(["Greedy", "Punished"])
        elif tp_dir == "removed":
            if is_win and post_favourable:
                # C5
                new_tags.extend(["Greedy", "Lucky"])
            else:
                # C6
                new_tags.extend(["Greedy", "Undisciplined"])

    # ── GROUP D: Manual exit context ──
    if is_manual:
        if ctx["consecutive_losses"] >= 2 and is_win:
            new_tags.append("Fear")  # D1
        elif ctx["target_achieved"] and is_win:
            new_tags.append("Strategic")  # D2
        elif not ctx["target_achieved"] and is_win:
            if post_favourable:
                new_tags.append("Impatient")  # D3
            else:
                new_tags.append("Smart Exit")  # D4
        elif is_loss:
            if post_favourable:
                new_tags.append("Panic Exit")  # D5
            else:
                new_tags.append("Damage Control")  # D6

    # ── GROUP E: Day-level context ──
    acc_res = supabase_admin.table("accounts")\
        .select("daily_profit_target")\
        .eq("id", trade.get("account_id","")).limit(1).execute()
    daily_target = float((acc_res.data or [{}])[0].get("daily_profit_target") or 0)
    if daily_target > 0 and ctx["day_pnl"] > daily_target and is_win:
        new_tags.append("Overtrading")  # E4 - trading after hitting target

    # Remove duplicates and merge with existing tags
    for t in new_tags:
        if t not in tags:
            tags.append(t)

    return tags


def run_tagging(trade_id: str, tenant_id: str):
    """Background task to apply scenario tags to a closed trade."""
    try:
        res = supabase_admin.table("trades").select("*")\
            .eq("id", trade_id).eq("tenant_id", tenant_id)\
            .limit(1).execute()
        if not res.data: return
        trade = res.data[0]

        if trade.get("status") != "CLOSED": return

        new_tags = apply_scenario_tags(trade, tenant_id)
        supabase_admin.table("trades").update({"tags": new_tags})\
            .eq("id", trade_id).execute()
        print(f"[Tagging] {trade.get('symbol')} → {new_tags}")
    except Exception as e:
        print(f"[Tagging] Error: {e}")


@router.post("/trade/{trade_id}")
async def tag_trade(
    trade_id:         str,
    background_tasks: BackgroundTasks,
    tenant_id:        str = Depends(get_current_tenant)
):
    background_tasks.add_task(run_tagging, trade_id, tenant_id)
    return {"status": "tagging_started"}
