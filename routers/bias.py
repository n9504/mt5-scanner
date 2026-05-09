from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import date, timedelta
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/bias", tags=["bias"])

class BiasSync(BaseModel):
    scanner:     str
    symbol:      str
    bias:        Optional[str]   = "NEUTRAL"
    condition:   Optional[str]   = None
    confidence:  Optional[float] = None
    hypothesis:  Optional[str]   = None
    bias_reason: Optional[str]   = None
    key_levels:  Optional[dict]  = None
    atr_daily:   Optional[float] = None
    buy_tp:      Optional[float] = None
    sell_tp:     Optional[float] = None
    session:     Optional[str]   = None

@router.post("/sync")
async def sync_bias(body: BiasSync, tenant_id: str = Depends(get_tenant_by_api_key)):
    today = str(date.today())
    data = {
        "scanner":     body.scanner,
        "symbol":      body.symbol,
        "bias":        body.bias,
        "condition":   body.condition,
        "confidence":  body.confidence,
        "hypothesis":  body.hypothesis,
        "bias_reason": body.bias_reason,
        "key_levels":  body.key_levels,
        "atr_daily":   body.atr_daily,
        "buy_tp":      body.buy_tp,
        "sell_tp":     body.sell_tp,
        "session":     body.session,
        "bias_date":   today,
        "tenant_id":   None,
    }
    existing = supabase_admin.table("scanner_bias")\
        .select("id")\
        .eq("scanner", body.scanner)\
        .eq("symbol", body.symbol)\
        .eq("bias_date", today)\
        .limit(1).execute()

    if existing.data:
        supabase_admin.table("scanner_bias")\
            .update(data).eq("id", existing.data[0]["id"]).execute()
    else:
        supabase_admin.table("scanner_bias").insert(data).execute()

    return {"status": "ok"}

@router.get("")
async def get_bias(
    scanner: Optional[str] = None,
    tenant_id: str = Depends(get_current_tenant)
):
    # Get last 7 days, return most recent per symbol
    week_ago = str(date.today() - timedelta(days=7))
    query = supabase_admin.table("scanner_bias")\
        .select("*")\
        .gte("bias_date", week_ago)\
        .order("bias_date", desc=True)\
        .order("symbol")

    if scanner:
        query = query.eq("scanner", scanner)

    res = query.execute()
    rows = res.data or []

    # Return latest per symbol (first occurrence = most recent)
    result = {}
    for row in rows:
        sym = row["symbol"]
        if sym not in result:
            result[sym] = row

    return result
