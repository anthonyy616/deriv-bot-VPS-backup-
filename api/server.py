from fastapi import FastAPI, HTTPException, Request, Depends
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List
from core.bot_manager import BotManager
from core.engine import TradingEngine 
from supabase import create_client, Client
import asyncio
import os
import pathlib
from dotenv import load_dotenv
from cachetools import TTLCache 

# FIX #1: Get absolute path to .env
PROJECT_ROOT = pathlib.Path(__file__).parent.parent.resolve()
env_path = PROJECT_ROOT / '.env'

print(f"📂 Looking for .env at: {env_path}")
print(f"📂 .env exists: {env_path.exists()}")

# FIX #2: Force load from absolute path
load_dotenv(dotenv_path=str(env_path))

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_KEY = os.getenv("SUPABASE_KEY")

print(f"🔑 SUPABASE_URL: {SUPABASE_URL}")
print(f"🔑 SUPABASE_KEY: {'***LOADED***' if SUPABASE_KEY else 'MISSING!'}")

# FIX #3: Graceful handling if env is missing
if not SUPABASE_URL or not SUPABASE_KEY:
    print("❌ CRITICAL: .env file not found or missing keys!")
    print(f"   Expected .env at: {env_path}")
    # Create a dummy placeholder to prevent crash - login will fail but server runs
    SUPABASE_URL = SUPABASE_URL or "https://placeholder.supabase.co"
    SUPABASE_KEY = SUPABASE_KEY or "placeholder_key"

supabase: Client = create_client(SUPABASE_URL, SUPABASE_KEY)

# Auth Cache (60 seconds) initially 60 but i chnaged it to 600
auth_cache = TTLCache(maxsize=100, ttl=600)

# --- 1. Initialize Core Systems ---
bot_manager = BotManager()
trading_engine = TradingEngine(bot_manager)

@app.on_event("startup")
async def startup_event():
    print("🚀 Server Starting: Launching Monolith Engine...")
    asyncio.create_task(trading_engine.start())

    # 2. RESURRECTION PROTOCOL
    await asyncio.sleep(2) 
    await bot_manager.restore_sessions()

class ConfigUpdate(BaseModel):
    symbol: str | None = None
    spread: float | None = None
    buy_stop_tp: float | None = None
    buy_stop_sl: float | None = None
    sell_stop_tp: float | None = None
    sell_stop_sl: float | None = None
    step_lots: List[float] | None = None
    max_positions: int | None = None
    max_runtime_minutes: int | None = None
    max_drawdown_usd: float | None = None

# --- 2. Auth Helper ---
def verify_token_sync(token):
    if token in auth_cache: return auth_cache[token]
    user = supabase.auth.get_user(token)
    if user and user.user:
        auth_cache[token] = user
        return user
    return None

async def get_current_bot(request: Request):
    auth_header = request.headers.get('Authorization')
    if not auth_header: raise HTTPException(401, "Missing token")
    
    try:
        user = await asyncio.to_thread(verify_token_sync, auth_header.split(" ")[1])
        if not user: raise HTTPException(401, "Invalid Token")
        return await bot_manager.get_or_create_bot(user.user.id)
    except Exception:
        raise HTTPException(401, "Auth Failed")

# --- 3. API Routes ---

@app.get("/env")
async def get_env():
    return { "SUPABASE_URL": SUPABASE_URL, "SUPABASE_KEY": SUPABASE_KEY }

@app.get("/config")
async def get_config(bot = Depends(get_current_bot)):
    return bot.config

@app.post("/config")
async def update_config(config: ConfigUpdate, bot = Depends(get_current_bot)):
    old_sym = bot.config.get('symbol')
    data = {k: v for k, v in config.model_dump().items() if v is not None}
    bot.config_manager.update_config(data)
    if config.symbol and config.symbol != old_sym:
        await bot.start_ticker()
    return True

@app.post("/control/start")
async def start_bot(request: Request):
    bot_instance = await get_current_bot(request)
    user_id = bot_instance.config_manager.user_id 
    await bot_manager.start_bot(user_id)
    return {"status": "started"}

@app.post("/control/stop")
async def stop_bot(request: Request):
    bot_instance = await get_current_bot(request)
    user_id = bot_instance.config_manager.user_id
    await bot_manager.stop_bot(user_id)
    return {"status": "stopped"}

@app.get("/status")
async def get_status(bot = Depends(get_current_bot)):
    return bot.get_status()

# --- 4. Static & Root Routes ---
app.mount("/static", StaticFiles(directory="static"), name="static")

@app.get("/")
async def read_index():
    return FileResponse('static/index.html')