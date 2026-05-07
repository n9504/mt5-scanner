from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from core.auth import get_current_tenant
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/config", tags=["config"])

class ConfigUpdate(BaseModel):
    s1_enabled:         Optional[bool]  = None
    s2_enabled:         Optional[bool]  = None
    max_positions:      Optional[int]   = None
    max_per_scanner:    Optional[int]   = None
    loss_limit_pct:     Optional[float] = None
    profit_ceil_pct:    Optional[float] = None
    lot_sizes:          Optional[dict]  = None
    trading_start_utc:  Optional[int]   = None
    trading_end_utc:    Optional[int]   = None
    # Account setup fields
    account_type:       Optional[str]   = None  # personal, prop
    daily_target:       Optional[float] = None
    daily_loss_cap:     Optional[float] = None
    risk_per_trade:     Optional[float] = None
    # Prop firm fields
    firm_name:          Optional[str]   = None
    max_loss_trade:     Optional[float] = None
    daily_max_loss:     Optional[float] = None
    five_day_max_loss:  Optional[float] = None
    profit_cap:         Optional[float] = None
    challenge_target:   Optional[float] = None

@router.get("")
async def get_config(tenant_id: str = Depends(get_current_tenant)):
    res = supabase_admin.table("configs")\
        .select("*")\
        .eq("tenant_id", tenant_id)\
        .single()\
        .execute()
    return res.data or {}

@router.put("")
async def update_config(
    body:      ConfigUpdate,
    tenant_id: str = Depends(get_current_tenant)
):
    updates = {k: v for k, v in body.dict().items() if v is not None}
    if not updates:
        return {"status": "no changes"}

    from datetime import datetime
    updates["updated_at"] = datetime.utcnow().isoformat()

    supabase_admin.table("configs")\
        .update(updates)\
        .eq("tenant_id", tenant_id)\
        .execute()
    return {"status": "updated"}
