"""
4Gent — Telegram Bot Pool Manager
Bot pool assignment, channel verification, posting, PFP, owner commands.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx
from supabase import Client

logger = logging.getLogger(__name__)

TG_API = "https://api.telegram.org/bot{token}/{method}"

def PLATFORM_BOT_TOKEN() -> str:
    """P-09: raises clear error if env var missing rather than silent KeyError at call time."""
    token = os.environ.get("PLATFORM_BOT_TOKEN", "")
    if not token:
        raise RuntimeError(
            "PLATFORM_BOT_TOKEN env var not set. "
            "Create a Telegram bot via @BotFather and set this var in Railway."
        )
    return token


async def _tg(token: str, method: str, **kwargs) -> dict:
    url = TG_API.format(token=token, method=method)
    async with httpx.AsyncClient(timeout=15) as client:
        r = await client.post(url, json=kwargs)
        r.raise_for_status()
        data = r.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram {method} failed: {data.get('description', data)}")
        return data["result"]


def _normalize_handle(channel_link: str) -> str:
    handle = channel_link.replace("https://t.me/", "").replace("t.me/", "").strip("/")
    return handle if handle.startswith("@") else f"@{handle}"


async def verify_bot_is_admin(bot_token: str, channel_link: str) -> bool:
    """Check that the bot has admin + post_messages rights in the channel."""
    try:
        handle = _normalize_handle(channel_link)
        me = await _tg(bot_token, "getMe")
        bot_id = me["id"]
        member = await _tg(bot_token, "getChatMember", chat_id=handle, user_id=bot_id)
        return (
            member.get("status") == "administrator"
            and member.get("can_post_messages", False)
        )
    except Exception as e:
        logger.warning("Admin check failed for %s: %s", channel_link, e)
        return False


async def set_bot_photo(bot_token: str, image_url: str) -> bool:
    """
    NOTE (P-07): Telegram Bot API has no method to set a BOT's own profile photo
    programmatically. Bot profile photos must be set via @BotFather (/setuserpic).
    This function logs a reminder and returns True to continue the launch pipeline.
    """
    logger.info(
        "Bot profile photo cannot be set via API — please set it manually via @BotFather "
        "/setuserpic. Token image: %s", image_url
    )
    return True


async def post_to_channel(bot_token: str, channel_link: str, text: str) -> bool:
    """Send a message to the agent's Telegram channel."""
    try:
        handle = _normalize_handle(channel_link)
        await _tg(bot_token, "sendMessage", chat_id=handle, text=text, parse_mode="HTML")
        return True
    except Exception as e:
        logger.error("Post to channel failed (%s): %s", channel_link, e)
        return False


async def assign_bot_from_pool(supabase: Client, agent_id: str) -> Optional[dict]:
    """
    B-14 fix: Atomically claim a bot from the pool using a Postgres RPC function
    (claim_bot_from_pool) to avoid TOCTOU race between concurrent launches.
    Falls back to two-step query if RPC not available.
    Returns dict with id, bot_username, bot_token or None if exhausted.
    """
    try:
        # Atomic claim via RPC (defined in schema.sql)
        resp = supabase.rpc("claim_bot_from_pool", {"p_agent_id": agent_id}).execute()
        if resp.data:
            bot = resp.data[0]
            logger.info("Assigned bot @%s to agent %s (atomic)", bot["bot_username"], agent_id)
            return bot
        logger.error("Bot pool exhausted")
        return None
    except Exception as e:
        logger.warning("Atomic bot claim RPC failed (%s) — falling back to two-step", e)

    # Fallback two-step (non-atomic, acceptable for single-instance deploys)
    try:
        resp = supabase.table("bot_pool")\
            .select("id, bot_username, bot_token")\
            .eq("available", True)\
            .is_("assigned_agent_id", "null")\
            .limit(1)\
            .execute()
        if not resp.data:
            logger.error("Bot pool exhausted (fallback)")
            return None
        bot = resp.data[0]
        supabase.table("bot_pool").update({
            "available": False,
            "assigned_agent_id": agent_id,
            "assigned_at": "now()",
        }).eq("id", bot["id"]).execute()
        logger.info("Assigned bot @%s to agent %s (fallback)", bot["bot_username"], agent_id)
        return bot
    except Exception as e:
        logger.error("Bot pool assignment failed: %s", e)
        return None


async def release_bot_to_pool(supabase: Client, bot_id: str) -> None:
    """Return a bot to the pool when an agent is deleted."""
    supabase.table("bot_pool").update({
        "available": True,
        "assigned_agent_id": None,
        "assigned_at": None,
    }).eq("id", bot_id).execute()
    logger.info("Bot %s returned to pool", bot_id)


