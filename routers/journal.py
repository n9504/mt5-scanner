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
        .select("id, ai_analysis, entry_analysis, exit_analysis, status, tags")\
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

    if updates:
        supabase_admin.table("trades").update(updates).eq("id", trade_id).execute()

    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if api_key:
        # Run entry analysis when entry screenshots arrive
        if has_entry_ss and not trade.get("entry_analysis"):
            background_tasks.add_task(run_entry_analysis, trade_id, tenant_id)
        # Run exit analysis when exit screenshots arrive
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


def run_entry_analysis(trade_id: str, tenant_id: str):
    """
    Runs when entry screenshots arrive.
    Analyses: setup quality, setup tags, TP/SL probability, entry score.
    Also adds session tag automatically.
    Does NOT tag emotion from outcome.
    """
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

    content.append({"type": "text", "text": f"""
You are a professional trading analyst reviewing the ENTRY of a {symbol} {bias} trade.

Trade details:
- Entry: {entry}
- Stop Loss: {sl}
- Take Profit: {tp}
- Risk/Reward: {rr}R

Analyse the M15 and H1 charts and return ONLY valid JSON:
{{
  "setup_tags": [],
  "entry_score": 0,
  "tp_probability": 0,
  "sl_probability": 0,
  "entry_reasoning": "",
  "key_level": "",
  "watch_for": "",
  "entry_quality": ""
}}

Rules:
- setup_tags: ONLY from this list, ONLY what you can actually see in the charts (max 3):
  ["FVG", "OB", "BOS", "CHoCH", "Support", "Resistance", "Breakout", "Range", "Trend", "Reversal"]
- entry_score: 1-10 quality of this entry based on confluence, structure, timing
- tp_probability: 0-100 percentage chance of TP being hit based on chart structure
- sl_probability: 0-100 percentage chance of SL being hit (tp + sl probabilities should add to ~100)
- entry_reasoning: 1-2 sentences describing WHY this entry makes sense structurally
- key_level: the most important price level to watch right now
- watch_for: what price action would confirm or invalidate this trade
- entry_quality: one of "Excellent", "Good", "Average", "Poor"
- DO NOT tag emotions at entry - only behaviour can determine that
- JSON only, no markdown
"""})

    try:
        client = ant.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=600,
            messages=[{"role": "user", "content": content}]
        )
        text   = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(text)

        setup_tags = result.get("setup_tags", [])

        # Auto session tag from open_time
        session_tag = _get_session_tag(trade.get("open_time", ""))
        all_tags    = setup_tags + ([session_tag] if session_tag else [])

        # Merge with existing tags
        existing = trade.get("tags") or []
        merged   = list(set(existing + all_tags))

        # Calculate simulated balances from all open trades
        sim_tp_pnl = 0.0; sim_sl_pnl = 0.0
        try:
            open_res = supabase_admin.table("trades").select(
                "entry_price,sl,tp,lot,bias,symbol"
            ).eq("tenant_id", tenant_id).eq("status","OPEN").execute()
            open_trades = open_res.data or []
            for ot in open_trades:
                e = float(ot.get("entry_price") or 0)
                s = float(ot.get("sl") or 0)
                t_price = float(ot.get("tp") or 0)
                lot_size = float(ot.get("lot") or 0)
                b = ot.get("bias","BUY")
                sym = ot.get("symbol","")
                # Pip value approximation
                pip_val = 10 if "JPY" not in sym and sym not in ["XAUUSD","BTCUSD","ETHUSD","US30","NAS100","GER40"] else 1
                if t_price and s and e and lot_size:
                    if b == "BUY":
                        sim_tp_pnl += (t_price - e) * lot_size * pip_val * 10000 if "JPY" not in sym else (t_price - e) * lot_size * pip_val * 100
                        sim_sl_pnl += (s - e) * lot_size * pip_val * 10000 if "JPY" not in sym else (s - e) * lot_size * pip_val * 100
                    else:
                        sim_tp_pnl += (e - t_price) * lot_size * pip_val * 10000 if "JPY" not in sym else (e - t_price) * lot_size * pip_val * 100
                        sim_sl_pnl += (e - s) * lot_size * pip_val * 10000 if "JPY" not in sym else (e - s) * lot_size * pip_val * 100
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

        print(f"[Entry Analysis] {symbol} {bias} score={result.get('entry_score')} tp={result.get('tp_probability')}%")

    except Exception as e:
        print(f"[Entry Analysis] Error: {e}")


