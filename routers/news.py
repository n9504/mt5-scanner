from fastapi import APIRouter, Depends, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
from datetime import date, datetime, timedelta
from core.auth import get_current_tenant, get_tenant_by_api_key
from core.database import supabase_admin
import httpx
import os

router = APIRouter(prefix="/api/v1", tags=["news"])

JBLANKED_KEY = os.environ.get("JBLANKED_API_KEY", "")

async def fetch_and_cache_news():
    today = str(date.today())
    existing = supabase_admin.table("news_events").select("id").eq("event_date", today).limit(1).execute()
    if existing.data:
        return
    try:
        url = "https://www.jblanked.com/news/api/forex-factory/calendar/today/"
        headers = {"Authorization": f"Bearer {JBLANKED_KEY}"} if JBLANKED_KEY else {}
        async with httpx.AsyncClient(timeout=10) as client:
            r = await client.get(url, headers=headers)
            if r.status_code != 200: return
            events = r.json()
        rows = []
        for e in (events if isinstance(events, list) else []):
            impact = str(e.get("impact", e.get("volatility","Low"))).capitalize()
            rows.append({
                "event_date": today,
                "event_time": str(e.get("time", e.get("date",""))),
                "currency":   str(e.get("currency", e.get("economy",""))),
                "impact":     impact,
                "title":      str(e.get("title", e.get("name",""))),
                "forecast":   str(e.get("forecast","")),
                "previous":   str(e.get("previous","")),
                "actual":     str(e.get("actual","")),
            })
        if rows:
            supabase_admin.table("news_events").insert(rows).execute()
            print(f"[News] Cached {len(rows)} events")
    except Exception as e:
        print(f"[News] Error: {e}")

@router.get("/news/upcoming")
async def get_upcoming_news(
    background_tasks: BackgroundTasks,
    minutes_ahead: int = 30,
    impact: str = "High",
    currencies: Optional[str] = None,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    background_tasks.add_task(fetch_and_cache_news)
    today = str(date.today())
    res = supabase_admin.table("news_events").select("*")\
        .eq("event_date", today).eq("impact", impact).order("event_time").execute()
    events = res.data or []
    if currencies:
        clist = [c.strip().upper() for c in currencies.split(",")]
        events = [e for e in events if e.get("currency","").upper() in clist]
    return events

@router.get("/news/today")
async def get_today_news(
    background_tasks: BackgroundTasks,
    tenant_id: str = Depends(get_current_tenant)
):
    background_tasks.add_task(fetch_and_cache_news)
    today = str(date.today())
    res = supabase_admin.table("news_events").select("*")\
        .eq("event_date", today).in_("impact", ["High","Medium"])\
        .order("event_time").execute()
    return res.data or []

@router.post("/news/fetch")
async def force_fetch_news(tenant_id: str = Depends(get_current_tenant)):
    """Force fetch today's news - call this to populate news_events table."""
    today = str(date.today())
    supabase_admin.table("news_events").delete().eq("event_date", today).execute()
    await fetch_and_cache_news()
    res = supabase_admin.table("news_events").select("*")\
        .eq("event_date", today).order("event_time").execute()
    return {"fetched": len(res.data or []), "events": res.data or []}

class AlertCreate(BaseModel):
    account_id: str
    type: str
    message: str
    data: Optional[dict] = None

@router.post("/alerts/create")
async def create_alert(body: AlertCreate, tenant_id: str = Depends(get_tenant_by_api_key)):
    supabase_admin.table("alerts").insert({
        "tenant_id": tenant_id, "account_id": body.account_id,
        "type": body.type, "message": body.message, "data": body.data or {},
    }).execute()
    return {"status": "ok"}

@router.get("/alerts")
async def get_alerts(tenant_id: str = Depends(get_current_tenant)):
    res = supabase_admin.table("alerts").select("*").eq("tenant_id", tenant_id)\
        .eq("read", False).order("created_at", desc=True).limit(20).execute()
    return res.data or []

@router.put("/alerts/{alert_id}/read")
async def mark_read(alert_id: str, tenant_id: str = Depends(get_current_tenant)):
    supabase_admin.table("alerts").update({"read": True})\
        .eq("id", alert_id).eq("tenant_id", tenant_id).execute()
    return {"status": "ok"}

@router.get("/alerts/pending")
async def get_pending_alerts(
    account_id: Optional[str] = None,
    tenant_id: str = Depends(get_tenant_by_api_key)
):
    query = supabase_admin.table("alerts").select("*").eq("tenant_id", tenant_id)\
        .eq("read", False).order("created_at", desc=True).limit(5)
    if account_id:
        query = query.eq("account_id", account_id)
    res = query.execute()
    alerts = res.data or []
    for a in alerts:
        supabase_admin.table("alerts").update({"read": True}).eq("id", a["id"]).execute()
    return alerts
