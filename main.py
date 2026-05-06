from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routers import auth, signals, trades, account, bias, config, journal

app = FastAPI(
    title="MT5 Scanner API",
    description="Multi-tenant MT5 trading scanner API",
    version="1.0.0",
)

# CORS — allow React frontend and EA
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Routers
app.include_router(auth.router)
app.include_router(signals.router)
app.include_router(trades.router)
app.include_router(account.router)
app.include_router(bias.router)
app.include_router(config.router)
app.include_router(journal.router)

@app.get("/")
async def root():
    return {"status": "ok", "version": "1.0.0"}

@app.get("/health")
async def health():
    return {"status": "healthy"}
