from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin
import os, json

router = APIRouter(prefix="/api/v1/trades", tags=["journal"])

class JournalUpdate(BaseModel):
    notes: Optional[str]       = None
    tags:  Optional[List[str]] = None

class ScreenshotSync(BaseModel):
    ticket:              int
    screenshot_entry:    Optional[str] = None
    screenshot_exit:     Optional[str] = None
    screenshot_h1_entry: Optional[str] = None
    screenshot_h1_exit:  Optional[str] = None
    timeframe:           Optional[str] = None

@router.put("/{trade_id}/journal")
async def update_journal(
    trade_id:  str,
    body:      JournalUpdate,
    tenant_id: str = Depends(get_current_tenant)
):
    updates = {}
    if body.notes is not None: updates["notes"] = body.notes
    if body.tags  is not None: updates["tags"]  = body.tags
    if not updates:
        return {"status": "no changes"}
    res = supabase_admin.table("trades")\
        .update(updates).eq("id", trade_id).eq("tenant_id", tenant_id).execute()
    if not res.data:
        raise HTTPException(404, "Trade not found")
    return {"status": "updated"}

@router.post("/screenshots")
async def upload_screenshots(
    body:             ScreenshotSync,
    background_tasks: BackgroundTasks,
    tenant_id:        str = Depends(get_tenant_by_api_key)
):
    res = supabase_admin.table("trades")\
        .select("id, ai_analysis, entry_analysis, exit_analysis, status, tags, entry_tags, exit_tags, open_time, close_time, sl, tp, entry_price, bias, margin_level, ticket, account_id")\
        .eq("tenant_id", tenant_id)\
        .eq("ticket", body.ticket)\
        .limit(1).execute()

    if not res.data:
        return {"status": "trade_not_found", "ticket": body.ticket}

    trade_id = res.data[0]["id"]
    trade    = res.data[0]
    updates  = {}

    has_entry_ss = bool(body.screenshot_entry or body.screenshot_h1_entry)
    has_exit_ss  = bool(body.screenshot_exit  or body.screenshot_h1_exit)

    if body.screenshot_entry:    updates["screenshot_entry"]    = body.screenshot_entry
    if body.screenshot_exit:     updates["screenshot_exit"]     = body.screenshot_exit
    if body.screenshot_h1_entry: updates["screenshot_h1_entry"] = body.screenshot_h1_entry
    if body.screenshot_h1_exit:  updates["screenshot_h1_exit"]  = body.screenshot_h1_exit

    # ── SYSTEM TAGS FIRST — no AI needed ──
    existing_tags       = list(trade.get("tags")       or [])
    existing_entry_tags = list(trade.get("entry_tags") or [])
    existing_exit_tags  = list(trade.get("exit_tags")  or [])

    if has_entry_ss and not existing_entry_tags:
        # Entry tags: session at open + plan quality
        entry_session = _get_session_tag(trade.get("open_time",""))
        plan_tag      = _get_plan_quality_tag(
            float(trade.get("sl") or 0),
            float(trade.get("tp") or 0),
            float(trade.get("entry_price") or 0),
            trade.get("bias","BUY")
        )
        risk_tag = _get_risk_tag(float(trade.get("margin_level") or 0))

        new_entry_tags = []
        if entry_session: new_entry_tags.append(entry_session)
        if plan_tag:      new_entry_tags.append(plan_tag)

        updates["entry_tags"] = new_entry_tags

        # Risk tag stored in flat tags
        if risk_tag and risk_tag not in existing_tags:
            existing_tags.append(risk_tag)
            updates["tags"] = existing_tags

        print(f"[System Tags] Entry: {new_entry_tags} Risk: {risk_tag}")

    if has_exit_ss and not existing_exit_tags:
        # Exit tags: session at close + behaviour (computed after trade data available)
        exit_session  = _get_session_tag(trade.get("close_time","") or trade.get("open_time",""))
        behaviour_tag = _get_behaviour_tag(trade)
        result_tag    = _get_result_tag(str(trade.get("execution_outcome","")))

        new_exit_tags = []
        if exit_session:  new_exit_tags.append(exit_session)
        if behaviour_tag: new_exit_tags.append(behaviour_tag)
        if result_tag:    new_exit_tags.append(result_tag)

        updates["exit_tags"] = new_exit_tags
        # Merge into flat tags too
        for t in new_exit_tags:
            if t not in existing_tags:
                existing_tags.append(t)
        updates["tags"] = existing_tags

        print(f"[System Tags] Exit: {new_exit_tags}")

    if updates:
        supabase_admin.table("trades").update(updates).eq("id", trade_id).execute()

    # ── AI ANALYSIS AFTER SYSTEM TAGS ──
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        if has_entry_ss and not trade.get("entry_analysis"):
            background_tasks.add_task(run_entry_analysis, trade_id, tenant_id)
        if has_exit_ss and not trade.get("exit_analysis"):
            background_tasks.add_task(run_exit_analysis, trade_id, tenant_id)

    return {"status": "ok", "trade_id": trade_id}


