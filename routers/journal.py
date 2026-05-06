from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin
import anthropic
import base64
import os

router = APIRouter(prefix="/api/v1/trades", tags=["journal"])

class JournalUpdate(BaseModel):
    notes: Optional[str] = None
    tags:  Optional[List[str]] = None

class ScreenshotSync(BaseModel):
    ticket:           int
    screenshot_entry: Optional[str] = None  # base64
    screenshot_exit:  Optional[str] = None  # base64

# ── Save tags + notes ──
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
        .update(updates)\
        .eq("id", trade_id)\
        .eq("tenant_id", tenant_id)\
        .execute()

    if not res.data:
        raise HTTPException(404, "Trade not found")
    return {"status": "updated"}

# ── EA uploads screenshots ──
@router.post("/screenshots")
async def upload_screenshots(
    body:      ScreenshotSync,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    # Find trade by ticket
    res = supabase_admin.table("trades")\
        .select("id")\
        .eq("tenant_id", tenant_id)\
        .eq("ticket", body.ticket)\
        .single()\
        .execute()

    if not res.data:
        raise HTTPException(404, f"Trade ticket {body.ticket} not found")

    trade_id = res.data["id"]
    updates  = {}

    if body.screenshot_entry:
        updates["screenshot_entry"] = body.screenshot_entry
    if body.screenshot_exit:
        updates["screenshot_exit"] = body.screenshot_exit

    if updates:
        supabase_admin.table("trades")\
            .update(updates)\
            .eq("id", trade_id)\
            .execute()

    return {"status": "ok", "trade_id": trade_id}

# ── AI analysis ──
@router.post("/{trade_id}/analyse")
async def analyse_trade(
    trade_id:  str,
    tenant_id: str = Depends(get_current_tenant)
):
    # Get trade with screenshots
    res = supabase_admin.table("trades")\
        .select("*")\
        .eq("id", trade_id)\
        .eq("tenant_id", tenant_id)\
        .single()\
        .execute()

    if not res.data:
        raise HTTPException(404, "Trade not found")

    trade = res.data
    entry_img = trade.get("screenshot_entry")
    exit_img  = trade.get("screenshot_exit")

    if not entry_img and not exit_img:
        raise HTTPException(400, "No screenshots available for analysis")

    # Build Claude message
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(500, "AI analysis not configured")

    client  = anthropic.Anthropic(api_key=api_key)
    content = []

    if entry_img:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": entry_img.split(",")[-1] if "," in entry_img else entry_img}
        })
        content.append({"type": "text", "text": "Entry chart:"})

    if exit_img:
        content.append({
            "type": "image",
            "source": {"type": "base64", "media_type": "image/png",
                       "data": exit_img.split(",")[-1] if "," in exit_img else exit_img}
        })
        content.append({"type": "text", "text": "Exit chart:"})

    symbol  = trade.get("symbol", "")
    bias    = trade.get("bias", "")
    outcome = trade.get("execution_outcome", "")
    pnl     = trade.get("net_pnl", 0)
    rr      = trade.get("rr_actual", 0)

    content.append({"type": "text", "text": f"""
Analyse this {symbol} {bias} trade.
Outcome: {outcome} | P&L: {pnl} | RR: {rr}

Return ONLY valid JSON:
{{
  "setup_tags": ["FVG", "OB", "BOS", "CHoCH", "Support", "Resistance", "Breakout", "Range", "Trend", "Reversal"],
  "emotion_tags": ["Disciplined", "FOMO", "Revenge", "Hesitated", "Overconfident", "Patient"],
  "analysis": "2-3 sentence analysis of the trade setup, execution and what could be improved",
  "key_observation": "single most important thing about this trade"
}}

Rules:
- setup_tags: only include tags visible in the chart (max 3)
- emotion_tags: detect from entry timing, position sizing, exit behaviour (max 2)
- JSON only, no markdown
"""})

    try:
        resp = client.messages.create(
            model      = "claude-sonnet-4-5",
            max_tokens = 500,
            messages   = [{"role": "user", "content": content}]
        )
        import json
        text   = resp.content[0].text.strip()
        text   = text.replace("```json","").replace("```","").strip()
        result = json.loads(text)

        all_tags = result.get("setup_tags", []) + result.get("emotion_tags", [])
        analysis = result.get("analysis", "")

        # Save to DB
        existing_tags = trade.get("tags") or []
        new_tags = list(set(existing_tags + all_tags))
        supabase_admin.table("trades").update({
            "tags":        new_tags,
            "ai_analysis": analysis,
        }).eq("id", trade_id).execute()

        return {
            "status":   "ok",
            "tags":     all_tags,
            "analysis": analysis,
            "key_observation": result.get("key_observation", ""),
        }

    except Exception as e:
        raise HTTPException(500, f"Analysis failed: {str(e)}")
