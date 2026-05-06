from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import date
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/bias", tags=["bias"])

class BiasSync(BaseModel):
    scanner:    str       # S2 or S3
    symbol:     str
    bias:       str       # BUY, SELL, NEUTRAL
    condition:  Optional[str]   = None
    confidence: Optional[float] = None
    hypothesis: Optional[str]   = None
    bias_reason:Optional[str]   = None
    key_levels: Optional[dict]  = None
    atr_daily:  Optional[float] = None
    buy_tp:     Optional[float] = None
    sell_tp:    Optional[float] = None
    session:    Optional[str]   = None

# ── Primary scanner pushes bias ──
@router.post("/sync")
async def sync_bias(
    body:      BiasSync,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    supabase_admin.table("scanner_bias").insert({
        "tenant_id":  tenant_id,
        "scanner":    body.scanner,
        "symbol":     body.symbol,
        "bias":       body.bias,
        "condition":  body.condition,
        "confidence": body.confidence,
        "hypothesis": body.hypothesis,
        "bias_reason":body.bias_reason,
        "key_levels": body.key_levels,
        "atr_daily":  body.atr_daily,
        "buy_tp":     body.buy_tp,
        "sell_tp":    body.sell_tp,
        "session":    body.session,
        "bias_date":  str(date.today()),
    }).execute()
    return {"status": "ok"}

# ── Secondary scanner / dashboard reads bias ──
@router.get("")
async def get_bias(
    scanner:   Optional[str] = None,
    tenant_id: str = Depends(get_current_tenant)
):
    query = supabase_admin.table("scanner_bias")\
        .select("*")\
        .eq("tenant_id", tenant_id)\
        .eq("bias_date", str(date.today()))\
        .order("recorded_at", desc=True)

    if scanner:
        query = query.eq("scanner", scanner)

    res = query.execute()

    # Return as dict keyed by symbol
    result = {}
    for r in (res.data or []):
        sym = r["symbol"]
        if sym not in result:
            result[sym] = r
    return result
