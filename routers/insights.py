from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from typing import Optional
from datetime import datetime, timedelta, date
from core.auth import get_current_tenant
from core.database import supabase_admin
import os, json, math

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])
ADMIN_EMAIL = "pnara9504@gmail.com"

def _is_admin(tenant_id: str) -> bool:
    res = supabase_admin.table("tenants").select("email").eq("id", tenant_id).limit(1).execute()
    return res.data and res.data[0].get("email") == ADMIN_EMAIL

def _can_run(tenant_id: str):
    res = supabase_admin.table("tenants").select("email,subscription,analysis_count_week,analysis_week_reset")\
        .eq("id", tenant_id).limit(1).execute()
    if not res.data: return False, "Not found"
    t = res.data[0]
    if t.get("email") == ADMIN_EMAIL: return True, ""
    if t.get("subscription","free") == "free": return False, "Available on Pro plan"
    reset = t.get("analysis_week_reset"); count = int(t.get("analysis_count_week") or 0)
    if reset:
        try:
            rdt = datetime.fromisoformat(str(reset).replace("Z","").replace("+00:00",""))
            if datetime.utcnow() - rdt > timedelta(days=7):
                supabase_admin.table("tenants").update({"analysis_count_week":0,"analysis_week_reset":datetime.utcnow().isoformat()}).eq("id",tenant_id).execute()
                count = 0
        except: pass
    if count >= 1: return False, "Weekly analysis already run"
    return True, ""

def _compute_behavioural_scores(trades: list) -> dict:
    """Compute behavioural scorecard from trade data."""
    if not trades: return {}
    total = len(trades)
    wins  = [t for t in trades if (t.get("execution_outcome","")).startswith("WIN")]
    
    # Discipline: SL/TP not moved, followed plan
    disciplined = sum(1 for t in trades if "Disciplined" in (t.get("tags") or []))
    calm        = sum(1 for t in trades if "Calm" in (t.get("tags") or []))
    fear        = sum(1 for t in trades if "Fear" in (t.get("tags") or []))
    greed       = sum(1 for t in trades if any(x in (t.get("tags") or []) for x in ["Greed","Greedy"]))
    revenge     = sum(1 for t in trades if "Revenge" in (t.get("tags") or []))
    overtraded  = sum(1 for t in trades if "Overtrading" in (t.get("tags") or []))
    patient     = sum(1 for t in trades if "Patient" in (t.get("tags") or []))
    
    # Scores (0-100)
    discipline_score    = min(100, round((disciplined + calm) / max(total,1) * 100 + (1 - fear/max(total,1)) * 30))
    risk_score          = min(100, round((1 - (greed + revenge)/max(total,1)) * 100))
    patience_score      = min(100, round(patient / max(total,1) * 100 + 50))
    emotional_score     = min(100, round((1 - (fear + greed + revenge)/max(total*3,1)) * 100))
    rule_adherence      = min(100, round((disciplined + calm + patient) / max(total,1) * 100))
    
    # Expectancy
    avg_win  = sum(float(t.get("net_pnl",0) or 0) for t in wins) / max(len(wins),1)
    losses_l = [t for t in trades if not (t.get("execution_outcome","")).startswith("WIN")]
    avg_loss = sum(float(t.get("net_pnl",0) or 0) for t in losses_l) / max(len(losses_l),1)
    wr       = len(wins)/max(total,1)
    lr       = 1 - wr
    expectancy = round(wr * avg_win + lr * avg_loss, 2)
    
    return {
        "discipline":        max(0, discipline_score),
        "risk_consistency":  max(0, risk_score),
        "patience":          max(0, patience_score),
        "emotional_stability":max(0, emotional_score),
        "rule_adherence":    max(0, rule_adherence),
        "expectancy":        expectancy,
        "avg_win":           round(avg_win, 2),
        "avg_loss":          round(avg_loss, 2),
        "total_trades":      total,
    }

