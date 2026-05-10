from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
import hashlib, secrets
from datetime import datetime, timedelta
from core.database import supabase_admin
from core.auth import create_token
from services.email import send_welcome_email

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])

def hash_password(password: str) -> str:
    salt = secrets.token_hex(32)
    h = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000)
    return f"{salt}${h.hex()}"

def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, h = hashed.split('$')
        check = hashlib.pbkdf2_hmac('sha256', password.encode(), salt.encode(), 260000)
        return check.hex() == h
    except Exception:
        return False

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    name: str

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

@router.post("/register")
async def register(body: RegisterRequest):
    # Check existing
    existing = supabase_admin.table("tenants")\
        .select("id").eq("email", body.email).execute()
    if existing.data:
        raise HTTPException(400, "Email already registered")

    hashed = hash_password(body.password)
    beta_expires = (datetime.utcnow() + timedelta(days=21)).isoformat()
    res = supabase_admin.table("tenants").insert({
        "email":           body.email,
        "name":            body.name,
        "password_hash":   hashed,
        "subscription":    "beta",
        "beta_expires_at": beta_expires,
        "is_beta":         True,
    }).execute()

    tenant = res.data[0]
    tenant_id = tenant["id"]

    # Create default config
    supabase_admin.table("configs").insert({
        "tenant_id": tenant_id
    }).execute()

    # Auto-create a pending account — links to real MT5 on first sync
    import uuid as _uuid
    account_res = supabase_admin.table("accounts").insert({
        "tenant_id": tenant_id,
        "label":     "My Trading Account",
        "pending":   True,
        "login":     0,
        "server":    "",
        "currency":  "USD",
        "balance":   0,
        "equity":    0,
        "active":    True,
    }).execute()
    account_id = account_res.data[0]["id"] if account_res.data else None

    # Store account_id on tenant for easy reference
    if account_id:
        supabase_admin.table("tenants").update({
            "default_account_id": account_id
        }).eq("id", tenant_id).execute()

    token = create_token(tenant_id)

    # Send welcome email with API key + account ID
    try:
        send_welcome_email(
            to         = tenant["email"],
            name       = tenant["name"] or tenant["email"].split("@")[0],
            api_key    = tenant["api_key"],
            account_id = account_id,
        )
    except Exception as e:
        print(f"Welcome email failed: {e}")

    return {
        "token":      token,
        "api_key":    tenant["api_key"],
        "account_id": account_id,
        "tenant":     {"id": tenant_id, "email": tenant["email"], "name": tenant["name"]},
    }

@router.post("/login")
async def login(body: LoginRequest):
    res = supabase_admin.table("tenants")\
        .select("id, email, name, password_hash, api_key, active, subscription, is_beta, beta_expires_at")\
        .eq("email", body.email)\
        .single()\
        .execute()

    if not res.data:
        raise HTTPException(401, "Invalid credentials")

    tenant = res.data
    if not tenant["active"]:
        raise HTTPException(403, "Account suspended")
    if not verify_password(body.password, tenant["password_hash"]):
        raise HTTPException(401, "Invalid credentials")

    # Check beta expiry
    subscription = tenant.get("subscription", "free")
    days_left = None
    if tenant.get("is_beta") and tenant.get("beta_expires_at"):
        try:
            expires = datetime.fromisoformat(str(tenant["beta_expires_at"]).replace("Z","").replace("+00:00",""))
            if datetime.utcnow() > expires and subscription == "elite":
                subscription = "free"
                supabase_admin.table("tenants").update({"subscription":"free"})\
                    .eq("id", tenant["id"]).execute()
            else:
                days_left = max(0, (expires - datetime.utcnow()).days)
        except Exception: pass

    token = create_token(tenant["id"])
    return {
        "token":   token,
        "api_key": tenant["api_key"],
        "tenant":  {
            "id":             tenant["id"],
            "email":          tenant["email"],
            "name":           tenant["name"],
            "subscription":   subscription,
            "is_beta":        tenant.get("is_beta", False),
            "beta_days_left": days_left,
        }
    }

@router.get("/me")
async def me(tenant_id: str = None):
    # Called with JWT — return tenant info
    from core.auth import get_current_tenant
    from fastapi import Depends
    res = supabase_admin.table("tenants")\
        .select("id, email, name, api_key, subscription, created_at")\
        .eq("id", tenant_id)\
        .single()\
        .execute()
    return res.data