def run_exit_analysis(trade_id: str, tenant_id: str):
    """
    Runs when exit screenshots arrive.
    Analyses: exit quality, emotion tags (based on behaviour not outcome),
    what happened after, lessons.
    """
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

    content.append({"type": "text", "text": f"""
You are a trading coach reviewing the COMPLETE trade - entry and exit.

{symbol} {bias} trade:
- Entry: {entry}, Close: {close}, SL: {sl}, TP: {tp}
- Outcome: {outcome}, P&L: {pnl}, RR achieved: {rr}
{post_exit_context}

Analyse entry and exit charts carefully. Return ONLY valid JSON:
{{
  "emotion_tags": [],
  "exit_reasoning": "",
  "what_went_right": "",
  "what_went_wrong": "",
  "lesson": "",
  "overall_analysis": "",
  "exit_score": 0
}}

Emotion tag rules - ONLY based on observable behaviour, NOT outcome:
- "Disciplined": price moved very little against position before moving in favour, lot size was normal, held to plan
- "Patient": entry was at a clear structural level, not mid-move
- "FOMO": entry was away from structure, price was already moving significantly before entry
- "Hesitated": obvious entry signal visible but entry was late (price moved significantly before entry)
- "Overconfident": lot size appears unusually large relative to the setup quality
- Only include tags you can genuinely observe from the charts (max 2)

Other fields:
- exit_reasoning: why was the trade closed here? at structure? hit TP/SL? manual?
- what_went_right: specific observation from charts
- what_went_wrong: specific observation or "Nothing significant"
- lesson: single actionable lesson from this trade
- overall_analysis: 2-3 sentences combining entry and exit quality
- exit_score: 1-10 how good was the exit timing
- JSON only, no markdown
"""})

    try:
        client = ant.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=800,
            messages=[{"role": "user", "content": content}]
        )
        text   = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        result = json.loads(text)

        emotion_tags = result.get("emotion_tags", [])

        # Rule-based behaviour tags from outcome (override AI emotion guesses)
        behaviour_tag = _get_behaviour_tag(trade)
        if behaviour_tag and behaviour_tag not in emotion_tags:
            emotion_tags.append(behaviour_tag)

        # Add result tag
        result_tag = _get_result_tag(outcome)
        all_new_tags = emotion_tags + ([result_tag] if result_tag else [])

        # Merge with existing tags
        existing = trade.get("tags") or []
        merged   = list(set(existing + all_new_tags))

        supabase_admin.table("trades").update({
            "tags":          merged,
            "exit_analysis": json.dumps(result),
            "ai_analysis":   result.get("overall_analysis", ""),
        }).eq("id", trade_id).execute()

        print(f"[Exit Analysis] {symbol} {bias} emotion={emotion_tags} exit_score={result.get('exit_score')}")

    except Exception as e:
        print(f"[Exit Analysis] Error: {e}")


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
    Rule-based behaviour detection from trade outcome.
    Calm      = hit TP exactly as planned
    Conservative = closed 60-90% of way to TP (trail/manual early exit)
    Fear      = closed < 50% of way to TP on a winning trade
    Disciplined = followed plan, closed at SL as planned
    """
    outcome    = str(trade.get("execution_outcome","")).upper()
    entry      = float(trade.get("entry_price") or 0)
    close_price= float(trade.get("close_price") or 0)
    tp         = float(trade.get("tp") or 0)
    sl         = float(trade.get("sl") or 0)
    bias       = str(trade.get("bias","BUY"))

    if not entry or not close_price: return ""

    if "WIN_TP" in outcome: return "Calm"
    if "LOSS_SL" in outcome: return "Disciplined"

    # Manual/trail close — determine how far toward TP
    if tp and sl and entry:
        total_dist = abs(tp - entry)
        if total_dist > 0:
            if bias == "BUY":
                achieved = close_price - entry
            else:
                achieved = entry - close_price
            pct = achieved / total_dist * 100 if total_dist else 0

            if pct >= 90:   return "Calm"
            if pct >= 60:   return "Conservative"
            if pct >= 0:    return "Fear"
            if pct < 0:     return "Disciplined"  # closed at loss

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
