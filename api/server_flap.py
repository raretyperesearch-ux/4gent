"""
4Gent — FastAPI Backend (Flap.sh edition)

Launch flow:
  1. POST /meta/prepare     — create agent record, return agent_id + metadata URL
  2. MetaMask calls newTokenV2 on Flap portal (frontend only, no backend)
  3. POST /launch/confirm   — frontend sends tx_hash, backend parses + runs pipeline
  4. GET  /agent/{id}       — poll for status

No platform wallet. No payment relay. No four.meme auth.
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

from fastapi import FastAPI, HTTPException, BackgroundTasks, Request, Header, Depends
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from supabase import create_client, Client

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages"))

from core.launch import run_launch, LaunchConfig
from core.monitor import FlapMonitor
from core.scheduler import AgentScheduler, AgentRuntime
from core.telegram import handle_owner_command

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
)
logger = logging.getLogger("4gent.api")

# ── Singletons ────────────────────────────────────────────────────────────────
scheduler = AgentScheduler()
monitor: Optional[FlapMonitor] = None
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
    global monitor
    db = get_db()

    await scheduler.load_from_supabase(db)
    logger.info("Loaded %d active agents", scheduler.count)

    asyncio.create_task(scheduler.start_retry_loop())

    monitor = FlapMonitor()
    monitor.register(scheduler.on_token_event)
    _monitor_task = asyncio.create_task(monitor.start())
    _monitor_task.add_done_callback(
        lambda t: logger.error("Flap monitor crashed: %s", t.exception()) if t.exception() else None
    )
    logger.info("Flap monitor started")

    yield

    if monitor:
        await monitor.stop()
    logger.info("4Gent shutdown complete")


app = FastAPI(title="4Gent API", version="2.0.0", lifespan=lifespan)

@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    logger.error("422 on %s: %s", request.url.path, exc.errors())
    return JSONResponse(status_code=422, content={"detail": exc.errors()})

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Models ────────────────────────────────────────────────────────────────────

class PrepareRequest(BaseModel):
    name:            str   = Field(..., min_length=1, max_length=50)
    ticker:          str   = Field(..., min_length=1, max_length=10)
    archetype:       str   = Field(default="schemer")
    prompt:          str   = Field(default="", max_length=1000)
    image_url:       str   = ""           # base64 data URL or https URL
    tg_channel_link: str   = Field(default="", min_length=1)
    owner_wallet:    str   = Field(default="", min_length=1)
    trading_enabled: bool  = False
    max_trade_bnb:   float = Field(default=0.1, ge=0, le=10)
    daily_limit_bnb: float = Field(default=1.0, ge=0, le=50)
    stop_loss_pct:   float = Field(default=50.0, ge=0, le=100)
    raise_amount_bnb:float = Field(default=0.0, ge=0, le=20)


class PrepareResponse(BaseModel):
    agent_id:    str
    meta_url:    str   # GET /meta/{agent_id} — passed as `meta` param to newTokenV2


class ConfirmRequest(BaseModel):
    tx_hash: str


class LaunchResponse(BaseModel):
    agent_id: str
    status:   str
    message:  str


class AgentStatusResponse(BaseModel):
    agent_id:       str
    name:           str
    ticker:         str
    status:         str
    token_address:  Optional[str]
    agent_wallet:   Optional[str]
    bot_username:   Optional[str]
    claim_code:     Optional[str]
    tg_verified:    bool
    token_deployed: bool
    total_posts:    int
    total_trades:   int
    total_fees_bnb: float
    error_message:  Optional[str]


# ── Admin auth ────────────────────────────────────────────────────────────────

def _verify_admin(x_admin_key: str = Header(default="")) -> None:
    expected = os.environ.get("ADMIN_API_KEY", "")
    if not expected or x_admin_key != expected:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Routes ────────────────────────────────────────────────────────────────────

@app.get("/health")
async def health():
    return {
        "status":        "ok",
        "active_agents": scheduler.count,
        "monitor_running": monitor is not None and monitor._running,
        "timestamp":     datetime.utcnow().isoformat(),
    }


@app.post("/meta/prepare", response_model=PrepareResponse)
async def meta_prepare(req: PrepareRequest):
    """
    Step 1 — Create agent record in DB, return agent_id + metadata URL.
    Frontend passes meta_url as the `meta` param to Flap's newTokenV2.
    """
    db = get_db()
    agent_id = str(uuid.uuid4())

    db.table("agents").insert({
        "id":              agent_id,
        "name":            req.name,
        "ticker":          req.ticker,
        "archetype":       req.archetype,
        "prompt":          req.prompt,
        "image_url":       req.image_url or "",
        "tg_channel_link": req.tg_channel_link,
        "owner_wallet":    req.owner_wallet,
        "trading_enabled": req.trading_enabled,
        "max_trade_bnb":   req.max_trade_bnb,
        "daily_limit_bnb": req.daily_limit_bnb,
        "stop_loss_pct":   req.stop_loss_pct,
        "raise_amount_bnb":req.raise_amount_bnb,
        "status":          "pending",
    }).execute()

    api_base = os.environ.get("API_BASE_URL", "https://4gent-api.railway.app")
    meta_url = f"{api_base}/meta/{agent_id}"

    return PrepareResponse(agent_id=agent_id, meta_url=meta_url)


@app.get("/meta/{agent_id}")
async def get_meta(agent_id: str):
    """
    Serves token metadata JSON for the Flap portal's `meta` field.
    Standard token metadata format.
    """
    db = get_db()
    row = db.table("agents").select("name, ticker, prompt, image_url").eq("id", agent_id).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Agent not found")
    a = row.data[0]
    return {
        "name":        a["name"],
        "symbol":      a["ticker"],
        "description": a["prompt"],
        "image":       a["image_url"],
    }


@app.post("/launch/confirm/{agent_id}", response_model=LaunchResponse)
async def launch_confirm(agent_id: str, req: ConfirmRequest, background_tasks: BackgroundTasks):
    """
    Step 2 — Frontend confirms the MetaMask tx hash.
    Backend parses TokenCreated event + runs post-deploy pipeline.
    """
    if not req.tx_hash or not req.tx_hash.startswith("0x"):
        raise HTTPException(status_code=400, detail="tx_hash required (must start with 0x)")

    db = get_db()
    row = db.table("agents").select("*").eq("id", agent_id).execute()
    if not row.data:
        raise HTTPException(status_code=404, detail="Agent not found")

    agent = row.data[0]
    if agent["status"] not in ("pending",):
        raise HTTPException(
            status_code=400,
            detail=f"Agent status is '{agent['status']}' — expected pending"
        )

    config = LaunchConfig(
        agent_id=        agent_id,
        name=            agent["name"],
        ticker=          agent["ticker"],
        archetype=       agent["archetype"],
        prompt=          agent["prompt"] or "",
        image_url=       agent["image_url"] or "",
        tg_channel_link= agent["tg_channel_link"] or "",
        owner_wallet=    agent["owner_wallet"] or "",
        trading_enabled= agent["trading_enabled"],
        max_trade_bnb=   float(agent["max_trade_bnb"]),
        daily_limit_bnb= float(agent["daily_limit_bnb"]),
        stop_loss_pct=   float(agent.get("stop_loss_pct", 50.0)),
        raise_amount_bnb=float(agent["raise_amount_bnb"]),
    )

    background_tasks.add_task(_run_launch, config, req.tx_hash)

    return LaunchResponse(
        agent_id=agent_id,
        status="launching",
        message=f"Launch started. Poll /agent/{agent_id} for status.",
    )


async def _run_launch(config: LaunchConfig, tx_hash: str) -> None:
    """Background task — runs pipeline then registers runtime in scheduler."""
    result = await run_launch(config, tx_hash)
    if result.success:
        db = get_db()
        row = db.table("agents").select(
            "id, name, ticker, archetype, prompt, tg_channel_link, "
            "trading_enabled, max_trade_bnb, daily_limit_bnb, stop_loss_pct, "
            "agent_wallet, agent_wallet_enc, tg_bot_id"
        ).eq("id", config.agent_id).execute()
        if row.data:
            agent = row.data[0]
            bot_token = ""
            if agent.get("tg_bot_id"):
                bot_row = db.table("bot_pool").select("bot_token").eq(
                    "id", agent["tg_bot_id"]
                ).execute()
                if bot_row.data:
                    bot_token = bot_row.data[0]["bot_token"]
            runtime = AgentRuntime(
                agent_id=       agent["id"],
                name=           agent["name"],
                ticker=         agent["ticker"],
                archetype=      agent["archetype"],
                prompt=         agent["prompt"],
                bot_token=      bot_token,
                tg_channel=     agent["tg_channel_link"],
                trading_enabled=agent["trading_enabled"],
                max_trade_bnb=  float(agent["max_trade_bnb"]),
                daily_limit_bnb=float(agent["daily_limit_bnb"]),
                stop_loss_pct=  float(agent.get("stop_loss_pct", 50.0)),
                agent_wallet=   agent["agent_wallet"],
                agent_wallet_enc=agent["agent_wallet_enc"],
                supabase=       db,
            )
            scheduler.register(runtime)
        logger.info("Agent %s launched and registered", config.agent_id)
    else:
        logger.error("Launch failed for %s: %s", config.agent_id, result.error)


@app.get("/agent/{agent_id}", response_model=AgentStatusResponse)
async def get_agent(agent_id: str):
    db = get_db()
    resp = db.table("agents").select(
        "id, name, ticker, status, token_address, agent_wallet, "
        "tg_verified, token_deployed, total_posts, total_trades, "
        "total_fees_bnb, error_message, claim_code, tg_bot_id"
    ).eq("id", agent_id).execute()

    if not resp.data:
        raise HTTPException(status_code=404, detail="Agent not found")

    a = resp.data[0]
    bot_username = None
    if a.get("tg_bot_id"):
        bot_resp = db.table("bot_pool").select("bot_username").eq("id", a["tg_bot_id"]).execute()
        if bot_resp.data:
            bot_username = bot_resp.data[0]["bot_username"]

    return AgentStatusResponse(
        agent_id=      a["id"],
        name=          a["name"],
        ticker=        a["ticker"],
        status=        a["status"],
        token_address= a.get("token_address"),
        agent_wallet=  a.get("agent_wallet"),
        bot_username=  bot_username,
        claim_code=    a.get("claim_code"),
        tg_verified=   a.get("tg_verified", False),
        token_deployed=a.get("token_deployed", False),
        total_posts=   a.get("total_posts", 0),
        total_trades=  a.get("total_trades", 0),
        total_fees_bnb=float(a.get("total_fees_bnb", 0)),
        error_message= a.get("error_message"),
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
    resp = db.table("agents").select("tg_bot_id").eq("id", agent_id).execute()
    if resp.data and resp.data[0].get("tg_bot_id"):
        from core.telegram import release_bot_to_pool
        await release_bot_to_pool(db, resp.data[0]["tg_bot_id"])
    db.table("agents").update({"status": "deleted"}).eq("id", agent_id).execute()
    scheduler.unregister(agent_id)
    return {"status": "deleted"}


@app.post("/verify-channel")
async def verify_channel(body: dict):
    import httpx
    channel_link = body.get("channel_link", "")
    if not channel_link:
        raise HTTPException(status_code=400, detail="channel_link required")
    handle = channel_link.replace("https://t.me/", "").replace("t.me/", "").strip("/").lstrip("@")
    try:
        async with httpx.AsyncClient(timeout=8) as client:
            r = await client.get(f"https://t.me/{handle}")
            exists = r.status_code == 200 and "tgme_page" in r.text
        return {"verified": exists, "channel_link": channel_link}
    except Exception:
        return {"verified": False, "channel_link": channel_link}


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    update = await request.json()
    db = get_db()
    task = asyncio.create_task(handle_owner_command(update, db, scheduler))
    task.add_done_callback(
        lambda t: logger.error("handle_owner_command raised: %s", t.exception()) if t.exception() else None
    )
    return {"ok": True}


@app.get("/admin/agents")
async def list_agents(_: None = Depends(_verify_admin)):
    db = get_db()
    resp = db.table("agents").select(
        "id, name, ticker, archetype, status, token_address, total_posts, total_trades, created_at"
    ).neq("status", "deleted").order("created_at", desc=True).execute()
    return {"agents": resp.data or [], "total": len(resp.data or [])}


@app.get("/admin/bot-pool")
async def bot_pool_status(_: None = Depends(_verify_admin)):
    db = get_db()
    resp = db.table("bot_pool").select("bot_username, available, assigned_agent_id, assigned_at").execute()
    bots = resp.data or []
    return {
        "total":    len(bots),
        "available":sum(1 for b in bots if b["available"]),
        "assigned": sum(1 for b in bots if not b["available"]),
        "bots":     bots,
    }


@app.post("/admin/seed-bots")
async def seed_bots(body: dict, _: None = Depends(_verify_admin)):
    db = get_db()
    bots = body.get("bots", [])
    if not bots:
        raise HTTPException(status_code=400, detail="No bots provided")
    inserted = []
    for b in bots:
        username = b.get("username", "").strip()
        token    = b.get("token", "").strip()
        if not username or not token:
            continue
        existing = db.table("bot_pool").select("id").eq("bot_username", username).execute()
        if existing.data:
            db.table("bot_pool").update({"bot_token": token}).eq("bot_username", username).execute()
        else:
            db.table("bot_pool").insert({"bot_username": username, "bot_token": token, "available": True}).execute()
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
