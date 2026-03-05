"""
4Gent — FastAPI Backend
Handles wizard → launch pipeline, agent management, Telegram webhook.
Runs on Railway. Starts FourMemeMonitor + AgentScheduler at startup.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
load_dotenv()



from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from supabase import create_client, Client

from core.launch import launch_agent, LaunchConfig
from core.monitor import FourMemeMonitor
from core.scheduler import AgentScheduler, AgentRuntime
from core.telegram import handle_owner_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("4gent.api")


# ── Global singletons ─────────────────────────────────────────────────────────
scheduler = AgentScheduler()
monitor: Optional[FourMemeMonitor] = None
supabase_client: Optional[Client] = None


def get_db() -> Client:
    global supabase_client
    if supabase_client is None:
        supabase_client = create_client(
            os.environ["SUPABASE_URL"],
            os.environ["SUPABASE_SERVICE_KEY"],
        )
    return supabase_client


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: load agents from DB, start Bitquery monitor."""
    global monitor

    db = get_db()

    # Load active agents
    await scheduler.load_from_supabase(db)
    logger.info("Loaded %d active agents", scheduler.count)

    # Start monitor
    bitquery_key = os.environ.get("BITQUERY_API_KEY", "")
    if bitquery_key:
        monitor = FourMemeMonitor(api_key=bitquery_key)
        monitor.register(scheduler.on_token_event)
        asyncio.create_task(monitor.start())
        logger.info("four.meme monitor started")
    else:
        logger.warning("BITQUERY_API_KEY not set — monitor disabled")

    yield

    # Shutdown
    if monitor:
        await monitor.stop()
    logger.info("4Gent shutdown complete")


app = FastAPI(title="4Gent API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # tighten in production
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Request / Response Models ─────────────────────────────────────────────────

class LaunchRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    ticker: str = Field(..., min_length=1, max_length=10)
    archetype: str = Field(..., pattern="^(degen|analyst|narrator|schemer|researcher|custom)$")
    prompt: str = Field(..., min_length=10, max_length=1000)
    image_url: str
    tg_channel_link: str
    owner_wallet: str
    trading_enabled: bool = False
    max_trade_bnb: float = Field(default=0.1, ge=0, le=10)
    daily_limit_bnb: float = Field(default=1.0, ge=0, le=50)
    stop_loss_pct: float = Field(default=50.0, ge=0, le=100)
    raise_amount_bnb: float = Field(default=0.0, ge=0, le=20)


class LaunchResponse(BaseModel):
    agent_id: str
    status: str
    message: str


class AgentStatusResponse(BaseModel):
    agent_id: str
    name: str
    ticker: str
    status: str
    token_address: Optional[str]
    agent_wallet: Optional[str]
    bot_username: Optional[str]
    claim_code: Optional[str]
    tg_verified: bool
    token_deployed: bool
    total_posts: int
    total_trades: int
    total_fees_bnb: float
    error_message: Optional[str]


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status": "ok",
        "active_agents": scheduler.count,
        "monitor_running": monitor is not None and monitor._running,
        "timestamp": datetime.utcnow().isoformat(),
    }


@app.post("/upload-image")
async def upload_image(file: UploadFile = File(...)):
    """
    Upload token image to four.meme CDN via their official API.
    Requires WALLET_PRIVATE_KEY env var (same wallet used for token creation).
    Returns four.meme CDN URL required by create_token imgUrl field.
    """
    import sys, os as _os
    sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "..", "packages", "fourmeme"))
    from fourmeme.auth import FourMemeAuth
    from fourmeme.client import FourMemeClient

    private_key = os.environ.get("WALLET_PRIVATE_KEY", "")
    if not private_key:
        raise HTTPException(status_code=500, detail="WALLET_PRIVATE_KEY not set")

    image_bytes = await file.read()
    mime = file.content_type or "image/png"
    filename = file.filename or "token.png"

    try:
        auth = FourMemeAuth(private_key=private_key)
        client = FourMemeClient(auth)
        cdn_url = await client.upload_image_bytes(image_bytes, filename=filename, mime=mime)
        await client.close()
        logger.info("Image uploaded to four.meme CDN: %s", cdn_url)
        return {"url": cdn_url}
    except Exception as e:
        logger.error("Image upload failed: %s", e)
        raise HTTPException(status_code=500, detail=f"Image upload failed: {str(e)}")