def _get_images(trade: dict, fields: list) -> list:
    """Extract base64 images from trade for given fields."""
    images = []
    labels = {
        "screenshot_entry":    "M15 Entry",
        "screenshot_h1_entry": "H1 Entry (structure)",
        "screenshot_exit":     "M15 Exit",
        "screenshot_h1_exit":  "H1 Exit (structure)",
    }
    for field in fields:
        img = trade.get(field)
        if img:
            data = img.split(",")[-1] if "," in img else img
            images.append({"data": data, "label": labels.get(field, field)})
    return images


# Daily AI analysis limits per tier
TIER_AI_LIMITS = {
    "journal":  0,
    "starter":  10,
    "growth":   25,
    "pro":      50,
    "elite":    100,
    "beta":     5,
    "free":     0,
}

def _get_daily_ai_count(tenant_id: str) -> tuple:
    # Returns (count_today, tier_limit, subscription)
    from datetime import date, datetime
    today = str(date.today())

    # Get subscription from DB
    sub = "free"
    try:
        t_res = supabase_admin.table("tenants")\
            .select("subscription,is_beta,beta_expires_at")\
            .eq("id", tenant_id).limit(1).execute()
        t   = (t_res.data or [{}])[0]
        sub = t.get("subscription", "free")
        # Override to beta if is_beta=true and not expired
        if t.get("is_beta") and t.get("beta_expires_at"):
            try:
                exp = datetime.fromisoformat(str(t["beta_expires_at"]).replace("Z","").replace("+00:00",""))
                if datetime.utcnow() <= exp:
                    sub = "beta"
            except: pass
    except Exception:
        sub = "free"

    limit = TIER_AI_LIMITS.get(sub, 0)

    # Get today's count
    count = 0
    try:
        count_res = supabase_admin.table("daily_analysis_counts")\
            .select("count")\
            .eq("tenant_id", tenant_id)\
            .eq("analysis_date", today)\
            .limit(1).execute()
        count = int(count_res.data[0]["count"]) if count_res.data else 0
    except Exception as e:
        print(f"[AI COUNT] Read error: {e}")
        count = 0

    print(f"[AI LIMIT] sub={sub} count={count}/{limit}")
    return count, limit, sub

def _increment_daily_ai_count(tenant_id: str, current_count: int):
    from datetime import date
    today = str(date.today())
    new_count = current_count + 1
    try:
        if current_count == 0:
            # First analysis today — insert
            supabase_admin.table("daily_analysis_counts").insert({
                "tenant_id":     tenant_id,
                "analysis_date": today,
                "count":         new_count,
            }).execute()
        else:
            # Already exists — update
            supabase_admin.table("daily_analysis_counts")\
                .update({"count": new_count})\
                .eq("tenant_id", tenant_id)\
                .eq("analysis_date", today).execute()
        print(f"[AI COUNT] count={new_count} for {today}")
    except Exception as e:
        print(f"[AI COUNT] FAILED: {e}")


