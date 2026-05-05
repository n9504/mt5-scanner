from datetime import datetime, timedelta
from jose import JWTError, jwt
from fastapi import Depends, HTTPException, Security, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials, APIKeyHeader
from core.config import settings
from core.database import supabase_admin

bearer_scheme = HTTPBearer(auto_error=False)
api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)

# ── JWT ──

def create_token(tenant_id: str) -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode(
        {"sub": tenant_id, "exp": expire},
        settings.jwt_secret,
        algorithm=settings.jwt_algorithm
    )

def decode_token(token: str) -> str:
    try:
        payload = jwt.decode(token, settings.jwt_secret,
                             algorithms=[settings.jwt_algorithm])
        return payload.get("sub")
    except JWTError:
        return None

# ── Get current tenant from JWT ──

async def get_current_tenant(
    credentials: HTTPAuthorizationCredentials = Security(bearer_scheme)
):
    if not credentials:
        raise HTTPException(status_code=401, detail="Not authenticated")
    tenant_id = decode_token(credentials.credentials)
    if not tenant_id:
        raise HTTPException(status_code=401, detail="Invalid token")
    return tenant_id

# ── Get tenant from API key (for EA and scanner) ──

async def get_tenant_by_api_key(
    api_key: str = Security(api_key_header)
):
    if not api_key:
        raise HTTPException(status_code=401, detail="API key required")
    res = supabase_admin.table("tenants")\
        .select("id, active")\
        .eq("api_key", api_key)\
        .single()\
        .execute()
    if not res.data:
        raise HTTPException(status_code=401, detail="Invalid API key")
    if not res.data["active"]:
        raise HTTPException(status_code=403, detail="Account suspended")
    return res.data["id"]