@app.post("/launch", response_model=LaunchResponse)
async def launch(req: LaunchRequest, background_tasks: BackgroundTasks):
    """
    Wizard submits agent config. Creates DB row, kicks off launch pipeline
    in background. Returns agent_id immediately for status polling.
    """
    db = get_db()
    agent_id = str(uuid.uuid4())

    # Create initial DB row
    db.table("agents").insert({
        "id":              agent_id,
        "name":            req.name,
        "ticker":          req.ticker,
        "archetype":       req.archetype,
        "prompt":          req.prompt,
        "image_url":       req.image_url,
        "tg_channel_link": req.tg_channel_link,
        "owner_wallet":    req.owner_wallet,
        "trading_enabled": req.trading_enabled,
        "max_trade_bnb":   req.max_trade_bnb,
        "daily_limit_bnb": req.daily_limit_bnb,
        "stop_loss_pct":   req.stop_loss_pct,
        "status":          "pending",
    }).execute()

    config = LaunchConfig(
        agent_id=agent_id,
        name=req.name,
        ticker=req.ticker,
        archetype=req.archetype,
        prompt=req.prompt,
        image_url=req.image_url,
        tg_channel_link=req.tg_channel_link,
        owner_wallet=req.owner_wallet,
        trading_enabled=req.trading_enabled,
        max_trade_bnb=req.max_trade_bnb,
        daily_limit_bnb=req.daily_limit_bnb,
        stop_loss_pct=req.stop_loss_pct,
        raise_amount_bnb=req.raise_amount_bnb,
    )

    # Run launch pipeline in background; wizard polls /agent/:id
    background_tasks.add_task(_run_launch, config)

    return LaunchResponse(
        agent_id=agent_id,
        status="pending",
        message="Launch initiated. Poll /agent/{agent_id} for status.",
    )


async def _run_launch(config: LaunchConfig) -> None:
    """Background task — runs full launch pipeline then registers runtime."""
    result = await launch_agent(config)
    if result.success:
        # Register runtime in scheduler
        db = get_db()
        row = db.table("active_agents").select("*").eq("id", config.agent_id).execute()
        if row.data:
            agent = row.data[0]
            runtime = AgentRuntime(
                agent_id=agent["id"],
                name=agent["name"],
                ticker=agent["ticker"],
                archetype=agent["archetype"],
                prompt=agent["prompt"],
                bot_token=agent["bot_token"],
                tg_channel=agent["tg_channel_link"],
                trading_enabled=agent["trading_enabled"],
                max_trade_bnb=float(agent["max_trade_bnb"]),
                daily_limit_bnb=float(agent["daily_limit_bnb"]),
                agent_wallet=agent["agent_wallet"],
                agent_wallet_enc=agent["agent_wallet_enc"],
                supabase=db,
            )
            scheduler.register(runtime)
            if monitor:
                monitor.register(runtime.handle_new_token)
        logger.info("Agent %s launched and registered in scheduler", config.agent_id)
    else:
        logger.error("Launch failed for %s: %s", config.agent_id, result.error)


@app.get("/agent/{agent_id}", response_model=AgentStatusResponse)
async def get_agent(agent_id: str):
    """Poll agent status — used by wizard during deploy + dashboard."""
    db = get_db()
    resp = db.table("agents").select(
        "id, name, ticker, status, token_address, agent_wallet, "
        "tg_verified, token_deployed, total_posts, total_trades, "
        "total_fees_bnb, error_message, claim_code"
    ).eq("id", agent_id).execute()

    if not resp.data:
        raise HTTPException(status_code=404, detail="Agent not found")

    a = resp.data[0]

    # Get bot username
    bot_username = None
    runtime = scheduler.get(agent_id)
    if runtime:
        bot_username = None  # could look up from bot_pool if needed

    return AgentStatusResponse(
        agent_id=a["id"],
        name=a["name"],
        ticker=a["ticker"],
        status=a["status"],
        token_address=a.get("token_address"),
        agent_wallet=a.get("agent_wallet"),
        bot_username=bot_username,
        claim_code=a.get("claim_code"),
        tg_verified=a.get("tg_verified", False),
        token_deployed=a.get("token_deployed", False),
        total_posts=a.get("total_posts", 0),
        total_trades=a.get("total_trades", 0),
        total_fees_bnb=float(a.get("total_fees_bnb", 0)),
        error_message=a.get("error_message"),
    )


@app.post("/agent/{agent_id}/pause")
async def pause_agent(agent_id: str):
    runtime = scheduler.get(agent_id)
    if not runtime:
        raise HTTPException(status_code=404, detail="Agent not running")
    await runtime.pause()
    return {"status": "paused"}


@app.post("/agent/{agent_id}/resume")
async def resume_agent(agent_id: str):
    runtime = scheduler.get(agent_id)
    if not runtime:
        raise HTTPException(status_code=404, detail="Agent not running")
    await runtime.resume()
    return {"status": "active"}