def _compute_trading_dna(trades: list) -> dict:
    """Find common characteristics of winning vs losing trades."""
    if len(trades) < 20: return {}
    wins   = [t for t in trades if (t.get("execution_outcome","")).startswith("WIN")]
    losses = [t for t in trades if (t.get("execution_outcome","")).startswith("LOSS")]
    
    def top_patterns(tlist, field, limit=3):
        counts = {}
        for t in tlist:
            v = t.get(field)
            if v: counts[v] = counts.get(v,0) + 1
        total = len(tlist) or 1
        return sorted([{"value":k,"pct":round(v/total*100)} for k,v in counts.items()
                       if v/total > 0.15], key=lambda x:-x["pct"])[:limit]
    
    def top_tags(tlist, limit=3):
        counts = {}
        for t in tlist:
            for tag in (t.get("tags") or []):
                if tag not in ["Calm","Fear","Greed","Disciplined","TP Hit","SL Hit","Trail","Manual Close"]:
                    counts[tag] = counts.get(tag,0) + 1
        total = len(tlist) or 1
        return sorted([{"tag":k,"pct":round(v/total*100)} for k,v in counts.items()
                       if v/total > 0.15], key=lambda x:-x["pct"])[:limit]
    
    return {
        "sample_size": len(trades),
        "confidence":  "High" if len(trades) >= 100 else "Medium" if len(trades) >= 50 else "Low",
        "winning_sessions":  top_patterns(wins, "session"),
        "losing_sessions":   top_patterns(losses, "session"),
        "winning_symbols":   top_patterns(wins, "symbol"),
        "losing_symbols":    top_patterns(losses, "symbol"),
        "winning_tags":      top_tags(wins),
        "losing_tags":       top_tags(losses),
    }

def _detect_behaviour_drift(recent: list, baseline: list) -> dict:
    """Compare recent 15 trades to historical baseline."""
    if len(baseline) < 20: return {}
    
    def avg_lot(tlist):
        lots = [float(t.get("lot",0) or 0) for t in tlist if t.get("lot")]
        return sum(lots)/len(lots) if lots else 0
    
    def emotional_rate(tlist):
        emotional_tags = ["Fear","Greed","Revenge","Panic","FOMO"]
        tagged = sum(1 for t in tlist if any(x in (t.get("tags") or []) for x in emotional_tags))
        return tagged/max(len(tlist),1)
    
    recent_lot       = avg_lot(recent)
    baseline_lot     = avg_lot(baseline)
    recent_emotional = emotional_rate(recent)
    baseline_emotional = emotional_rate(baseline)
    
    lot_change      = round((recent_lot/max(baseline_lot,0.001) - 1) * 100)
    emotional_change= round((recent_emotional - baseline_emotional) * 100)
    
    drifts = []
    if lot_change > 25:
        drifts.append(f"Position sizing increased {lot_change}% vs historical baseline")
    if lot_change < -25:
        drifts.append(f"Position sizing decreased {abs(lot_change)}% vs historical baseline")
    if emotional_change > 15:
        drifts.append(f"Emotional execution up {emotional_change}% in recent trades vs baseline")
    if emotional_change < -15:
        drifts.append(f"Emotional execution improved — down {abs(emotional_change)}% vs baseline")
    
    return {
        "drifts":            drifts,
        "lot_change_pct":    lot_change,
        "emotional_change":  emotional_change,
        "recent_count":      len(recent),
        "baseline_count":    len(baseline),
    }

def _personality_profile(trades: list) -> str:
    """Determine trader personality from 100+ trades."""
    if len(trades) < 50: return ""
    wins   = [t for t in trades if (t.get("execution_outcome","")).startswith("WIN")]
    losses = [t for t in trades if (t.get("execution_outcome","")).startswith("LOSS")]
    wr     = len(wins)/max(len(trades),1)
    
    # Avg duration
    durations = []
    for t in trades:
        if t.get("open_time") and t.get("close_time"):
            try:
                o = datetime.fromisoformat(str(t["open_time"]).replace("Z","").replace("+00:00",""))
                c = datetime.fromisoformat(str(t["close_time"]).replace("Z","").replace("+00:00",""))
                durations.append((c-o).seconds/60)
            except: pass
    avg_dur = sum(durations)/len(durations) if durations else 60
    
    tags_all = [tag for t in trades for tag in (t.get("tags") or [])]
    
    # Profile logic
    if avg_dur < 15 and len(trades) > 50:      return "Aggressive Scalper"
    if wr > 0.65 and avg_dur > 120:            return "Conservative Executor"
    if tags_all.count("Revenge") > len(trades)*0.2: return "Emotional Recovery Trader"
    if tags_all.count("Patient") > len(trades)*0.25: return "Structured Breakout Trader"
    if "Trend Continuation" in tags_all and tags_all.count("Trend Continuation") > len(trades)*0.3:
        return "Trend Follower"
    return "Momentum Trader"

