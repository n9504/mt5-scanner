from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/signals", tags=["signals"])

# ── Schemas ──

class SignalCreate(BaseModel):
    account_id:    Optional[str] = None
    scanner:       str
    symbol:        str
    bias:          str
    entry:         float
    sl:            float
    tp:            float
    lot:           Optional[float] = None
    rr_target:     Optional[float] = None
    zone_type:     Optional[str]   = None
    confluences:   Optional[float] = None
    conf_reasons:  Optional[list]  = None
    strategy_tag:  Optional[str]   = None
    session:       Optional[str]   = None
    s3_bias:       Optional[str]   = None
    s3_condition:  Optional[str]   = None
    narrative_tag: Optional[str]   = "primary"

# ── Scanner pushes new signal ──
@router.post("/create")
async def create_signal(
    body: SignalCreate,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    # Check fire_enabled from config
    cfg = supabase_admin.table("configs")\
        .select("s1_enabled, s2_enabled")\
        .eq("tenant_id", tenant_id)\
        .single().execute()

    if cfg.data:
        if body.scanner == "S1" and not cfg.data.get("s1_enabled", True):
            return {"status": "skipped", "reason": "S1 disabled"}
        if body.scanner == "S2" and not cfg.data.get("s2_enabled", True):
            return {"status": "skipped", "reason": "S2 disabled"}

    res = supabase_admin.table("signals").insert({
        "tenant_id":    tenant_id,
        "account_id":   body.account_id,
        "scanner":      body.scanner,
        "symbol":       body.symbol,
        "bias":         body.bias,
        "entry":        body.entry,
        "sl":           body.sl,
        "tp":           body.tp,
        "lot":          body.lot,
        "rr_target":    body.rr_target,
        "zone_type":    body.zone_type,
        "confluences":  body.confluences,
        "conf_reasons": body.conf_reasons,
        "strategy_tag": body.strategy_tag,
        "session":      body.session,
        "s3_bias":      body.s3_bias,
        "s3_condition": body.s3_condition,
        "narrative_tag":body.narrative_tag,
        "fire_enabled": True,
        "status":       "PENDING",
        "expires_at":   (datetime.utcnow() + timedelta(hours=4)).isoformat(),
    }).execute()
    return {"status": "created", "signal_id": res.data[0]["id"]}

# ── EA polls this every 5 seconds ──
@router.get("/pending")
async def get_pending(
    account_id: Optional[str] = None,
    tenant_id:  str = Depends(get_tenant_by_api_key)
):
    query = supabase_admin.table("signals")\
        .select("*")\
        .eq("tenant_id", tenant_id)\
        .eq("status", "PENDING")\
        .eq("fire_enabled", True)\
        .gt("expires_at", datetime.utcnow().isoformat())\
        .order("signal_time")

    if account_id:
        query = query.eq("account_id", account_id)

    res = query.execute()
    return res.data or []

# ── EA confirms signal was executed ──
@router.put("/{signal_id}/fired")
async def mark_fired(
    signal_id: str,
    ticket:    int,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    supabase_admin.table("signals").update({
        "status":   "FIRED",
        "fired_at": datetime.utcnow().isoformat(),
    }).eq("id", signal_id).eq("tenant_id", tenant_id).execute()
    return {"status": "ok"}

# ── Dashboard toggles fire_enabled ──
@router.put("/{signal_id}/toggle")
async def toggle_fire(
    signal_id: str,
    tenant_id: str = Depends(get_current_tenant)
):
    res = supabase_admin.table("signals")\
        .select("fire_enabled")\
        .eq("id", signal_id)\
        .eq("tenant_id", tenant_id)\
        .single().execute()
    if not res.data:
        raise HTTPException(404, "Signal not found")
    new_val = not res.data["fire_enabled"]
    supabase_admin.table("signals").update({
        "fire_enabled": new_val
    }).eq("id", signal_id).execute()
    return {"fire_enabled": new_val}

# ── Dashboard cancels signal ──
@router.put("/{signal_id}/cancel")
async def cancel_signal(
    signal_id: str,
    tenant_id: str = Depends(get_current_tenant)
):
    supabase_admin.table("signals").update({
        "status": "CANCELLED"
    }).eq("id", signal_id).eq("tenant_id", tenant_id).execute()
    return {"status": "cancelled"}

# ── Dashboard reads active signals ──
@router.get("")
async def list_signals(
    status:    Optional[str] = "PENDING",
    tenant_id: str = Depends(get_current_tenant)
):
    res = supabase_admin.table("signals")\
        .select("*")\
        .eq("tenant_id", tenant_id)\
        .eq("status", status)\
        .order("signal_time", desc=True)\
        .limit(50)\
        .execute()
    return res.data or []
