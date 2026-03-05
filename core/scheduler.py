"""
4Gent — Agent Scheduler
Manages all active AgentRuntime instances. Wires them to the shared FourMemeMonitor.
Handles posting, trading, daily spend limits, pause/resume.
"""
from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timedelta
from typing import Optional

from supabase import Client

logger = logging.getLogger(__name__)


class AgentRuntime:
    """
    One instance per active deployed agent.
    Receives token events from FourMemeMonitor and acts on them.
    """

    def __init__(
        self,
        agent_id: str,
        name: str,
        ticker: str,
        archetype: str,
        prompt: str,
        bot_token: str,
        tg_channel: str,
        trading_enabled: bool,
        max_trade_bnb: float,
        daily_limit_bnb: float,
        agent_wallet: str,
        agent_wallet_enc: str,
        supabase: Client,
    ) -> None:
        self.agent_id = agent_id
        self.name = name
        self.ticker = ticker
        self.archetype = archetype
        self.prompt = prompt
        self.bot_token = bot_token
        self.tg_channel = tg_channel
        self.trading_enabled = trading_enabled
        self.max_trade_bnb = max_trade_bnb
        self.daily_limit_bnb = daily_limit_bnb
        self.agent_wallet = agent_wallet
        self.agent_wallet_enc = agent_wallet_enc
        self.supabase = supabase

        self.paused = False
        self.daily_spent_bnb: float = 0.0
        self.daily_reset_at: datetime = datetime.utcnow() + timedelta(days=1)
        self._seen: set[str] = set()

        # Brain instantiated lazily so we don't hold connections at rest
        self._brain = None

    def _get_brain(self):
        from .claude_brain import ClaudeBrain
        if self._brain is None:
            self._brain = ClaudeBrain(
                archetype=self.archetype,
                agent_name=self.name,
                agent_ticker=self.ticker,
                custom_prompt=self.prompt,
                trading_enabled=self.trading_enabled,
                max_trade_bnb=self.max_trade_bnb,
            )
        return self._brain

    def _reset_daily_if_needed(self) -> None:
        if datetime.utcnow() >= self.daily_reset_at:
            self.daily_spent_bnb = 0.0
            self.daily_reset_at = datetime.utcnow() + timedelta(days=1)
            self.supabase.table("agents").update({
                "daily_spent_bnb": 0,
                "daily_reset_at": self.daily_reset_at.isoformat(),
            }).eq("id", self.agent_id).execute()

    def _can_trade(self, amount: float) -> bool:
        if not self.trading_enabled:
            return False
        if self.daily_spent_bnb + amount > self.daily_limit_bnb:
            logger.info("[%s] Daily limit reached (%.4f/%.4f BNB)", 
                        self.name, self.daily_spent_bnb, self.daily_limit_bnb)
            return False
        return True

    async def handle_new_token(self, token_data: dict) -> None:
        if self.paused:
            return

        address = token_data.get("address", "")
        if not address or address in self._seen:
            return
        self._seen.add(address)

        # Dedup via Supabase
        existing = self.supabase.table("seen_tokens")\
            .select("id").eq("agent_id", self.agent_id)\
            .eq("token_address", address).execute()
        if existing.data:
            return

        self.supabase.table("seen_tokens").insert({
            "agent_id": self.agent_id,
            "token_address": address,
        }).execute()

        self._reset_daily_if_needed()

        # Get wallet balance for trading decisions
        wallet_balance = 0.0
        if self.trading_enabled:
            try:
                from web3 import Web3
                from web3.middleware import ExtraDataToPOAMiddleware
                w3 = Web3(Web3.HTTPProvider(
                    os.environ.get("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/")
                ))
                w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                wallet_balance = float(w3.from_wei(
                    w3.eth.get_balance(self.agent_wallet), "ether"
                ))
            except Exception as e:
                logger.warning("[%s] Balance check failed: %s", self.name, e)

        # Evaluate token
        brain = self._get_brain()
        evaluation = brain.evaluate_token(token_data, wallet_balance)

        logger.info(
            "[%s] Token %s — score=%.1f post=%s trade=%s",
            self.name, address[:10], evaluation.score,
            evaluation.should_post, evaluation.should_trade
        )

        # Post to channel
        if evaluation.should_post and evaluation.post_text:
            from .telegram import post_to_channel
            posted = await post_to_channel(
                self.bot_token, self.tg_channel, evaluation.post_text
            )
            if posted:
                self.supabase.table("agent_posts").insert({
                    "agent_id": self.agent_id,
                    "post_type": "call",
                    "content": evaluation.post_text,
                    "token_ref": address,
                    "posted": True,
                }).execute()
                # Increment total_posts via raw SQL to avoid race conditions
                self.supabase.rpc("increment_agent_stat", {
                    "p_agent_id": self.agent_id,
                    "p_column": "total_posts",
                }).execute()

                # Mark as acted
                self.supabase.table("seen_tokens").update({"acted": True})\
                    .eq("agent_id", self.agent_id)\
                    .eq("token_address", address).execute()

        # Execute trade
        if evaluation.should_trade and self._can_trade(evaluation.trade_amount_bnb):
            await self._execute_trade(address, evaluation.trade_amount_bnb, token_data)

    async def _execute_trade(self, token_address: str, amount_bnb: float, token_data: dict) -> None:
        """Buy token on PancakeSwap via the agent wallet."""
        from .wallet import decrypt_key

        tx_hash = None
        success = False
        error_msg = None

        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware

            private_key = decrypt_key(self.agent_wallet_enc)
            w3 = Web3(Web3.HTTPProvider(
                os.environ.get("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/")
            ))
            w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            # PancakeSwap V2 Router
            ROUTER = "0x10ED43C718714eb63d5aA57B78B54704E256024E"
            WBNB   = "0xbb4CdB9CBd36B01bD1cBaEBF2De08d9173bc095c"

            ROUTER_ABI = [{
                "name": "swapExactETHForTokensSupportingFeeOnTransferTokens",
                "type": "function",
                "stateMutability": "payable",
                "inputs": [
                    {"name": "amountOutMin", "type": "uint256"},
                    {"name": "path", "type": "address[]"},
                    {"name": "to", "type": "address"},
                    {"name": "deadline", "type": "uint256"},
                ],
                "outputs": [],
            }]

            router = w3.eth.contract(
                address=Web3.to_checksum_address(ROUTER), abi=ROUTER_ABI
            )
            pk = private_key if private_key.startswith("0x") else f"0x{private_key}"
            account = w3.eth.account.from_key(pk)
            deadline = int(datetime.utcnow().timestamp()) + 300  # 5 min

            tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                0,  # amountOutMin — accept any (meme launch, slippage expected)
                [Web3.to_checksum_address(WBNB), Web3.to_checksum_address(token_address)],
                account.address,
                deadline,
            ).build_transaction({
                "from": account.address,
                "value": w3.to_wei(amount_bnb, "ether"),
                "gas": 300_000,
                "gasPrice": w3.eth.gas_price,
                "nonce": w3.eth.get_transaction_count(account.address),
                "chainId": 56,
            })

            signed = account.sign_transaction(tx)
            tx_hash_bytes = w3.eth.send_raw_transaction(signed.raw_transaction)
            tx_hash = tx_hash_bytes.hex()
            receipt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=60)
            success = receipt["status"] == 1

            if success:
                self.daily_spent_bnb += amount_bnb
                self.supabase.table("agents").update({
                    "daily_spent_bnb": self.daily_spent_bnb,
                }).eq("id", self.agent_id).execute()
                self.supabase.rpc("increment_agent_stat", {
                    "p_agent_id": self.agent_id,
                    "p_column": "total_trades",
                }).execute()
                logger.info("[%s] Trade SUCCESS %s %.4f BNB tx=%s",
                            self.name, token_address[:10], amount_bnb, tx_hash)
            else:
                logger.warning("[%s] Trade FAILED tx=%s", self.name, tx_hash)

        except Exception as e:
            error_msg = str(e)
            logger.error("[%s] Trade error: %s", self.name, e)

        # Log trade
        self.supabase.table("agent_trades").insert({
            "agent_id":      self.agent_id,
            "token_address": token_address,
            "token_name":    token_data.get("name", ""),
            "token_symbol":  token_data.get("symbol", ""),
            "direction":     "buy",
            "amount_bnb":    amount_bnb,
            "tx_hash":       tx_hash,
            "success":       success,
            "error_message": error_msg,
        }).execute()

    async def pause(self) -> None:
        self.paused = True
        self.supabase.table("agents").update({"status": "paused"}).eq("id", self.agent_id).execute()
        logger.info("[%s] Paused", self.name)

    async def resume(self) -> None:
        self.paused = False
        self.supabase.table("agents").update({"status": "active"}).eq("id", self.agent_id).execute()
        logger.info("[%s] Resumed", self.name)


