"""
4Gent — Agent Launch Pipeline
Split into two phases:
  Phase 1: /launch/prepare  — stores config, fetches createArg+sig from four.meme, returns to frontend
  Phase 2: /launch/confirm  — receives tx_hash from frontend after user signs on BSC, runs post-deploy pipeline

The user's own wallet submits the createToken() tx and pays gas.
The agent wallet is created AFTER successful deploy — used only for autonomous trading.
No channel verification at launch time — user adds their assigned bot AFTER launch.
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
class PrepareResult:
    success: bool
    agent_id: str
    create_arg: str = ""
    signature: str = ""
    contract_address: str = ""
    value_wei: str = "0"
    calldata: str = ""
    error: str = ""


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


async def prepare_launch(config: LaunchConfig) -> PrepareResult:
    """
    Phase 1: fetch createArg + signature from four.meme API.
    Returns calldata the frontend needs to submit the tx via the user's wallet.
    No on-chain tx here — just API auth (message signature only, no BNB needed).
    """
    from fourmeme.auth import FourMemeAuth
    from fourmeme.client import FourMemeClient
    from fourmeme.onchain import TOKEN_MANAGER2_ADDRESS, TOKEN_MANAGER2_ABI

    supabase = get_supabase()

    platform_key = os.environ.get("PLATFORM_FOURMEME_KEY") or os.environ.get("WALLET_PRIVATE_KEY", "")
    if not platform_key:
        return PrepareResult(
            success=False, agent_id=config.agent_id,
            error="PLATFORM_FOURMEME_KEY not set — needed to authenticate with four.meme API"
        )

    try:
        await _update_agent(supabase, config.agent_id, status="preparing")

        auth = FourMemeAuth(private_key=platform_key)
        client = FourMemeClient(auth)

        try:
            create_result = await client.create_token(
                name=config.name,
                short_name=config.ticker,
                description=config.prompt[:200],
                img_url=config.image_url,
                pre_sale=config.raise_amount_bnb,
                telegram=(
                    "https://t.me/" + config.tg_channel_link
                        .replace("https://t.me/", "")
                        .replace("http://t.me/", "")
                        .replace("t.me/", "")
                        .lstrip("@").strip("/")
                    if config.tg_channel_link else ""
                ),
            )
        finally:
            await client.close()

        # Build calldata locally — no provider/RPC/network needed
        # selector = keccak256("createToken(bytes,bytes)")[:4]
        from eth_abi import encode as abi_encode
        from web3 import Web3
        selector = bytes.fromhex("5c9e4318")
        arg1 = bytes.fromhex(create_result["createArg"].removeprefix("0x"))
        arg2 = bytes.fromhex(create_result["signature"].removeprefix("0x"))
        calldata = "0x" + selector.hex() + abi_encode(["bytes", "bytes"], [arg1, arg2]).hex()

        # 0.02 BNB is the fixed deploy fee required by four.meme contract + any presale amount on top
        DEPLOY_FEE_BNB = 0.02
        value_wei = str(Web3.to_wei(config.raise_amount_bnb + DEPLOY_FEE_BNB, "ether"))

        await _update_agent(supabase, config.agent_id, status="awaiting_tx")

        logger.info("[%s] Prepare complete — calldata ready, awaiting user tx", config.name)
        return PrepareResult(
            success=True,
            agent_id=config.agent_id,
            create_arg=create_result["createArg"],
            signature=create_result["signature"],
            contract_address=TOKEN_MANAGER2_ADDRESS,
            value_wei=value_wei,
            calldata=calldata,
        )

    except Exception as e:
        logger.error("[%s] Prepare failed: %s", config.name, e, exc_info=True)
        await _update_agent(supabase, config.agent_id, status="error", error_message=str(e))
        return PrepareResult(success=False, agent_id=config.agent_id, error=str(e))


async def confirm_launch(agent_id: str, tx_hash: str) -> LaunchResult:
    """
    Phase 2: user has submitted the createToken() tx from their wallet.
    Wait for confirmation, parse token address, then:
      - create agent trading wallet
      - ERC-8004 registration
      - assign bot from pool
      - generate intro posts (saved to DB, attempted to channel — no hard fail if bot not added yet)
      - generate claim code
    No channel verification — user adds their bot after launch from the success screen.
    """
    from .wallet import create_agent_wallet
    from .telegram import assign_bot_from_pool, post_to_channel
    from .claude_brain import ClaudeBrain
    from .erc8004 import register_agent_wallet

    supabase = get_supabase()
    result = LaunchResult(success=False, agent_id=agent_id)

    row = supabase.table("agents").select("*").eq("id", agent_id).execute()
    if not row.data:
        result.error = "Agent not found"
        return result
    agent = row.data[0]

    try:
        await _update_agent(supabase, agent_id, status="launching", token_tx_hash=tx_hash)

        # 1. Confirm tx on BSC + parse token address
        logger.info("[%s] Step 1/6 — Confirming tx %s on BSC", agent["name"], tx_hash[:12])
        from fourmeme.onchain import BSCChain
        bsc_rpc = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/")
        _dummy_key = "0x" + "1" * 64  # only needed to instantiate BSCChain, never used for signing
        chain = BSCChain(private_key=_dummy_key, rpc_url=bsc_rpc)

        loop = asyncio.get_running_loop()
        token_address, confirmed_tx_hash = await loop.run_in_executor(
            None, lambda: chain.wait_for_receipt_and_address(tx_hash)
        )

        if not token_address:
            raise RuntimeError(f"Could not parse token address from tx {tx_hash}")

        result.token_address = token_address
        result.token_tx_hash = confirmed_tx_hash
        await _update_agent(supabase, agent_id, token_address=token_address, token_deployed=True)
        logger.info("[%s] Token deployed: %s", agent["name"], token_address)

        # 2. Create agent trading wallet
        logger.info("[%s] Step 2/6 — Creating agent trading wallet", agent["name"])
        wallet = create_agent_wallet()
        result.agent_wallet = wallet.address
        await _update_agent(supabase, agent_id,
                            agent_wallet=wallet.address,
                            agent_wallet_enc=wallet.encrypted_key)

        # 3. ERC-8004 registration
        logger.info("[%s] Step 3/6 — ERC-8004 registration", agent["name"])
        erc = await register_agent_wallet(
            wallet_address=wallet.address,
            private_key=wallet.private_key,
            agent_name=agent["name"],
            agent_id=agent_id,
        )
        await _update_agent(supabase, agent_id,
                            erc8004_registered=not erc.get("skipped", False),
                            erc8004_tx_hash=erc.get("tx_hash"))

        # 4. Assign bot from pool
        logger.info("[%s] Step 4/6 — Assigning Telegram bot", agent["name"])
        bot = await assign_bot_from_pool(supabase, agent_id)
        if not bot:
            raise RuntimeError("Bot pool exhausted — add more bots via @BotFather")
        result.bot_username = bot["bot_username"]
        _assigned_bot_id = bot["id"]
        await _update_agent(supabase, agent_id, tg_bot_id=bot["id"])

        # 5. Generate intro posts via Claude + attempt to post
        # No hard fail if posting fails — user hasn't added the bot yet.
        # Posts are saved to DB regardless so they can be retried.
        logger.info("[%s] Step 5/6 — Generating intro posts", agent["name"])
        brain = ClaudeBrain(
            archetype=agent["archetype"],
            agent_name=agent["name"],
            agent_ticker=agent["ticker"],
            custom_prompt=agent["prompt"],
            trading_enabled=agent["trading_enabled"],
            max_trade_bnb=float(agent["max_trade_bnb"]),
        )
        try:
            posts = await brain.generate_intro_posts()
        finally:
            await brain.close()

        for i, text in enumerate(posts):
            await asyncio.sleep(1.5)
            # Save post to DB regardless of whether channel posting succeeds
            supabase.table("agent_posts").insert({
                "agent_id": agent_id,
                "post_type": "intro",
                "content": text,
                "posted": False,  # will be updated to True if/when actually posted
            }).execute()
            posted = await post_to_channel(bot["bot_token"], agent["tg_channel_link"], text)
            if posted:
                supabase.table("agent_posts").update({"posted": True})\
                    .eq("agent_id", agent_id)\
                    .eq("content", text)\
                    .execute()
                logger.info("[%s] Intro post %d/3 sent", agent["name"], i + 1)
            else:
                logger.info("[%s] Intro post %d/3 saved — bot not yet in channel, will retry", agent["name"], i + 1)

        # 6. Finalize
        logger.info("[%s] Step 6/6 — Finalizing", agent["name"])
        claim_code = _generate_claim_code()
        result.claim_code = claim_code
        await _update_agent(supabase, agent_id,
                            status="active",
                            claim_code=claim_code,
                            claim_code_expires=(datetime.utcnow() + timedelta(hours=24)).isoformat(),
                            last_active_at=datetime.utcnow().isoformat())

        result.success = True
        logger.info("[%s] LAUNCH COMPLETE ✓ token=%s bot=%s", agent["name"], token_address, bot["bot_username"])
        return result

    except Exception as e:
        logger.error("[%s] Confirm failed: %s", agent["name"], e, exc_info=True)
        await _update_agent(supabase, agent_id, status="error", error_message=str(e))
        result.error = str(e)
        try:
            bot_id_to_release = locals().get("_assigned_bot_id")
            if not bot_id_to_release:
                bot_resp = supabase.table("agents").select("tg_bot_id").eq("id", agent_id).execute()
                if bot_resp.data:
                    bot_id_to_release = bot_resp.data[0].get("tg_bot_id")
            if bot_id_to_release:
                from .telegram import release_bot_to_pool
                await release_bot_to_pool(supabase, bot_id_to_release)
        except Exception as release_err:
            logger.warning("[%s] Failed to release bot: %s", agent["name"], release_err)
        return result
