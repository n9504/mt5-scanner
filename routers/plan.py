from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, List
from core.auth import get_current_tenant
from core.database import supabase_admin
import os, json

router = APIRouter(prefix="/api/v1/plan", tags=["plan"])

class DaySummary(BaseModel):
    day: str
    symbols: List[dict]

class PlanRequest(BaseModel):
    trades_summary:  List[DaySummary]
    daily_target:    Optional[float] = 0
    account_balance: Optional[float] = 0

@router.post("/generate")
async def generate_plan(body: PlanRequest, tenant_id: str = Depends(get_current_tenant)):
    import anthropic as ant
    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        return {"error":"AI not configured"}

    prompt = f"""You are a trading performance analyst. Analyse this trader's 4-week performance by day.

Daily profit target: ${body.daily_target}
Account balance: ${body.account_balance}

Performance by day (last 4 weeks):
{json.dumps([d.dict() for d in body.trades_summary], indent=2)}

Return ONLY valid JSON:
{{
  "summary": "2-3 sentence overall assessment of their weekly edge pattern",
  "days": [
    {{
      "day": "Monday",
      "focus": "best instrument(s) for this day based on win rate and P&L",
      "caution": "worst instrument(s) to be careful with on this day",
      "note": "one specific observation about their trading on this day"
    }}
  ]
}}

Rules:
- Only include days with sufficient data (2+ trades per symbol)
- Be specific - name the instruments and their win rates
- Focus on patterns not predictions
- Add disclaimer: this is historical data not a recommendation
- JSON only, no markdown
"""
    try:
        client = ant.Anthropic(api_key=api_key)
        resp   = client.messages.create(
            model="claude-sonnet-4-5", max_tokens=1500,
            messages=[{"role":"user","content":prompt}]
        )
        text   = resp.content[0].text.strip().replace("```json","").replace("```","").strip()
        return json.loads(text)
    except Exception as e:
        return {"error": str(e)}
