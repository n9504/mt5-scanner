from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional, List
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin
import os
import json

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
    res = supabase_admin.table("trades")        .update(updates).eq("id", trade_id).eq("tenant_id", tenant_id).execute()
    if not res.data:
        raise HTTPException(404, "Trade not found")
    return {"status": "updated"}

@router.post("/screenshots")
async def upload_screenshots(
    body:             ScreenshotSync,
    background_tasks: BackgroundTasks,
    tenant_id:        str = Depends(get_tenant_by_api_key)
):
    res = supabase_admin.table("trades")        .select("id, ai_analysis, tags")        .eq("tenant_id", tenant_id)        .eq("ticket", body.ticket)        .limit(1)        .execute()

    if not res.data:
        return {"status": "trade_not_found", "ticket": body.ticket}

    trade_id = res.data[0]["id"]
    updates  = {}

    if body.screenshot_entry:    updates["screenshot_entry"]    = body.screenshot_entry
    if body.screenshot_exit:     updates["screenshot_exit"]     = body.screenshot_exit
    if body.screenshot_h1_entry: updates["screenshot_h1_entry"] = body.screenshot_h1_entry
    if body.screenshot_h1_exit:  updates["screenshot_h1_exit"]  = body.screenshot_h1_exit

    if updates:
        supabase_admin.table("trades").update(updates).eq("id", trade_id).execute()
        api_key = os.environ.get("ANTHROPIC_API_KEY", "")
        already_analysed = bool(res.data[0].get("ai_analysis"))
        if api_key and not already_analysed:
            background_tasks.add_task(run_ai_analysis, trade_id, tenant_id)

    return {"status": "ok", "trade_id": trade_id}

def run_ai_analysis(trade_id: str, tenant_id: str):
    import anthropic as ant
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        return

    res = supabase_admin.table("trades").select("*").eq("id", trade_id).limit(1).execute()
    if not res.data:
        return

    trade = res.data[0]

    images = []
    for field, label in [
        ("screenshot_entry",    "M15 Entry chart"),
        ("screenshot_h1_entry", "H1 Entry chart (structure)"),
        ("screenshot_exit",     "M15 Exit chart"),
        ("screenshot_h1_exit",  "H1 Exit chart (structure)"),
    ]:
        img = trade.get(field)
        if img:
            data = img.split(",")[-1] if "," in img else img
            images.append({"data": data, "label": label})

    if not images:
        return

    content = []
    for img in images:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png", "data": img["data"]}
        })
        content.append({"type": "text", "text": img["label"]})

    symbol  = trade.get("symbol", "")
    bias    = trade.get("bias", "")
    outcome = trade.get("execution_outcome", "unknown")
    pnl     = trade.get("net_pnl", 0)
    rr      = trade.get("rr_actual", 0)

    content.append({"type": "text", "text": f"""
You are a professional trading coach reviewing a {symbol} {bias} trade.
Outcome: {outcome} | Net P&L: {pnl} | RR achieved: {rr}

Return ONLY valid JSON:
{{
  "setup_tags": [],
  "emotion_tags": [],
  "analysis": "",
  "key_observation": "",
  "entry_reasoning": ""
}}

Rules:
- setup_tags: pick FROM ONLY THESE that are visible: FVG, OB, BOS, CHoCH, Support, Resistance, Breakout, Range, Trend, Reversal (max 3)
- emotion_tags: pick FROM ONLY THESE: Disciplined, FOMO, Revenge, Hesitated, Overconfident, Patient (max 2)
- analysis: 2-3 sentences on setup quality, execution, improvement
- key_observation: single most important takeaway
- entry_reasoning: why price was taken at this level
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

        all_tags = result.get("setup_tags", []) + result.get("emotion_tags", [])

        # Auto session tag
        import datetime
        if trade.get("open_time"):
            try:
                ot = datetime.datetime.fromisoformat(
                    str(trade["open_time"]).replace("Z","").replace("+00:00",""))
                h = ot.hour
                if 0 <= h < 7:    all_tags.append("Asia")
                elif 7 <= h < 12: all_tags.append("London")
                elif 12 <= h < 21: all_tags.append("US")
            except Exception:
                pass

        # Auto result tag
        oc = str(outcome).upper()
        if "TP"      in oc: all_tags.append("TP Hit")
        elif "SL"    in oc: all_tags.append("SL Hit")
        elif "TRAIL" in oc: all_tags.append("Trail")
        elif "MANUAL" in oc: all_tags.append("Manual Close")

        existing = trade.get("tags") or []
        merged   = list(set(existing + all_tags))

        supabase_admin.table("trades").update({
            "tags":            merged,
            "ai_analysis":     result.get("analysis", ""),
        }).eq("id", trade_id).execute()

        print(f"AI done: {symbol} {bias} tags={merged}")

    except Exception as e:
        print(f"AI error {trade_id}: {e}")

@router.post("/{trade_id}/analyse")
async def analyse_trade(
    trade_id:         str,
    background_tasks: BackgroundTasks,
    tenant_id:        str = Depends(get_current_tenant)
):
    res = supabase_admin.table("trades").select("id, screenshot_entry")        .eq("id", trade_id).eq("tenant_id", tenant_id).limit(1).execute()
    if not res.data:
        raise HTTPException(404, "Trade not found")
    if not res.data[0].get("screenshot_entry"):
        raise HTTPException(400, "No screenshots available")
    if not os.environ.get("ANTHROPIC_API_KEY"):
        raise HTTPException(500, "AI not configured")
    background_tasks.add_task(run_ai_analysis, trade_id, tenant_id)
    return {"status": "analysis_started"}
