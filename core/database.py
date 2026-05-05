from supabase import create_client, Client
from core.config import settings

# Anon client — for user-facing operations (RLS enforced)
supabase: Client = create_client(
    settings.supabase_url,
    settings.supabase_anon_key
)

# Service client — for admin operations (bypasses RLS)
supabase_admin: Client = create_client(
    settings.supabase_url,
    settings.supabase_service_key
)
