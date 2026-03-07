"""
4Gent — Agent Launch Pipeline (Flap.sh edition)

Flow:
  1. Frontend (MetaMask) calls newTokenV2 on Flap portal directly
  2. Frontend gets tx_hash, POSTs to /launch/confirm with agent_id + tx_hash
  3. Backend parses TokenCreated event, runs post-deploy pipeline

No platform wallet. No auth API. No payment relay.
User pays their own gas (~$0.50).
"""
from __future__ import annotations

import asyncio
import logging
import os
import secrets
import string
import sys
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from supabase import create_client, Client

logger = logging.getLogger(__name__)

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages"))


def get_supabase() -> Client:
    return create_client(
        os.environ["SUPABASE_URL"],
        os.environ["SUPABASE_SERVICE_KEY"],
    )


@dataclass
class LaunchConfig:
    agent_id: str
    name: str
    ticker: str
    archetype: str
    prompt: str
    image_url: str
    tg_channel_link: str
    owner_wallet: str
    trading_enabled: bool
    max_trade_bnb: float = 0.1
    daily_limit_bnb: float = 1.0
    stop_loss_pct: float = 50.0
    raise_amount_bnb: float = 0.0


@dataclass
class LaunchResult:
    success: bool
    agent_id: str
    agent_wallet: str = ""
    token_address: str = ""
    token_tx_hash: str = ""
    bot_username: str = ""
    claim_code: str = ""
    error: str = ""


def _generate_claim_code() -> str:
    chars = string.ascii_uppercase + string.digits
    parts = ["".join(secrets.choice(chars) for _ in range(3)) for _ in range(3)]
    return "-".join(parts)


async def _update_agent(supabase: Client, agent_id: str, **fields) -> None:
    fields["updated_at"] = datetime.utcnow().isoformat()
    supabase.table("agents").update(fields).eq("id", agent_id).execute()


async def run_launch(config: LaunchConfig, tx_hash: str) -> LaunchResult:
    """
    Post-deploy pipeline — runs after user's MetaMask tx is confirmed.

    Steps:
      1. Parse TokenCreated event from tx_hash → get token address
      2. Create agent trading wallet
      3. Assign bot from pool
      4. Generate + post intro posts via Claude
      5. Set status=active, generate claim code
    """
    from flap.onchain import get_w3, parse_token_created_receipt
    from .wallet import create_agent_wallet
    from .telegram import assign_bot_from_pool, post_to_channel
    from .claude_brain import ClaudeBrain

    supabase = get_supabase()
    result = LaunchResult(success=False, agent_id=config.agent_id)

    try:
        await _update_agent(supabase, config.agent_id, status="launching")

        # 1. Parse token address from tx
        logger.info("[%s] Step 1/4 — Parsing TokenCreated from tx %s", config.name, tx_hash[:12])
        bsc_rpc = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/")

        loop = asyncio.get_running_loop()
        w3 = get_w3(bsc_rpc)
        token_info = await loop.run_in_executor(
            None,
            lambda: parse_token_created_receipt(w3, tx_hash)
        )

        if not token_info:
            raise RuntimeError(f"Could not parse TokenCreated from tx {tx_hash}")

        token_address = token_info["token_address"]
        result.token_address = token_address
        result.token_tx_hash = tx_hash

        await _update_agent(supabase, config.agent_id,
                            token_address=token_address,
                            token_tx_hash=tx_hash,
                            token_deployed=True)
        logger.info("[%s] Token deployed: %s", config.name, token_address)

        # 2. Create agent trading wallet
        logger.info("[%s] Step 2/4 — Creating agent trading wallet", config.name)
        wallet = create_agent_wallet()
        result.agent_wallet = wallet.address
        await _update_agent(supabase, config.agent_id,
                            agent_wallet=wallet.address,
                            agent_wallet_enc=wallet.encrypted_key)

        # 3. Assign bot from pool
        logger.info("[%s] Step 3/4 — Assigning Telegram bot", config.name)
        bot = await assign_bot_from_pool(supabase, config.agent_id)
        if not bot:
            raise RuntimeError("Bot pool exhausted — add more bots via /admin/seed-bots")
        result.bot_username = bot["bot_username"]
        _assigned_bot_id = bot["id"]
        await _update_agent(supabase, config.agent_id, tg_bot_id=bot["id"])

        # 4. Generate intro posts
        logger.info("[%s] Step 4/4 — Generating intro posts", config.name)
        brain = ClaudeBrain(
            archetype=config.archetype,
            agent_name=config.name,
            agent_ticker=config.ticker,
            custom_prompt=config.prompt,
            trading_enabled=config.trading_enabled,
            max_trade_bnb=float(config.max_trade_bnb),
        )
        try:
            posts = await brain.generate_intro_posts()
        finally:
            await brain.close()

        for i, text in enumerate(posts):
            await asyncio.sleep(1.5)
            supabase.table("agent_posts").insert({
                "agent_id":  config.agent_id,
                "post_type": "intro",
                "content":   text,
                "posted":    False,
            }).execute()
            posted = await post_to_channel(bot["bot_token"], config.tg_channel_link, text)
            if posted:
                supabase.table("agent_posts").update({"posted": True})\
                    .eq("agent_id", config.agent_id)\
                    .eq("content", text)\
                    .execute()
                logger.info("[%s] Intro post %d/3 sent", config.name, i + 1)
            else:
                logger.info("[%s] Intro post %d/3 saved — retry when bot added to channel", config.name, i + 1)

        # Finalize
        claim_code = _generate_claim_code()
        result.claim_code = claim_code
        await _update_agent(supabase, config.agent_id,
                            status="active",
                            claim_code=claim_code,
                            claim_code_expires=(datetime.utcnow() + timedelta(hours=24)).isoformat(),
                            last_active_at=datetime.utcnow().isoformat())

        result.success = True
        logger.info("[%s] LAUNCH COMPLETE ✓ token=%s bot=%s", config.name, token_address, bot["bot_username"])
        return result

    except Exception as e:
        logger.error("[%s] Launch failed: %s", config.name, e, exc_info=True)
        await _update_agent(supabase, config.agent_id, status="error", error_message=str(e))
        result.error = str(e)
        try:
            bot_id_to_release = locals().get("_assigned_bot_id")
            if bot_id_to_release:
                from .telegram import release_bot_to_pool
                await release_bot_to_pool(supabase, bot_id_to_release)
        except Exception as release_err:
            logger.warning("[%s] Failed to release bot: %s", config.name, release_err)
        return result