def run_insights_analysis(tenant_id: str, account_id: str):
    import anthropic as ant
    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key: return

    res = supabase_admin.table("trades").select(
        "symbol,bias,scanner,net_pnl,gross_pnl,commission,rr_actual,execution_outcome,"
        "open_time,close_time,session,tags,exit_quality,lot,entry_score"
    ).eq("tenant_id",tenant_id).eq("account_id",account_id)\
     .eq("status","CLOSED").order("close_time",desc=True).limit(300).execute()
    trades = res.data or []
    if len(trades) < 5: return

    acc_res = supabase_admin.table("accounts").select("balance,currency")\
        .eq("id",account_id).limit(1).execute()
    account  = (acc_res.data or [{}])[0]
    balance  = float(account.get("balance",0) or 0)
    currency = account.get("currency","USD")

    recent_50  = trades[:50]
    older      = trades[50:]
    recent_15  = trades[:15]

    # Compute all metrics
    scores   = _compute_behavioural_scores(trades)
    dna      = _compute_trading_dna(trades)
    drift    = _detect_behaviour_drift(recent_15, trades[15:])
    profile  = _personality_profile(trades)

    # Weekly projection
    projected = None
    try:
        if len(trades) >= 2:
            t1 = datetime.fromisoformat(str(trades[-1].get("close_time","")).replace("Z","").replace("+00:00",""))
            t2 = datetime.fromisoformat(str(trades[0].get("close_time","")).replace("Z","").replace("+00:00",""))
            days = max(1,(t2-t1).days)
            total_pnl = sum(float(t.get("net_pnl",0) or 0) for t in trades)
            weekly_avg = (total_pnl/days)*7
            weeks_left = max(0,(date(datetime.utcnow().year,12,31)-date.today()).days//7)
            projected  = round(total_pnl + weekly_avg*weeks_left, 2)
    except: pass

    # Recent vs older summary
    def summarise(tlist):
        if not tlist: return {}
        wins = [t for t in tlist if (t.get("execution_outcome","")).startswith("WIN")]
        pnl  = sum(float(t.get("net_pnl",0) or 0) for t in tlist)
        by_session = {}
        by_symbol  = {}
        for t in tlist:
            s = t.get("session","unknown") or "unknown"
            sym = t.get("symbol","")
            if s not in by_session: by_session[s] = {"wins":0,"losses":0,"pnl":0}
            if sym not in by_symbol: by_symbol[sym] = {"wins":0,"losses":0,"pnl":0}
            won = (t.get("execution_outcome","")).startswith("WIN")
            by_session[s]["wins" if won else "losses"] += 1
            by_session[s]["pnl"] += float(t.get("net_pnl",0) or 0)
            by_symbol[sym]["wins" if won else "losses"] += 1
            by_symbol[sym]["pnl"] += float(t.get("net_pnl",0) or 0)
        return {
            "count":len(tlist),"wins":len(wins),
            "win_rate":round(len(wins)/len(tlist)*100,1),
            "net_pnl":round(pnl,2),
            "by_session":by_session,"by_symbol":by_symbol,
            "expectancy":round(scores.get("expectancy",0),2),
        }

    prompt = f"""You are a trading performance analyst writing a behavioural intelligence report.
You analyse trader behaviour — NOT market conditions or trade recommendations.
NEVER suggest what to trade, buy, sell, or predict market direction.
NEVER give trading guidance. Analyse ONLY the trader's behaviour and execution patterns.

TRADER DATA:
Balance: {currency} {balance:,.2f}
Total trades analysed: {len(trades)}
Trader profile: {profile or "Developing"}

BEHAVIOURAL SCORES:
{json.dumps(scores, indent=2)}

TRADING DNA:
{json.dumps(dna, indent=2)}

BEHAVIOUR DRIFT (recent vs baseline):
{json.dumps(drift, indent=2)}

RECENT 50 TRADES:
{json.dumps(summarise(recent_50), indent=2)}

OLDER TRADES:
{json.dumps(summarise(older), indent=2)}

YEAR-END PROJECTION: {currency} {projected:,.2f} if current pattern continues

Return ONLY valid JSON:
{{
  "narrative": "3-4 sentences written like a personal trading coach. Reference specific numbers. Focus on BEHAVIOUR patterns — execution, psychology, consistency. Sound like: 'Your strongest execution occurs when...' NO market direction.",
  "strengths": ["2-3 specific behavioural strengths with data evidence"],
  "weaknesses": ["2-3 specific behavioural weaknesses with data evidence"],
  "behaviour_contradictions": ["Any gap between assumed vs actual patterns — e.g. you trade X but profit from Y"],
  "what_improved": ["Improvements vs older baseline with specific metrics"],
  "drift_alerts": ["Any concerning recent behaviour shifts"],
  "year_end_projection": {projected or 0},
  "year_end_narrative": "1 sentence about trajectory based on current behaviour pattern",
  "top_focus": "Single most important behavioural pattern to address — phrased as observation not instruction"
}}

Rules:
- All observations are backward-looking behavioural analysis
- Never say 'you should trade X' or predict market movement  
- Reference actual numbers from the data
- Use language: 'observed', 'detected', 'historical tendency', 'behavioural pattern'
- JSON only, no markdown"""

    try:
        client = ant.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=2000,
            messages=[{"role":"user","content":prompt}]
        )
        text   = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(text)

        # Add computed metrics
        result["behavioural_scores"] = scores
        result["trading_dna"]        = dna
        result["behaviour_drift"]    = drift
        result["trader_profile"]     = profile
        result["trade_count"]        = len(trades)

        supabase_admin.table("insights").insert({
            "tenant_id":   tenant_id,
            "account_id":  account_id,
            "analysis":    json.dumps(result),
            "year_end_pnl":projected,
            "trade_count": len(trades),
        }).execute()

        supabase_admin.table("tenants").update({
            "analysis_count_week": 1,
            "analysis_week_reset": datetime.utcnow().isoformat(),
        }).eq("id",tenant_id).execute()

        print(f"[Insights] Complete for {tenant_id}")
    except Exception as e:
        print(f"[Insights] Error: {e}")