class AgentScheduler:
    """
    Global scheduler — manages all active AgentRuntime instances.
    Loaded at FastAPI startup. Wires runtimes to the FourMemeMonitor.
    """

    def __init__(self) -> None:
        self._agents: dict[str, AgentRuntime] = {}

    def register(self, runtime: AgentRuntime) -> None:
        self._agents[runtime.agent_id] = runtime
        logger.info("Scheduler: registered agent %s (%s)", runtime.name, runtime.agent_id)

    def unregister(self, agent_id: str) -> None:
        self._agents.pop(agent_id, None)
        logger.info("Scheduler: unregistered agent %s", agent_id)

    def get(self, agent_id: str) -> Optional[AgentRuntime]:
        return self._agents.get(agent_id)

    async def on_token_event(self, token_data: dict) -> None:
        """Fan out incoming token event to all active agents."""
        if not self._agents:
            return
        results = await asyncio.gather(
            *[a.handle_new_token(token_data) for a in self._agents.values()],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error("Agent handler exception: %s", r)

    async def load_from_supabase(self, supabase: Client) -> None:
        """Load all active agents from DB at startup."""
        resp = supabase.table("active_agents").select("*").execute()
        for row in resp.data or []:
            runtime = AgentRuntime(
                agent_id=row["id"],
                name=row["name"],
                ticker=row["ticker"],
                archetype=row["archetype"],
                prompt=row["prompt"],
                bot_token=row["bot_token"],
                tg_channel=row["tg_channel_link"],
                trading_enabled=row["trading_enabled"],
                max_trade_bnb=float(row["max_trade_bnb"]),
                daily_limit_bnb=float(row["daily_limit_bnb"]),
                agent_wallet=row["agent_wallet"],
                agent_wallet_enc=row["agent_wallet_enc"],
                supabase=supabase,
            )
            self.register(runtime)
        logger.info("Scheduler: loaded %d active agents from DB", len(self._agents))

    @property
    def count(self) -> int:
        return len(self._agents)
