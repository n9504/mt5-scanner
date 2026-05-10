from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/setup", tags=["setup"])

class PersonalSetup(BaseModel):
    account_id:          str
    timezone:            str = "UTC"
    daily_profit_target: Optional[float] = None
    daily_loss_cap:      Optional[float] = None
    risk_per_trade_pct:  Optional[float] = 1.0
    max_trades_per_day:  Optional[int]   = 4

class PropSetup(BaseModel):
    account_id:              str
    timezone:                str = "UTC"
    prop_max_loss_per_trade: Optional[float] = None
    prop_daily_max_loss:     Optional[float] = None
    prop_5day_max_loss:      Optional[float] = None
    prop_profit_cap:         Optional[float] = None
    prop_challenge_target:   Optional[float] = None
    risk_per_trade_pct:      Optional[float] = 1.0
    max_trades_per_day:      Optional[int]   = 4

class TimezoneSync(BaseModel):
    account_id: str
    server:     str = ""
    utc_offset: int = 0

@router.post("/personal")
async def setup_personal(body: PersonalSetup, tenant_id: str = Depends(get_current_tenant)):
    supabase_admin.table("accounts").update({
        "account_type": "personal", "timezone": body.timezone,
        "daily_profit_target": body.daily_profit_target,
        "daily_loss_cap": body.daily_loss_cap,
        "risk_per_trade_pct": body.risk_per_trade_pct,
        "max_trades_per_day": body.max_trades_per_day,
        "setup_complete": True,
    }).eq("id", body.account_id).eq("tenant_id", tenant_id).execute()
    return {"status": "ok"}

@router.post("/prop")
async def setup_prop(body: PropSetup, tenant_id: str = Depends(get_current_tenant)):
    supabase_admin.table("accounts").update({
        "account_type": "prop", "timezone": body.timezone,
        "prop_max_loss_per_trade": body.prop_max_loss_per_trade,
        "prop_daily_max_loss": body.prop_daily_max_loss,
        "prop_5day_max_loss": body.prop_5day_max_loss,
        "prop_profit_cap": body.prop_profit_cap,
        "prop_challenge_target": body.prop_challenge_target,
        "risk_per_trade_pct": body.risk_per_trade_pct,
        "max_trades_per_day": body.max_trades_per_day,
        "setup_complete": True,
    }).eq("id", body.account_id).eq("tenant_id", tenant_id).execute()
    return {"status": "ok"}

@router.post("/timezone")
async def sync_timezone(body: TimezoneSync, tenant_id: str = Depends(get_tenant_by_api_key)):
    server = body.server.lower()
    tz = "UTC"
    if any(x in server for x in ["sydney","australia","aest"]):   tz = "Australia/Sydney"
    elif any(x in server for x in ["london","gmt","bst"]):        tz = "Europe/London"
    elif any(x in server for x in ["newyork","new york","est"]):  tz = "America/New_York"
    elif any(x in server for x in ["tokyo","japan","jst"]):       tz = "Asia/Tokyo"
    elif any(x in server for x in ["singapore","sgt"]):           tz = "Asia/Singapore"
    elif any(x in server for x in ["frankfurt","germany","cet"]): tz = "Europe/Berlin"
    elif body.utc_offset == 10: tz = "Australia/Sydney"
    elif body.utc_offset == 0:  tz = "Europe/London"
    elif body.utc_offset == -5: tz = "America/New_York"
    elif body.utc_offset == 9:  tz = "Asia/Tokyo"
    elif body.utc_offset == 8:  tz = "Asia/Singapore"

    supabase_admin.table("accounts").update({"timezone": tz})\
        .eq("id", body.account_id).eq("tenant_id", tenant_id).execute()
    return {"status": "ok", "timezone": tz}