def run_entry_analysis(trade_id: str, tenant_id: str):
    """
    Runs when entry screenshots arrive.
    Screenshots always stored. AI analysis runs only within daily tier limit.
    """
    # Check daily AI analysis limit first
    count_today, limit, sub = _get_daily_ai_count(tenant_id)
    print(f"[AI LIMIT CHECK] tenant={tenant_id} count={count_today} limit={limit} sub={sub}")
    if limit == 0:
        supabase_admin.table("trades").update({
            "entry_analysis": '{"skipped":true,"reason":"AI analysis not included in current plan. Upgrade to access entry scoring."}'
        }).eq("id", trade_id).execute()
        print(f"[Entry Analysis] Skipped — {sub} plan has no AI analysis")
        return
    if count_today >= limit:
        supabase_admin.table("trades").update({
            "entry_analysis": f'{{"skipped":true,"reason":"Daily AI analysis limit reached ({count_today}/{limit}). Screenshots saved. Resets tomorrow."}}'
        }).eq("id", trade_id).execute()
        print(f"[Entry Analysis] Daily limit reached: {count_today}/{limit}")
        return

    import anthropic as ant
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key: return

    res = supabase_admin.table("trades").select("*")\
        .eq("id", trade_id).limit(1).execute()
    if not res.data: return
    trade = res.data[0]

    images = _get_images(trade, ["screenshot_entry", "screenshot_h1_entry"])
    if not images: return

    symbol = trade.get("symbol", "")
    bias   = trade.get("bias", "")
    entry  = trade.get("entry_price", 0)
    sl     = trade.get("sl", 0)
    tp     = trade.get("tp", 0)
    status = trade.get("status", "OPEN")

    # Calculate RR
    try:
        risk   = abs(float(entry) - float(sl))
        reward = abs(float(tp) - float(entry))
        rr     = round(reward / risk, 2) if risk > 0 else 0
    except Exception:
        rr = 0

    content = []
    for img in images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img["data"]}
        })
        content.append({"type": "text", "text": img["label"]})

    # Get news events near entry time
    news_context = ""
    try:
        from datetime import datetime as dt2, timedelta as td2
        entry_dt = dt2.fromisoformat(str(trade.get("open_time","")).replace("Z","").replace("+00:00",""))
        entry_date = entry_dt.strftime("%Y-%m-%d")
        win_start  = (entry_dt - td2(minutes=15)).strftime("%H:%M")
        win_end    = (entry_dt + td2(minutes=15)).strftime("%H:%M")
        sym_up = symbol.upper()
        currencies = ([sym_up[:3], sym_up[3:6]] if len(sym_up) >= 6 else
                      ["USD"] if any(x in sym_up for x in ["XAU","NAS","US30","GER"]) else [])
        if currencies:
            nr = supabase_admin.table("news_events").select("title,currency,event_time")\
                .eq("event_date", entry_date).eq("impact","High")\
                .in_("currency", currencies).execute()
            nearby = [n for n in (nr.data or []) if n.get("event_time","") and win_start <= n["event_time"] <= win_end]
            if nearby:
                news_context = "HIGH IMPACT NEWS within 15min: " + ", ".join(
                    [f"{n['currency']} {n['title']} at {n['event_time']}" for n in nearby])
    except Exception: pass

    # Get weekly bias alignment
    bias_context = ""
    bias_aligned = True
    try:
        br = supabase_admin.table("scanner_bias").select("bias,condition")\
            .eq("symbol", symbol).eq("scanner","S3")\
            .order("recorded_at", desc=True).limit(1).execute()
        if br.data:
            s3 = br.data[0].get("bias","NEUTRAL")
            bias_aligned = (s3 == bias or s3 == "NEUTRAL")
            bias_context = f"Weekly S3 bias: {s3}. Trade is {'ALIGNED with' if bias_aligned else 'COUNTER to'} weekly trend."
    except Exception: pass

    # Pip calculation
    pip = 0.01 if "JPY" in symbol else (0.1 if "XAU" in symbol else (1.0 if any(x in symbol for x in ["BTC","ETH","US30","NAS","GER"]) else 0.0001))
    sl_pips = round(abs(float(entry)-float(sl))/pip, 1) if sl and entry else "?"
    rr_val  = rr if rr else "?"

    content.append({"type": "text", "text": f"""
You are a professional trading analyst reviewing the ENTRY of a {symbol} {bias} trade.

TRADE DATA:
- Entry: {entry} | SL: {sl} ({sl_pips} pips) | TP: {tp} | RR: {rr_val}R
- Session: {_get_session_tag(trade.get("open_time",""))}
{f"- {bias_context}" if bias_context else ""}
{f"- WARNING: {news_context}" if news_context else ""}

Analyse M15 (entry timing) and H1 (structure) charts carefully. Look specifically for trendlines.

Analyse the chart and return ONLY valid JSON. You are a behavioural analyst — describe what you observe structurally. Do NOT give trading direction or guidance.

{{
  "setup_tags": [],
  "market_condition_tags": [],
  "trendline_touches": 0,
  "trendline_type": "",
  "structural_observation": "",
  "news_risk": false
}}



setup_tags: max 3, ONLY chart structures visible: ["FVG","OB","BOS","CHoCH","Support","Resistance","Pullback","Trendline Touch","Trendline Break"]
market_condition_tags: max 1, overall market state ONLY: ["Trending","Ranging","Breakout","Reversal"]
Do NOT include session, emotion, or behaviour tags — those are computed by the system separately
trendline_touches: count of visible trendline touches (0 if none)
trendline_type: "ascending"/"descending"/"horizontal"/""
structural_observation: describe ONLY what objectively occurred on the chart. Use post-trade observational language.
GOOD: "price moved above prior range", "horizontal level visible", "price expanded above consolidation highs"  
AVOID: "bullish momentum", "buy levels", "bearish signal", "expect", "should", "indicates continuation"
Describe what happened structurally. No predictions. No trading guidance.
key_zone: the most significant price zone visible on the chart
news_risk: true if high impact news within 15min of entry — only flag, system handles tagging
JSON only, no markdown
"""})

    try:
        client = ant.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=700,
            messages=[{"role": "user", "content": content}]
        )
        text   = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(text)

        setup_tags       = result.get("setup_tags", [])
        market_cond_tags = result.get("market_condition_tags", [])

        # Trendline → setup tag
        if result.get("trendline_touches", 0) >= 2:
            tl_type = result.get("trendline_type","")
            if tl_type:
                setup_tags.append(f"{tl_type.capitalize()} Trendline")

        # News risk → system tag
        news_tags = ["News Risk"] if result.get("news_risk") else []

        # Session tag — system computed from time
        session_tag = _get_session_tag(trade.get("open_time", ""))

        # Risk tag — system computed from margin_level
        margin_level = float(trade.get("margin_level") or 0)
        risk_tag = _get_risk_tag(margin_level)

        # Combine all tag categories
        all_tags = (setup_tags + market_cond_tags + news_tags +
                    ([session_tag] if session_tag else []) +
                    ([risk_tag] if risk_tag else []))

        # Merge with existing tags
        existing = trade.get("tags") or []
        merged   = list(set(existing + all_tags))

        # Calculate simulated balances using actual tick_value from broker
        sim_tp_pnl = 0.0; sim_sl_pnl = 0.0
        open_trades = []
        try:
            open_res = supabase_admin.table("trades").select(
                "entry_price,sl,tp,lot,bias,symbol,tick_value,tick_size"
            ).eq("tenant_id", tenant_id).eq("status","OPEN").execute()
            open_trades = open_res.data or []
            for ot in open_trades:
                e          = float(ot.get("entry_price") or 0)
                sl_p       = float(ot.get("sl") or 0)
                tp_p       = float(ot.get("tp") or 0)
                lot        = float(ot.get("lot") or 0)
                bias       = ot.get("bias","BUY")
                tick_val   = float(ot.get("tick_value") or 0)
                tick_sz    = float(ot.get("tick_size") or 0)
                if not (e and sl_p and tp_p and lot): continue
                if not (tick_val and tick_sz): continue  # need tick data from MT5

                # P&L = (price_distance / tick_size) * tick_value * lot
                if bias == "BUY":
                    tp_dist = tp_p - e
                    sl_dist = sl_p - e  # negative for buy
                else:
                    tp_dist = e - tp_p
                    sl_dist = e - sl_p  # positive for sell going down

                tp_pnl = (tp_dist / tick_sz) * tick_val * lot
                sl_pnl = (sl_dist / tick_sz) * tick_val * lot

                sim_tp_pnl += tp_pnl
                sim_sl_pnl += sl_pnl

        except Exception as se:
            print(f"[Sim balance error] {se}")

        # Add simulation to result
        result["sim_tp_pnl"]  = round(sim_tp_pnl, 2)
        result["sim_sl_pnl"]  = round(sim_sl_pnl, 2)
        result["open_count"]  = len(open_trades) if 'open_trades' in dir() else 0

        supabase_admin.table("trades").update({
            "tags":           merged,
            "entry_analysis": json.dumps(result),
        }).eq("id", trade_id).execute()

        # Increment daily count AFTER successful analysis
        _increment_daily_ai_count(tenant_id, count_today)
        print(f"[Entry Analysis] {symbol} {bias} tags={result.get('setup_tags')} trendline={result.get('trendline_touches')}x [{count_today+1}/{limit}]")

    except Exception as e:
        print(f"[Entry Analysis] Error: {e}")


