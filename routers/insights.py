from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from datetime import datetime, timedelta, date
from core.auth import get_current_tenant
from core.database import supabase_admin
import os, json

router = APIRouter(prefix="/api/v1/insights", tags=["insights"])
ADMIN_EMAIL = "pnara9504@gmail.com"

def _is_admin(tenant_id: str) -> bool:
    res = supabase_admin.table("tenants").select("email").eq("id", tenant_id).limit(1).execute()
    return res.data and res.data[0].get("email") == ADMIN_EMAIL

def _can_run_insights(tenant_id: str):
    res = supabase_admin.table("tenants").select("email,subscription,analysis_count_week,analysis_week_reset").eq("id", tenant_id).limit(1).execute()
    if not res.data: return False, "Not found"
    t = res.data[0]
    if t.get("email") == ADMIN_EMAIL: return True, ""
    if t.get("subscription","free") == "free": return False, "Upgrade to Pro to access weekly insights"
    reset = t.get("analysis_week_reset"); count = int(t.get("analysis_count_week") or 0)
    if reset:
        try:
            rdt = datetime.fromisoformat(str(reset).replace("Z","").replace("+00:00",""))
            if datetime.utcnow() - rdt > timedelta(days=7):
                supabase_admin.table("tenants").update({"analysis_count_week":0,"analysis_week_reset":datetime.utcnow().isoformat()}).eq("id",tenant_id).execute()
                count = 0
        except Exception: pass
    if count >= 1: return False, "Weekly limit reached. Available again in 7 days."
    return True, ""

def run_insights_analysis(tenant_id: str, account_id: str):
    import anthropic as ant
    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key: return
    cutoff = (datetime.utcnow() - timedelta(days=90)).isoformat()
    res = supabase_admin.table("trades").select("symbol,bias,scanner,net_pnl,rr_actual,execution_outcome,open_time,close_time,session,tags,exit_quality").eq("tenant_id",tenant_id).eq("account_id",account_id).eq("status","CLOSED").gte("close_time",cutoff).order("close_time",desc=True).limit(200).execute()
    trades = res.data or []
    if len(trades) < 5: return

    acc_res = supabase_admin.table("accounts").select("balance").eq("id",account_id).limit(1).execute()
    balance = float((acc_res.data or [{}])[0].get("balance",0) or 0)

    def summarise(tlist):
        if not tlist: return {}
        wins = [t for t in tlist if (t.get("execution_outcome","")).startswith("WIN")]
        pnl  = sum(float(t.get("net_pnl",0) or 0) for t in tlist)
        rrs  = [float(t.get("rr_actual",0) or 0) for t in tlist if t.get("rr_actual")]
        by_session = {}; by_symbol = {}; emotion_counts = {}
        for t in tlist:
            s = t.get("session","unknown") or "unknown"
            if s not in by_session: by_session[s] = {"wins":0,"losses":0,"pnl":0}
            if (t.get("execution_outcome","")).startswith("WIN"): by_session[s]["wins"] += 1
            else: by_session[s]["losses"] += 1
            by_session[s]["pnl"] = round(by_session[s]["pnl"] + float(t.get("net_pnl",0) or 0), 2)
            sym = t.get("symbol","")
            if sym not in by_symbol: by_symbol[sym] = {"wins":0,"losses":0,"pnl":0}
            if (t.get("execution_outcome","")).startswith("WIN"): by_symbol[sym]["wins"] += 1
            else: by_symbol[sym]["losses"] += 1
            by_symbol[sym]["pnl"] = round(by_symbol[sym]["pnl"] + float(t.get("net_pnl",0) or 0), 2)
            for tag in (t.get("tags") or []):
                if tag in ["FOMO","Revenge","Hesitated","Overconfident","Disciplined","Patient"]:
                    emotion_counts[tag] = emotion_counts.get(tag,0) + 1
        return {"count":len(tlist),"wins":len(wins),"losses":len(tlist)-len(wins),"win_rate":round(len(wins)/len(tlist)*100,1),"net_pnl":round(pnl,2),"avg_rr":round(sum(rrs)/len(rrs),2) if rrs else 0,"by_session":by_session,"by_symbol":by_symbol,"emotion_tags":emotion_counts}

    recent_summary = summarise(trades[:50])
    older_summary  = summarise(trades[50:])
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
    except Exception: pass

    prompt = f"""You are an expert trading coach reviewing a trader's journal.
BALANCE: ${balance:,.2f}
RECENT 50 TRADES: {json.dumps(recent_summary)}
OLDER TRADES: {json.dumps(older_summary)}
PROJECTED YEAR-END: ${projected or 0:,.2f}

Return ONLY valid JSON:
{{"headline":"one sentence summary","overall_assessment":"2-3 sentences, be direct","strengths":["2-3 genuine strengths with evidence"],"weaknesses":["2-3 areas to improve"],"patterns":[{{"pattern":"","description":"","impact":"positive/negative"}}],"behaviour_flags":[{{"flag":"","evidence":"","recommendation":""}}],"best_setup":"best performing setup/session/symbol","worst_setup":"worst performing","year_end_projection":{projected or 0},"year_end_narrative":"2 sentences on projection","top_recommendation":"single most important change","encouragement":"genuine specific positive message"}}
Rules: be specific, reference actual numbers, JSON only no markdown"""

    try:
        client = ant.Anthropic(api_key=api_key)
        resp = client.messages.create(model="claude-sonnet-4-5", max_tokens=2000, messages=[{"role":"user","content":prompt}])
        text = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(text)
        supabase_admin.table("insights").insert({"tenant_id":tenant_id,"account_id":account_id,"analysis":json.dumps(result),"year_end_pnl":projected,"trade_count":len(trades)}).execute()
        supabase_admin.table("tenants").update({"analysis_count_week":1,"analysis_week_reset":datetime.utcnow().isoformat()}).eq("id",tenant_id).execute()
        print(f"[Insights] Done for {tenant_id}")
    except Exception as e:
        print(f"[Insights] Error: {e}")

@router.post("/run")
async def run_insights(account_id: str, background_tasks: BackgroundTasks, tenant_id: str = Depends(get_current_tenant)):
    can_run, reason = _can_run_insights(tenant_id)
    if not can_run: raise HTTPException(403, reason)
    background_tasks.add_task(run_insights_analysis, tenant_id, account_id)
    return {"status":"running","message":"Analysis started — check back in 30 seconds"}

@router.get("/latest")
async def get_latest_insights(account_id: Optional[str] = None, tenant_id: str = Depends(get_current_tenant)):
    query = supabase_admin.table("insights").select("*").eq("tenant_id",tenant_id).order("generated_at",desc=True).limit(1)
    if account_id: query = query.eq("account_id",account_id)
    res = query.execute()
    if not res.data: return None
    row = res.data[0]
    try: row["analysis"] = json.loads(row["analysis"])
    except Exception: pass
    return row

@router.get("/history")
async def get_insights_history(tenant_id: str = Depends(get_current_tenant)):
    res = supabase_admin.table("insights").select("id,generated_at,trade_count,year_end_pnl").eq("tenant_id",tenant_id).order("generated_at",desc=True).limit(10).execute()
    return res.data or []
