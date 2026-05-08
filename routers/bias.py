from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import date
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/bias", tags=["bias"])

class BiasSync(BaseModel):
    scanner:     str
    symbol:      str
    bias:        Optional[str]       = "NEUTRAL"
    condition:   Optional[str]       = None
    confidence:  Optional[float]     = None
    hypothesis:  Optional[str]       = None
    bias_reason: Optional[str]       = None
    key_levels:  Optional[dict]      = None
    atr_daily:   Optional[float]     = None
    buy_tp:      Optional[float]     = None
    sell_tp:     Optional[float]     = None
    session:     Optional[str]       = None

@router.post("/sync")
async def sync_bias(body: BiasSync, tenant_id: str = Depends(get_tenant_by_api_key)):
    """Scanner VPS posts bias here. Stored globally (tenant_id=NULL) so all users can read."""
    today = str(date.today())

    # Upsert - update existing row for same scanner+symbol+date
    existing = supabase_admin.table("scanner_bias")\
        .select("id")\
        .eq("scanner", body.scanner)\
        .eq("symbol", body.symbol)\
        .eq("bias_date", today)\
        .is_("tenant_id", "null")\
        .limit(1).execute()

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
        "tenant_id":   None,  # Global — visible to all
    }

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
    """All users read global bias (tenant_id IS NULL)."""
    today = str(date.today())
    query = supabase_admin.table("scanner_bias")\
        .select("*")\
        .eq("bias_date", today)\
        .is_("tenant_id", "null")\
        .order("symbol")

    if scanner:
        query = query.eq("scanner", scanner)

    res = query.execute()
    rows = res.data or []

    # Return as dict keyed by symbol for easy frontend use
    result = {}
    for row in rows:
        result[row["symbol"]] = row

    return result