def run_exit_analysis(trade_id: str, tenant_id: str):
    """
    Runs when exit screenshots arrive.
    Structural analysis only — no emotion tags (system computed separately).
    """
    # Check daily AI analysis limit — shared with entry analysis
    count_today, limit, sub = _get_daily_ai_count(tenant_id)
    if limit == 0:
        print(f"[Exit Analysis] Skipped — {sub} plan has no AI analysis")
        return
    if count_today >= limit:
        print(f"[Exit Analysis] Daily limit reached: {count_today}/{limit}")
        return

    import anthropic as ant
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key: return

    res = supabase_admin.table("trades").select("*")\
        .eq("id", trade_id).limit(1).execute()
    if not res.data: return
    trade = res.data[0]

    # Need both entry and exit screenshots for full analysis
    images = _get_images(trade, [
        "screenshot_entry", "screenshot_h1_entry",
        "screenshot_exit",  "screenshot_h1_exit"
    ])
    if not images: return

    symbol  = trade.get("symbol", "")
    bias    = trade.get("bias", "")
    outcome = trade.get("execution_outcome", "")
    pnl     = trade.get("net_pnl", 0)
    rr      = trade.get("rr_actual", 0)
    entry   = trade.get("entry_price", 0)
    close   = trade.get("close_price", 0)
    sl      = trade.get("sl", 0)
    tp      = trade.get("tp", 0)

    post_high  = trade.get("post_exit_high")
    post_low   = trade.get("post_exit_low")
    exit_qual  = trade.get("exit_quality", "")

    content = []
    for img in images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img["data"]}
        })
        content.append({"type": "text", "text": img["label"]})

    post_exit_context = ""
    if post_high and post_low:
        post_exit_context = f"\n60-minute post-exit range: High={post_high}, Low={post_low}, Exit quality flagged as: {exit_qual}"

    content.append({"type": "text", "text": (
        f"You are a trading coach reviewing a {symbol} {bias} trade.\n\n"
        f"TRADE NUMBERS:\n"
        f"Entry: {entry}, Close: {close}, Planned SL: {sl}, Planned TP: {tp}\n"
        f"Outcome: {outcome}, P&L: {pnl}, RR achieved: {rr}\n"
        f"{post_exit_context}\n\n"
        "You have M15 and H1 charts for both entry and exit. Be SPECIFIC with price levels.\n"
        "Return ONLY valid JSON:\n\n"
        "{\n"
        '  \"behaviour_tags\": [],\n'
        '  \"exit_reasoning\": \"\",\n'
        '  \"what_went_right\": \"\",\n'
        '  \"what_went_wrong\": \"\",\n'
        '  \"lesson\": \"\",\n'
        '  \"overall_analysis\": \"\"\n'
        "}\n\n"
        "behaviour_tags (max 2, from charts only, not outcome):\n"
        "- Patient: entry at key H1/M15 structure level\n"
        "- FOMO: entry mid-move away from structure\n"
        "- Hesitated: late entry, signal was visible earlier\n"
        "- Overconfident: no clear structure at entry\n\n"
        f"exit_reasoning: Reference actual prices e.g. closed at {close}, TP was {tp}\n"
        f"what_went_right: Specific observation with price e.g. entry at {entry} aligned with FVG\n"
        "what_went_wrong: Specific observation, or 'Trade followed plan' if WIN_TP\n"
        "lesson: One actionable lesson with price reference\n"
        f"overall_analysis: 2 sentences. 1: entry quality at {entry}. 2: exit at {close} vs plan TP={tp} SL={sl}\n"
        "JSON only, no markdown"
    )})

    try:
        client = ant.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=800,
            messages=[{"role": "user", "content": content}]
        )
        text   = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(text)

        # Exit tags — system computed, no AI
        # 1. Session at close time
        exit_session  = _get_session_tag(trade.get("close_time", "") or trade.get("open_time",""))
        # 2. Behaviour from SL/TP movement history
        behaviour_tag = _get_behaviour_tag(trade)
        # 3. Result tag
        result_tag    = _get_result_tag(outcome)

        exit_tags = []
        if exit_session:  exit_tags.append(exit_session)
        if behaviour_tag: exit_tags.append(behaviour_tag)
        if result_tag:    exit_tags.append(result_tag)

        # Merge with existing flat tags for backward compatibility
        existing_tags = trade.get("tags") or []
        merged_tags   = list(set(existing_tags + exit_tags))

        supabase_admin.table("trades").update({
            "exit_tags":     exit_tags,
            "tags":          merged_tags,
            "exit_analysis": json.dumps(result),
            "ai_analysis":   result.get("overall_analysis", ""),
        }).eq("id", trade_id).execute()

        # Increment daily count
        _increment_daily_ai_count(tenant_id, count_today)
        print(f"[Exit Tags] {symbol} {bias} exit_tags={exit_tags} [{count_today+1}/{limit}]")

    except Exception as e:
        print(f"[Exit Analysis] Error: {e}")


