from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, EmailStr
from passlib.context import CryptContext
from core.database import supabase_admin
from core.auth import create_token

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])
pwd   = CryptContext(schemes=["bcrypt"], deprecated="auto", bcrypt__rounds=12)

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

    hashed = pwd.hash(body.password[:72])
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
    if not pwd.verify(body.password[:72], tenant["password_hash"]):
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
