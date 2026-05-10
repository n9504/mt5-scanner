from fastapi import APIRouter, Depends
from core.auth import get_current_tenant
from core.database import supabase_admin
from datetime import date, datetime, timedelta

router = APIRouter(prefix="/api/v1/daily-plan", tags=["daily_plan"])

def _compute_day_status(planned_trades, planned_profit, planned_max_loss,
                         actual_trades, actual_profit, actual_losses) -> str:
    actual_loss_amt = abs(min(0, actual_profit))
    over_trades     = actual_trades > planned_trades if planned_trades else False
    over_loss       = planned_max_loss and actual_loss_amt > abs(planned_max_loss)
    profitable      = actual_profit > 0

    if over_loss:                                    return "MISSED"
    if over_trades and not profitable:               return "OVERTRADED"
    if actual_trades == 0:                           return "NO_TRADES"
    if not over_trades and profitable:               return "PERFECT"
    if not over_loss and actual_profit >= 0:         return "SUCCESSFUL"
    return "MISSED"

@router.get("/today")
async def get_today_plan(
    account_id: str,
    tenant_id:  str = Depends(get_current_tenant)
):
    today = str(date.today())

    # Get account plan settings
    acc = supabase_admin.table("accounts")\
        .select("daily_profit_target,daily_loss_cap,account_type,prop_daily_max_loss,max_trades_per_day")\
        .eq("id", account_id).limit(1).execute()
    acc_data = (acc.data or [{}])[0]

    planned_profit    = float(acc_data.get("daily_profit_target") or 0)
    planned_max_loss  = float(acc_data.get("daily_loss_cap") or acc_data.get("prop_daily_max_loss") or 0)
    planned_trades    = int(acc_data.get("max_trades_per_day") or 4)

    # Get today's actual trades
    res = supabase_admin.table("trades")\
        .select("net_pnl,execution_outcome,close_time")\
        .eq("tenant_id", tenant_id)\
        .eq("account_id", account_id)\
        .eq("status", "CLOSED")\
        .gte("close_time", f"{today}T00:00:00").execute()
    trades = res.data or []

    actual_trades  = len(trades)
    actual_profit  = sum(float(t.get("net_pnl",0) or 0) for t in trades)
    actual_wins    = sum(1 for t in trades if (t.get("execution_outcome","")).startswith("WIN"))
    actual_losses  = actual_trades - actual_wins
    actual_wr      = round(actual_wins/actual_trades*100) if actual_trades else 0
    planned_wr     = 50  # baseline target

    status = _compute_day_status(
        planned_trades, planned_profit, planned_max_loss,
        actual_trades, actual_profit, actual_losses
    )

    return {
        "date":            today,
        "planned_trades":  planned_trades,
        "planned_profit":  planned_profit,
        "planned_max_loss":planned_max_loss,
        "planned_wr":      planned_wr,
        "actual_trades":   actual_trades,
        "actual_profit":   round(actual_profit, 2),
        "actual_wins":     actual_wins,
        "actual_losses":   actual_losses,
        "actual_wr":       actual_wr,
        "status":          status,
    }

@router.get("/weekly")
async def get_weekly_plan(
    account_id: str,
    tenant_id:  str = Depends(get_current_tenant)
):
    # Get account plan settings
    acc = supabase_admin.table("accounts")\
        .select("daily_profit_target,daily_loss_cap,prop_daily_max_loss,max_trades_per_day")\
        .eq("id", account_id).limit(1).execute()
    acc_data = (acc.data or [{}])[0]

    planned_profit   = float(acc_data.get("daily_profit_target") or 0)
    planned_max_loss = float(acc_data.get("daily_loss_cap") or acc_data.get("prop_daily_max_loss") or 0)
    planned_trades   = int(acc_data.get("max_trades_per_day") or 4)

    # Get this week's trades
    today  = date.today()
    monday = today - timedelta(days=today.weekday())
    res = supabase_admin.table("trades")\
        .select("net_pnl,execution_outcome,close_time")\
        .eq("tenant_id", tenant_id)\
        .eq("account_id", account_id)\
        .eq("status", "CLOSED")\
        .gte("close_time", f"{monday}T00:00:00").execute()
    trades = res.data or []

    # Group by day
    days = {}
    for t in trades:
        if not t.get("close_time"): continue
        d = t["close_time"][:10]
        if d not in days: days[d] = []
        days[d].append(t)

    # Build daily rows Mon-Fri
    weekly_rows = []
    weekly_stats = {"EXCELLENT":0,"PASS":0,"UNPROFITABLE":0,"RISKY":0}
    week_profit = 0

    for i in range(5):  # Mon-Fri
        day_date = monday + timedelta(days=i)
        day_str  = str(day_date)
        day_trades = days.get(day_str, [])

        actual_trades = len(day_trades)
        actual_profit = sum(float(t.get("net_pnl",0) or 0) for t in day_trades)
        actual_wins   = sum(1 for t in day_trades if (t.get("execution_outcome","")).startswith("WIN"))
        actual_losses = actual_trades - actual_wins
        actual_wr     = round(actual_wins/actual_trades*100) if actual_trades else 0
        week_profit  += actual_profit

        if day_date > today:
            status = "FUTURE"
        elif actual_trades == 0:
            status = "NO_TRADES"
        else:
            status = _compute_day_status(
                planned_trades, planned_profit, planned_max_loss,
                actual_trades, actual_profit, actual_losses
            )

        if status in weekly_stats: weekly_stats[status] += 1

        weekly_rows.append({
            "date":            day_str,
            "day":             day_date.strftime("%A"),
            "planned_trades":  planned_trades,
            "planned_profit":  planned_profit,
            "planned_max_loss":planned_max_loss,
            "actual_trades":   actual_trades,
            "actual_profit":   round(actual_profit, 2),
            "actual_wins":     actual_wins,
            "actual_losses":   actual_losses,
            "actual_wr":       actual_wr,
            "status":          status,
        })

    return {
        "week_start":    str(monday),
        "week_profit":   round(week_profit, 2),
        "days":          weekly_rows,
        "summary":       weekly_stats,
        "trading_days":  sum(1 for r in weekly_rows if r["status"] not in ["FUTURE","NO_TRADES"]),
        "excellent_days":weekly_stats["EXCELLENT"],
        "pass_days":     weekly_stats["PASS"],
        "fail_days":     weekly_stats["UNPROFITABLE"],
        "risky_days":    weekly_stats["RISKY"],
    }
