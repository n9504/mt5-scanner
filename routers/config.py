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