def _get_plan_quality_tag(sl: float, tp: float, entry: float, bias: str) -> str:
    has_sl = sl and sl > 0
    has_tp = tp and tp > 0
    if not has_sl and not has_tp: return "Reckless"
    if has_sl and not has_tp:     return "Forcing Trade"
    if not has_sl and has_tp:     return "Gamble"
    try:
        risk   = abs(float(entry) - float(sl))
        reward = abs(float(tp)    - float(entry))
        rr     = reward / risk if risk > 0 else 0
        return "Clarity" if rr >= 0.7 else "Desperate"
    except: return "Clarity"

def _get_risk_tag(margin_level: float) -> str:
    if margin_level <= 0:    return ""
    if margin_level > 1000:  return "No Risk"
    if margin_level > 700:   return "Balanced Risk"
    if margin_level > 300:   return "Elevated Risk"
    return "Aggressive Risk"

def _get_session_tag(open_time: str) -> str:
    if not open_time: return ""
    try:
        from datetime import datetime
        dt = datetime.fromisoformat(str(open_time).replace("Z","").replace("+00:00",""))
        h = dt.hour
        if 0 <= h < 7:    return "Asia"
        elif 7 <= h < 12: return "London"
        elif 12 <= h < 21: return "US"
    except Exception: pass
    return ""


