from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional, List
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin

router = APIRouter(prefix="/api/v1/account", tags=["account"])

class AccountSync(BaseModel):
    login:    int
    server:   str
    currency: str = "USD"
    label:    Optional[str] = None
    balance:  float
    equity:   float

class PositionSync(BaseModel):
    ticket:        int
    symbol:        str
    bias:          str
    lot:           float
    entry_price:   float
    sl:            Optional[float] = None
    tp:            Optional[float] = None
    profit:        float
    commission:    float = 0
    swap:          float = 0
    price_current: float
    magic:         int = 0

# ── EA syncs account info ──
@router.post("/sync")
async def sync_account(
    body:      AccountSync,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    # Upsert account — link pending account if exists, else create new
    existing = supabase_admin.table("accounts")\
        .select("id,pending")\
        .eq("tenant_id", tenant_id)\
        .eq("login", body.login)\
        .execute()

    data = {
        "tenant_id": tenant_id,
        "login":     body.login,
        "server":    body.server,
        "currency":  body.currency,
        "label":     body.label,
        "balance":   body.balance,
        "equity":    body.equity,
        "active":    True,
        "pending":   False,
    }

    if existing.data:
        supabase_admin.table("accounts")\
            .update({"balance": body.balance, "equity": body.equity, "pending": False})\
            .eq("id", existing.data[0]["id"])\
            .execute()
        account_id = existing.data[0]["id"]
    else:
        # Check for a pending (pre-generated) account to link
        pending = supabase_admin.table("accounts")\
            .select("id")\
            .eq("tenant_id", tenant_id)\
            .eq("pending", True)\
            .limit(1).execute()

        if pending.data:
            # Link the pending account to this real MT5 account
            supabase_admin.table("accounts")\
                .update(data)\
                .eq("id", pending.data[0]["id"])\
                .execute()
            account_id = pending.data[0]["id"]
            print(f"[Account] Linked pending account {account_id} to MT5 login {body.login}")
        else:
            res = supabase_admin.table("accounts").insert(data).execute()
            account_id = res.data[0]["id"]

    return {"account_id": account_id, "balance": body.balance}

# ── Dashboard reads account ──
@router.get("")
async def get_accounts(
    tenant_id: str = Depends(get_current_tenant)
):
    res = supabase_admin.table("accounts")\
        .select("*")\
        .eq("tenant_id", tenant_id)\
        .eq("active", True)\
        .execute()
    return res.data or []

@router.get("/by-key")
async def get_accounts_by_key(
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    res = supabase_admin.table("accounts")        .select("*")        .eq("tenant_id", tenant_id)        .eq("active", True)        .execute()
    return res.data or []