async def handle_owner_command(update: dict, supabase: Client, scheduler=None) -> None:
    """
    Handle incoming Telegram updates to @4GentBot.
    Processes claim codes and owner commands (/pause /resume /stats etc).
    """
    message = update.get("message", {})
    text = message.get("text", "").strip()
    tg_user_id = str(message.get("from", {}).get("id", ""))
    chat_id = message.get("chat", {}).get("id")

    if not text or not chat_id:
        return

    bot_token = PLATFORM_BOT_TOKEN()

    # ── Claim code flow ───────────────────────────────────────────────────────
    # Format: XXX-YYY-ZZZ
    import re
    if re.match(r'^[A-Z0-9]{3}-[A-Z0-9]{3}-[A-Z0-9]{3}$', text):
        from datetime import datetime
        resp = supabase.table("agents")\
            .select("id, name, owner_claimed, claim_code_expires")\
            .eq("claim_code", text)\
            .execute()

        if not resp.data:
            await _tg(bot_token, "sendMessage", chat_id=chat_id,
                      text="❌ Invalid or expired claim code.")
            return

        agent = resp.data[0]

        if agent["owner_claimed"]:
            await _tg(bot_token, "sendMessage", chat_id=chat_id,
                      text="❌ This agent has already been claimed.")
            return

        expires = datetime.fromisoformat(agent["claim_code_expires"].replace("Z", "+00:00"))
        if datetime.utcnow().replace(tzinfo=expires.tzinfo) > expires:
            await _tg(bot_token, "sendMessage", chat_id=chat_id,
                      text="❌ Claim code expired. Use /tglink in the dashboard to get a new one.")
            return

        supabase.table("agents").update({
            "owner_claimed": True,
            "claim_code": None,
            "claim_code_expires": None,  # B-17: clear expiry after claim
        }).eq("id", agent["id"]).execute()

        supabase.table("owner_commands").insert({
            "agent_id": agent["id"],
            "command": "claim",
            "tg_user_id": tg_user_id,
        }).execute()

        await _tg(bot_token, "sendMessage", chat_id=chat_id,
                  text=f"✅ <b>{agent['name']}</b> claimed.\n\nYour owner commands:\n"
                       "/pause — pause the agent\n/resume — resume the agent\n"
                       "/stats — view stats\n/fees — view fee earnings",
                  parse_mode="HTML")
        return

    # ── Owner commands ────────────────────────────────────────────────────────
    parts = text.split()
    cmd = parts[0].lower()

    # Find agent owned by this Telegram user
    # (we store tg_user_id via first claim)
    # Simplified: look up by most recent command from this user
    if cmd in ("/pause", "/resume", "/stats", "/fees"):
        recent = supabase.table("owner_commands")\
            .select("agent_id")\
            .eq("tg_user_id", tg_user_id)\
            .order("created_at", desc=True)\
            .limit(1)\
            .execute()

        if not recent.data:
            await _tg(bot_token, "sendMessage", chat_id=chat_id,
                      text="❌ No agent found. Send your claim code first.")
            return

        agent_id = recent.data[0]["agent_id"]
        agent_resp = supabase.table("agents").select("*").eq("id", agent_id).execute()
        agent = agent_resp.data[0] if agent_resp.data else None

        if not agent:
            return

        if cmd == "/pause":
            supabase.table("agents").update({"status": "paused"}).eq("id", agent_id).execute()
            # N-06 fix: also pause the in-memory runtime so it stops acting on events immediately
            if scheduler:
                runtime = scheduler.get(agent_id)
                if runtime:
                    runtime.paused = True
            supabase.table("owner_commands").insert({
                "agent_id": agent_id, "command": "/pause", "tg_user_id": tg_user_id
            }).execute()
            await _tg(bot_token, "sendMessage", chat_id=chat_id,
                      text=f"⏸ <b>{agent['name']}</b> paused.", parse_mode="HTML")

        elif cmd == "/resume":
            supabase.table("agents").update({"status": "active"}).eq("id", agent_id).execute()
            # N-06 fix: also resume the in-memory runtime
            if scheduler:
                runtime = scheduler.get(agent_id)
                if runtime:
                    runtime.paused = False
            supabase.table("owner_commands").insert({
                "agent_id": agent_id, "command": "/resume", "tg_user_id": tg_user_id
            }).execute()
            await _tg(bot_token, "sendMessage", chat_id=chat_id,
                      text=f"▶️ <b>{agent['name']}</b> resumed.", parse_mode="HTML")

        elif cmd == "/stats":
            await _tg(bot_token, "sendMessage", chat_id=chat_id,
                      text=(
                          f"📊 <b>{agent['name']}</b> Stats\n\n"
                          f"Status: {agent['status'].upper()}\n"
                          f"Token: ${agent['ticker']} — <code>{agent.get('token_address', 'pending')[:10]}...</code>\n"
                          f"Total posts: {agent['total_posts']}\n"
                          f"Total trades: {agent['total_trades']}\n"
                          f"Total fees: {agent['total_fees_bnb']} BNB"
                      ), parse_mode="HTML")

        elif cmd == "/fees":
            fees_resp = supabase.table("fee_records")\
                .select("owner_cut_bnb, paid_out")\
                .eq("agent_id", agent_id)\
                .execute()
            fees = fees_resp.data or []
            total = sum(f["owner_cut_bnb"] for f in fees)
            unpaid = sum(f["owner_cut_bnb"] for f in fees if not f["paid_out"])
            await _tg(bot_token, "sendMessage", chat_id=chat_id,
                      text=(
                          f"💰 <b>{agent['name']}</b> Fees\n\n"
                          f"Total earned: {total:.6f} BNB\n"
                          f"Pending payout: {unpaid:.6f} BNB\n\n"
                          f"Payouts run daily."
                      ), parse_mode="HTML")