def _get_behaviour_tag(trade: dict) -> str:
    """
    Full scenario engine — reads SL/TP movement history from alerts table.
    Applies behaviour tags based on what actually happened during the trade.

    Scenario table:
    SL+TP unchanged + WIN_TP          → Disciplined
    SL+TP unchanged + LOSS_SL         → Disciplined
    SL tightened                       → Cautious
    SL widened + WIN                   → Lucky
    SL widened + LOSS + target reached → Greedy
    SL widened + LOSS + in red         → Fear
    SL removed + LOSS                  → Panic
    TP reduced + WIN (target hit)      → Strategic
    TP reduced + WIN (target not hit)  → Impatient
    TP extended + WIN                  → Patient
    TP extended + LOSS                 → Greedy
    Manual exit after 2 losses         → Fear
    WIN_TP no movements                → Calm
    LOSS_SL no movements               → Disciplined
    """
    outcome     = str(trade.get("execution_outcome","")).upper()
    ticket      = trade.get("ticket")
    entry       = float(trade.get("entry_price") or 0)
    close_price = float(trade.get("close_price") or 0)
    orig_tp     = float(trade.get("tp") or 0)
    orig_sl     = float(trade.get("sl") or 0)
    bias        = str(trade.get("bias","BUY"))
    is_win      = "WIN" in outcome
    is_loss     = "LOSS" in outcome

    if not entry or not close_price: return ""

    # Read SL/TP movement history from alerts table
    sl_widened   = False
    sl_tightened = False
    sl_removed   = False
    tp_extended  = False
    tp_reduced   = False
    tp_removed   = False
    movements    = 0

    try:
        alerts_res = supabase_admin.table("alerts")\
            .select("type,data")\
            .eq("ticket", str(ticket))\
            .in_("type", ["SL_MOVED","TP_MOVED"])\
            .execute()

        for alert in all_alerts:
            try:
                data = alert.get("data") or {}
                if isinstance(data, str):
                    import json as _json
                    data = _json.loads(data)
                direction = str(data.get("direction",""))
                atype     = alert.get("type","")
                movements += 1

                if atype == "SL_MOVED":
                    if direction == "widened":   sl_widened   = True
                    if direction == "tightened": sl_tightened = True
                    if direction == "removed":   sl_removed   = True
                elif atype == "TP_MOVED":
                    if direction == "extended":  tp_extended  = True
                    if direction == "reduced":   tp_reduced   = True
                    if direction == "removed":   tp_removed   = True
            except Exception:
                continue
    except Exception as e:
        print(f"[Behaviour] Alert read error: {e}")

    # No movements — clean trade
    if movements == 0:
        if "WIN_TP" in outcome:  return "Calm"
        if "LOSS_SL" in outcome: return "Disciplined"

    # SL scenarios
    if sl_removed and is_loss:   return "Panic"
    if sl_widened and is_win:    return "Lucky"
    if sl_widened and is_loss:
        # Was target ever reached? Check if close near orig_tp
        if orig_tp and abs(close_price - orig_tp) / max(abs(orig_tp - entry), 0.0001) < 0.1:
            return "Greedy"
        return "Fear"
    if sl_tightened:             return "Cautious"

    # TP scenarios
    if tp_extended and is_win:   return "Patient"
    if tp_extended and is_loss:  return "Greedy"
    if tp_reduced  and is_win:
        # Did they hit the reduced target or original?
        return "Strategic"
    if tp_reduced  and is_loss:  return "Impatient"

    # Manual exit scenarios
    if "MANUAL" in outcome or "TRAIL" in outcome:
        if is_win:
            if orig_tp and entry:
                total = abs(orig_tp - entry)
                achieved = abs(close_price - entry)
                pct = achieved / total * 100 if total > 0 else 0
                if pct >= 80: return "Patient"
                if pct >= 50: return "Conservative"
                return "Impatient"
        else:
            return "Fear"

    # Fallback
    if is_win:  return "Calm"
    if is_loss: return "Disciplined"
    return ""