@router.post("/run")
async def run_insights(account_id: str, background_tasks: BackgroundTasks,
                       tenant_id: str = Depends(get_current_tenant)):
    can_run, reason = _can_run(tenant_id)
    if not can_run: raise HTTPException(403, reason)
    background_tasks.add_task(run_insights_analysis, tenant_id, account_id)
    return {"status":"running"}

@router.get("/check")
async def check_and_run(account_id: str, background_tasks: BackgroundTasks,
                        tenant_id: str = Depends(get_current_tenant)):
    res = supabase_admin.table("insights").select("generated_at")\
        .eq("tenant_id",tenant_id).order("generated_at",desc=True).limit(1).execute()
    last_run = None
    if res.data:
        try: last_run = datetime.fromisoformat(str(res.data[0]["generated_at"]).replace("Z","").replace("+00:00",""))
        except: pass
    should_run = (last_run is None) or (datetime.utcnow() - last_run > timedelta(days=7))
    can_run, _ = _can_run(tenant_id)
    if should_run and can_run:
        background_tasks.add_task(run_insights_analysis, tenant_id, account_id)
        return {"status":"running"}
    return {"status":"ok","last_run":str(last_run) if last_run else None}

@router.get("/latest")
async def get_latest(account_id: Optional[str] = None,
                     tenant_id: str = Depends(get_current_tenant)):
    query = supabase_admin.table("insights").select("*")\
        .eq("tenant_id",tenant_id).order("generated_at",desc=True).limit(1)
    if account_id: query = query.eq("account_id",account_id)
    res = query.execute()
    if not res.data: return None
    row = res.data[0]
    try: row["analysis"] = json.loads(row["analysis"])
    except: pass
    return row
