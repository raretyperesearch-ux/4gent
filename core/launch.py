"""
4Gent — Agent Launch Pipeline
Full pipeline: wallet → ERC-8004 → token deploy → Telegram → claim code → Supabase
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

# Add packages to path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "packages", "fourmeme"))


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


async def _deploy_token(config: LaunchConfig, wallet_address: str, private_key: str) -> tuple[str, str]:
    """Deploy token on four.meme. Returns (token_address, tx_hash)."""
    from fourmeme.auth import FourMemeAuth
    from fourmeme.client import FourMemeClient
    from fourmeme.onchain import BSCChain

    auth = FourMemeAuth(private_key=private_key)
    client = FourMemeClient(auth)
    chain = BSCChain(
        private_key=private_key,
        rpc_url=os.environ.get("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/"),
    )

    # Validate image_url is a proper URL (not base64 or placeholder)
    if not config.image_url.startswith("http"):
        raise ValueError(f"image_url must be a CDN URL, got: {config.image_url[:50]}")

    try:
        create_result = await client.create_token(
            name=config.name,
            short_name=config.ticker,
            description=config.prompt[:200],
            img_url=config.image_url,
            pre_sale=config.raise_amount_bnb,
            telegram=config.tg_channel_link,
        )
        tx_hash = chain.submit_create_token(
            create_arg=create_result["createArg"],
            signature=create_result["signature"],
            raise_amount_bnb=config.raise_amount_bnb,
        )
        receipt = chain.wait_for_receipt(tx_hash)
        token_address = receipt.get("contractAddress") or ""
        if not token_address and receipt.get("logs"):
            token_address = receipt["logs"][0].get("address", "")
        return token_address, tx_hash
    finally:
        await client.close()


async def launch_agent(config: LaunchConfig) -> LaunchResult:
    """Full agent launch pipeline."""
    from .wallet import create_agent_wallet
    from .telegram import assign_bot_from_pool, verify_bot_is_admin, set_bot_photo, post_to_channel
    from .claude_brain import ClaudeBrain
    from .erc8004 import register_agent_wallet

    supabase = get_supabase()
    result = LaunchResult(success=False, agent_id=config.agent_id)

    try:
        # 1. Create agent wallet
        logger.info("[%s] Step 1/8 — Creating agent wallet", config.name)
        await _update_agent(supabase, config.agent_id, status="launching")
        wallet = create_agent_wallet()
        result.agent_wallet = wallet.address
        await _update_agent(supabase, config.agent_id,
                            agent_wallet=wallet.address,
                            agent_wallet_enc=wallet.encrypted_key)

        # 2. ERC-8004 registration
        logger.info("[%s] Step 2/8 — ERC-8004 registration", config.name)
        erc = await register_agent_wallet(
            wallet_address=wallet.address,
            private_key=wallet.private_key,
            agent_name=config.name,
            agent_id=config.agent_id,
        )
        await _update_agent(supabase, config.agent_id,
                            erc8004_registered=not erc.get("skipped", False),
                            erc8004_tx_hash=erc.get("tx_hash"))

        # 3. Deploy token
        logger.info("[%s] Step 3/8 — Deploying $%s on four.meme", config.name, config.ticker)
        token_address, tx_hash = await _deploy_token(config, wallet.address, wallet.private_key)
        result.token_address = token_address
        result.token_tx_hash = tx_hash
        await _update_agent(supabase, config.agent_id,
                            token_address=token_address,
                            token_tx_hash=tx_hash,
                            token_deployed=True)

        # 4. Assign bot from pool
        logger.info("[%s] Step 4/8 — Assigning Telegram bot", config.name)
        bot = await assign_bot_from_pool(supabase, config.agent_id)
        if not bot:
            raise RuntimeError("Bot pool exhausted — add more bots via @BotFather")
        result.bot_username = bot["bot_username"]

        # 5. Set bot PFP
        logger.info("[%s] Step 5/8 — Setting bot profile photo", config.name)
        await set_bot_photo(bot["bot_token"], config.image_url)

        # 6. Verify channel admin
        logger.info("[%s] Step 6/8 — Verifying Telegram channel", config.name)
        is_admin = await verify_bot_is_admin(bot["bot_token"], config.tg_channel_link)
        if not is_admin:
            raise RuntimeError(
                f"Bot @{bot['bot_username']} is not admin in {config.tg_channel_link}. "
                "Please add the bot as admin and retry."
            )
        await _update_agent(supabase, config.agent_id,
                            tg_verified=True, tg_bot_id=bot["id"])

        # 7. Generate + dispatch intro posts
        logger.info("[%s] Step 7/8 — Dispatching intro posts", config.name)
        brain = ClaudeBrain(
            archetype=config.archetype,
            agent_name=config.name,
            agent_ticker=config.ticker,
            custom_prompt=config.prompt,
            trading_enabled=config.trading_enabled,
            max_trade_bnb=config.max_trade_bnb,
        )
        posts = brain.generate_intro_posts()
        for i, text in enumerate(posts):
            await asyncio.sleep(1.5)
            posted = await post_to_channel(bot["bot_token"], config.tg_channel_link, text)
            if posted:
                supabase.table("agent_posts").insert({
                    "agent_id": config.agent_id,
                    "post_type": "intro",
                    "content": text,
                    "posted": True,
                }).execute()
                logger.info("[%s] Intro post %d/3 sent", config.name, i + 1)

        # 8. Claim code + finalize
        logger.info("[%s] Step 8/8 — Finalizing", config.name)
        claim_code = _generate_claim_code()
        result.claim_code = claim_code
        await _update_agent(supabase, config.agent_id,
                            status="active",
                            claim_code=claim_code,
                            claim_code_expires=(datetime.utcnow() + timedelta(hours=24)).isoformat(),
                            last_active_at=datetime.utcnow().isoformat())

        result.success = True
        logger.info("[%s] LAUNCH COMPLETE ✓ token=%s", config.name, token_address)
        return result

    except Exception as e:
        logger.error("[%s] Launch failed: %s", config.name, e, exc_info=True)
        await _update_agent(supabase, config.agent_id,
                            status="error", error_message=str(e))
        result.error = str(e)
        return result