def _get_result_tag(outcome: str) -> str:
    oc = str(outcome).upper()
    if "TP"     in oc: return "TP Hit"
    if "SL"     in oc: return "SL Hit"
    if "TRAIL"  in oc: return "Trail"
    if "MANUAL" in oc: return "Manual Close"
    return ""


@router.post("/{trade_id}/analyse")
async def analyse_trade(
    trade_id:         str,
    background_tasks: BackgroundTasks,
    tenant_id:        str = Depends(get_current_tenant)
):
    """Manual trigger - runs both entry and exit analysis."""
    res = supabase_admin.table("trades").select("id, screenshot_entry, status")\
        .eq("id", trade_id).eq("tenant_id", tenant_id).limit(1).execute()
    if not res.data:
        raise HTTPException(404, "Trade not found")
    if not res.data[0].get("screenshot_entry"):
        raise HTTPException(400, "No entry screenshot available")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(500, "AI not configured")

    background_tasks.add_task(run_entry_analysis, trade_id, tenant_id)
    if res.data[0].get("status") == "CLOSED":
        background_tasks.add_task(run_exit_analysis, trade_id, tenant_id)
    return {"status": "analysis_started"}


# ── Custom tags endpoints ──
class CustomTagBody(BaseModel):
    category: str  # 'setup' or 'market_condition'
    tag_name: str

@router.get("/custom-tags")
async def get_custom_tags(tenant_id: str = Depends(get_current_tenant)):
    res = supabase_admin.table("tenant_custom_tags")\
        .select("category,tag_name")\
        .eq("tenant_id", tenant_id).execute()
    return res.data or []

@router.post("/custom-tags")
async def add_custom_tag(body: CustomTagBody, tenant_id: str = Depends(get_current_tenant)):
    if body.category not in ("setup", "market_condition"):
        raise HTTPException(400, "Invalid category")
    tag = body.tag_name.strip()[:50]
    if not tag:
        raise HTTPException(400, "Tag name required")
    try:
        supabase_admin.table("tenant_custom_tags").insert({
            "tenant_id": tenant_id,
            "category":  body.category,
            "tag_name":  tag,
        }).execute()
    except Exception:
        pass  # Already exists — ignore
    return {"status": "ok", "tag_name": tag}

@router.delete("/custom-tags/{tag_name}")
async def delete_custom_tag(tag_name: str, category: str, tenant_id: str = Depends(get_current_tenant)):
    supabase_admin.table("tenant_custom_tags")\
        .delete()\
        .eq("tenant_id", tenant_id)\
        .eq("category", category)\
        .eq("tag_name", tag_name).execute()
    return {"status": "ok"}
