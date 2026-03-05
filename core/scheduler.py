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
        stop_loss_pct: float = 50.0,  # P-01: max % of daily_limit to lose before halting trading
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
        self.stop_loss_pct = stop_loss_pct  # P-01
        self.agent_wallet = agent_wallet
        self.agent_wallet_enc = agent_wallet_enc
        self.supabase = supabase

        self.paused = False
        # P-06: will be overwritten by load_from_supabase with actual DB value to survive restarts
        self.daily_spent_bnb: float = 0.0
        self.daily_reset_at: datetime = datetime.utcnow() + timedelta(days=1)
        self._seen: set[str] = set()
        self._seen_max = 10_000  # O-10: cap in-memory dedup set

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
        # P-01: stop-loss — halt trading once spent >= stop_loss_pct% of daily_limit
        stop_threshold = self.daily_limit_bnb * (self.stop_loss_pct / 100.0)
        if self.daily_spent_bnb >= stop_threshold:
            logger.warning(
                "[%s] Stop loss triggered: spent %.4f BNB >= %.1f%% of daily limit (%.4f BNB) — trading halted",
                self.name, self.daily_spent_bnb, self.stop_loss_pct, stop_threshold,
            )
            return False
        return True

    async def handle_new_token(self, token_data: dict) -> None:
        if self.paused:
            return

        address = token_data.get("address", "")
        if not address or address in self._seen:
            return
        # O-10: evict oldest half when cap reached (Supabase is the authoritative dedup)
        if len(self._seen) >= self._seen_max:
            evict = list(self._seen)[:self._seen_max // 2]
            self._seen.difference_update(evict)
        self._seen.add(address)

        # Dedup via Supabase
        existing = self.supabase.table("seen_tokens")\
            .select("id").eq("agent_id", self.agent_id)\
            .eq("token_address", address).execute()
        if existing.data:
            return

        # P-02: do NOT insert seen_tokens here — if evaluation crashes the token
        # would be permanently skipped. Insert AFTER evaluation below.
        self._reset_daily_if_needed()

        # Get wallet balance for trading decisions
        wallet_balance = 0.0
        if self.trading_enabled:
            try:
                from web3 import Web3
                from web3.middleware import ExtraDataToPOAMiddleware
                _w3 = Web3(Web3.HTTPProvider(
                    os.environ.get("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/")
                ))
                _w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
                _agent_wallet = self.agent_wallet
                wallet_balance = float(_w3.from_wei(
                    await asyncio.get_running_loop().run_in_executor(
                        None, lambda: _w3.eth.get_balance(_agent_wallet)
                    ), "ether"
                ))
            except Exception as e:
                logger.warning("[%s] Balance check failed: %s", self.name, e)

        # Evaluate token
        brain = self._get_brain()
        evaluation = await brain.evaluate_token(token_data, wallet_balance)

        logger.info(
            "[%s] Token %s — score=%.1f post=%s trade=%s",
            self.name, address[:10], evaluation.score,
            evaluation.should_post, evaluation.should_trade
        )

        # P-02: insert seen_tokens AFTER evaluation — crash during eval won't permanently skip this token.
        # Upsert to handle rare duplicate events safely.
        self.supabase.table("seen_tokens").upsert({
            "agent_id": self.agent_id,
            "token_address": address,
            "acted": False,
        }, on_conflict="agent_id,token_address").execute()

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
            # P-03: compute deadline INSIDE _do_trade so it's fresh when the executor actually runs

            # N-05 fix: all web3 calls are synchronous/blocking — run entire trade in executor
            # to avoid blocking the asyncio event loop for up to 60s during tx wait.
            ERC20_BALANCE_ABI = [{"name": "balanceOf", "type": "function",
                "inputs": [{"name": "owner", "type": "address"}],
                "outputs": [{"name": "", "type": "uint256"}], "stateMutability": "view"}]

            def _do_trade():
                token_contract = w3.eth.contract(
                    address=Web3.to_checksum_address(token_address), abi=ERC20_BALANCE_ABI
                )
                bal_before = 0
                try:
                    bal_before = token_contract.functions.balanceOf(account.address).call()
                except Exception:
                    pass

                # P-03: deadline computed here, not outside executor, so it's always fresh
                deadline = int(datetime.utcnow().timestamp()) + 300
                # B-15: amountOutMin stays 0 for meme launches (thin liquidity)
                tx = router.functions.swapExactETHForTokensSupportingFeeOnTransferTokens(
                    0,
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
                signed_tx = account.sign_transaction(tx)
                tx_hash_bytes = w3.eth.send_raw_transaction(signed_tx.raw_transaction)
                rcpt = w3.eth.wait_for_transaction_receipt(tx_hash_bytes, timeout=60)
                bal_after = 0
                try:
                    bal_after = token_contract.functions.balanceOf(account.address).call()
                except Exception:
                    pass
                return tx_hash_bytes.hex(), rcpt, bal_before, bal_after

            loop = asyncio.get_running_loop()
            tx_hash, receipt, balance_before, balance_after = await loop.run_in_executor(None, _do_trade)
            success = receipt["status"] == 1

            if success:
                tokens_received = balance_after - balance_before
                logger.info("[%s] Trade received %s tokens for %.4f BNB",
                            self.name, tokens_received, amount_bnb)
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

        # O-06: only insert trade record if tx_hash is set — tx_hash has a UNIQUE NOT NULL
        # constraint; two failed pre-submission errors would both produce tx_hash=None → unique violation.
        if tx_hash is not None:
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
        else:
            logger.warning("[%s] Trade aborted before TX submission — not logging to agent_trades", self.name)

    async def retry_intro_posts(self) -> None:
        """
        Attempt to send any intro posts that failed because the bot wasn't in the channel yet.
        Called every 10 minutes by the scheduler. Stops retrying once all intro posts are sent.
        """
        from .telegram import post_to_channel
        try:
            unsent = self.supabase.table("agent_posts")                .select("id, content")                .eq("agent_id", self.agent_id)                .eq("post_type", "intro")                .eq("posted", False)                .execute()
            if not unsent.data:
                return
            for post in unsent.data:
                sent = await post_to_channel(self.bot_token, self.tg_channel, post["content"])
                if sent:
                    self.supabase.table("agent_posts")                        .update({"posted": True})                        .eq("id", post["id"])                        .execute()
                    logger.info("[%s] Retried intro post sent ✓", self.name)
        except Exception as e:
            logger.warning("[%s] Intro post retry error: %s", self.name, e)

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
        runtime = self._agents.pop(agent_id, None)
        if runtime and runtime._brain:
            # P-08: schedule brain close to release AsyncAnthropic httpx client
            try:
                asyncio.get_running_loop().create_task(runtime._brain.close())
            except RuntimeError:
                pass  # no running loop at shutdown, GC will handle it
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

    async def start_retry_loop(self) -> None:
        """
        Every 10 minutes, retry unsent intro posts for all active agents.
        Agents whose bot hasn't been added to their channel yet will catch up automatically.
        """
        while True:
            await asyncio.sleep(600)  # 10 minutes
            for runtime in list(self._agents.values()):
                try:
                    await runtime.retry_intro_posts()
                except Exception as e:
                    logger.warning("Retry loop error for %s: %s", runtime.name, e)

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
                stop_loss_pct=float(row.get("stop_loss_pct", 50.0)),  # P-01
                agent_wallet=row["agent_wallet"],
                agent_wallet_enc=row["agent_wallet_enc"],
                supabase=supabase,
            )
            # P-06: restore daily spend from DB so restart doesn't reset the daily counter
            runtime.daily_spent_bnb = float(row.get("daily_spent_bnb") or 0.0)
            if row.get("daily_reset_at"):
                try:
                    runtime.daily_reset_at = datetime.fromisoformat(
                        row["daily_reset_at"].replace("Z", "+00:00")
                    ).replace(tzinfo=None)
                except Exception:
                    pass
            self.register(runtime)
        logger.info("Scheduler: loaded %d active agents from DB", len(self._agents))

    @property
    def count(self) -> int:
        return len(self._agents)