@app.post("/agent/{agent_id}/delete")
async def delete_agent(agent_id: str):
    db = get_db()

    # Get bot assignment to release
    resp = db.table("agents").select("tg_bot_id").eq("id", agent_id).execute()
    if resp.data and resp.data[0].get("tg_bot_id"):
        from core.telegram import release_bot_to_pool
        await release_bot_to_pool(db, resp.data[0]["tg_bot_id"])

    # Mark deleted
    db.table("agents").update({"status": "deleted"}).eq("id", agent_id).execute()
    scheduler.unregister(agent_id)
    return {"status": "deleted"}


@app.get("/agent/{agent_id}/stats")
async def agent_stats(agent_id: str):
    db = get_db()
    agent_resp = db.table("agents").select(
        "name, ticker, status, total_posts, total_trades, total_fees_bnb, last_active_at"
    ).eq("id", agent_id).execute()

    if not agent_resp.data:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent = agent_resp.data[0]

    posts = db.table("agent_posts").select("post_type, created_at")\
        .eq("agent_id", agent_id).order("created_at", desc=True).limit(10).execute()

    trades = db.table("agent_trades").select("direction, amount_bnb, success, created_at")\
        .eq("agent_id", agent_id).order("created_at", desc=True).limit(10).execute()

    fees = db.table("fee_records").select("owner_cut_bnb, paid_out")\
        .eq("agent_id", agent_id).execute()

    total_fees = sum(f["owner_cut_bnb"] for f in (fees.data or []))
    pending_fees = sum(f["owner_cut_bnb"] for f in (fees.data or []) if not f["paid_out"])

    return {
        "agent": agent,
        "recent_posts": posts.data or [],
        "recent_trades": trades.data or [],
        "fees": {
            "total_earned_bnb": total_fees,
            "pending_payout_bnb": pending_fees,
        },
    }


@app.post("/verify-channel")
async def verify_channel(body: dict):
    """
    Pre-launch channel verification.
    Wizard calls this before deploy step to confirm bot has admin access.
    """
    from core.telegram import verify_bot_is_admin
    bot_token = os.environ["PLATFORM_BOT_TOKEN"]
    channel_link = body.get("channel_link", "")
    if not channel_link:
        raise HTTPException(status_code=400, detail="channel_link required")

    is_admin = await verify_bot_is_admin(bot_token, channel_link)
    return {"verified": is_admin, "channel_link": channel_link}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """
    Telegram sends updates here for @4GentBot.
    Set webhook via: https://api.telegram.org/bot{TOKEN}/setWebhook?url=https://your-api.railway.app/webhook/telegram
    """
    update = await request.json()
    db = get_db()
    asyncio.create_task(handle_owner_command(update, db))
    return {"ok": True}


@app.get("/admin/agents")
async def list_agents():
    """Admin — list all agents and their status."""
    db = get_db()
    resp = db.table("agents").select(
        "id, name, ticker, archetype, status, token_address, total_posts, total_trades, created_at"
    ).neq("status", "deleted").order("created_at", desc=True).execute()
    return {"agents": resp.data or [], "total": len(resp.data or [])}


@app.get("/admin/bot-pool")
async def bot_pool_status():
    """Admin — view bot pool availability."""
    db = get_db()
    resp = db.table("bot_pool").select("bot_username, available, assigned_agent_id, assigned_at").execute()
    bots = resp.data or []
    return {
        "total": len(bots),
        "available": sum(1 for b in bots if b["available"]),
        "assigned": sum(1 for b in bots if not b["available"]),
        "bots": bots,
    }


@app.post("/admin/seed-bots")
async def seed_bots(body: dict):
    """
    Seed bot pool from JSON payload.
    POST {"bots": [{"username": "@FourGentAgent1_bot", "token": "..."}]}
    """
    db = get_db()
    bots = body.get("bots", [])
    if not bots:
        raise HTTPException(status_code=400, detail="No bots provided")

    inserted = []
    for b in bots:
        username = b.get("username", "").strip()
        token = b.get("token", "").strip()
        if not username or not token:
            continue
        # Upsert — safe to call multiple times
        db.table("bot_pool").upsert({
            "bot_username": username,
            "bot_token": token,
            "available": True,
        }, on_conflict="bot_username").execute()
        inserted.append(username)

    return {"seeded": inserted, "count": len(inserted)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "server:app",
        host="0.0.0.0",
        port=int(os.environ.get("PORT", 8000)),
        reload=os.environ.get("ENVIRONMENT") == "development",
    )
