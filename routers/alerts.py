from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from datetime import datetime
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/alerts", tags=["alerts"])

class AlertCreate(BaseModel):
    type:    str
    symbol:  Optional[str] = None
    message: str
    lot:     Optional[float] = None
    avg_lot: Optional[float] = None

@router.post("/create")
async def create_alert(
    body:      AlertCreate,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    supabase_admin.table("alerts").insert({
        "tenant_id":  tenant_id,
        "type":       body.type,
        "symbol":     body.symbol,
        "message":    body.message,
        "data":       {"lot": body.lot, "avg_lot": body.avg_lot},
        "read":       False,
        "created_at": datetime.utcnow().isoformat(),
    }).execute()
    return {"status": "ok"}

@router.get("")
async def get_alerts(
    unread_only: bool = False,
    tenant_id:   str  = Depends(get_current_tenant)
):
    query = supabase_admin.table("alerts")\
        .select("*")\
        .eq("tenant_id", tenant_id)\
        .order("created_at", desc=True)\
        .limit(20)
    if unread_only:
        query = query.eq("read", False)
    res = query.execute()
    return res.data or []

@router.put("/{alert_id}/read")
async def mark_read(
    alert_id:  str,
    tenant_id: str = Depends(get_current_tenant)
):
    supabase_admin.table("alerts")\
        .update({"read": True})\
        .eq("id", alert_id)\
        .eq("tenant_id", tenant_id)\
        .execute()
    return {"status": "ok"}
