from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
import hashlib, secrets
from core.database import supabase_admin
from core.auth import create_token

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
    res = supabase_admin.table("tenants").insert({
        "email":         body.email,
        "name":          body.name,
        "password_hash": hashed,
    }).execute()

    tenant = res.data[0]

    # Create default config
    supabase_admin.table("configs").insert({
        "tenant_id": tenant["id"]
    }).execute()

    token = create_token(tenant["id"])
    return {
        "token":   token,
        "api_key": tenant["api_key"],
        "tenant":  {"id": tenant["id"], "email": tenant["email"], "name": tenant["name"]},
    }

@router.post("/login")
async def login(body: LoginRequest):
    res = supabase_admin.table("tenants")\
        .select("id, email, name, password_hash, api_key, active")\
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

    token = create_token(tenant["id"])
    return {
        "token":   token,
        "api_key": tenant["api_key"],
        "tenant":  {"id": tenant["id"], "email": tenant["email"], "name": tenant["name"]},
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
