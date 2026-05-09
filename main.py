from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import auth, trades, account, config, journal, insights, news, alerts, setup, plan, tagging, daily_plan

app = FastAPI(title="TradePattrnly API", version="2.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(trades.router)
app.include_router(account.router)
app.include_router(config.router)
app.include_router(journal.router)
app.include_router(insights.router)
app.include_router(news.router)
app.include_router(alerts.router)
app.include_router(setup.router)
app.include_router(plan.router)
app.include_router(tagging.router)
app.include_router(daily_plan.router)

@app.get("/")
async def root(): return {"status": "ok", "version": "2.0.0"}

@app.get("/health")
async def health(): return {"status": "healthy"}
