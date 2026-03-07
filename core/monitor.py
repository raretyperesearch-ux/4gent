"""
4Gent — Flap.sh Token Monitor

Polls BSC for new TokenCreated events from the Flap portal.
No Bitquery. No API key. No websocket auth.
Uses web3.py to poll every 3 seconds.
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Callable, Awaitable

logger = logging.getLogger(__name__)

TokenHandler = Callable[[dict], Awaitable[None]]

POLL_INTERVAL = 3  # seconds


class FlapMonitor:
    """
    Polls BSC every 3 seconds for new TokenCreated events on the Flap portal.
    Fans events out to all registered agent handlers.
    """

    def __init__(self) -> None:
        self._handlers: list[TokenHandler] = []
        self._running = False
        self._last_block: int = 0

    def register(self, handler: TokenHandler) -> None:
        self._handlers.append(handler)

    async def start(self) -> None:
        from flap.onchain import get_w3, get_portal, FLAP_PORTAL
        from web3 import Web3

        self._running = True
        bsc_rpc = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/")
        w3 = get_w3(bsc_rpc)
        portal = get_portal(w3)

        loop = asyncio.get_running_loop()

        # Start from current block
        self._last_block = await loop.run_in_executor(None, w3.eth.block_number)
        logger.info("Flap monitor started at block %d", self._last_block)

        while self._running:
            try:
                current_block = await loop.run_in_executor(None, w3.eth.block_number)

                if current_block > self._last_block:
                    logs = await loop.run_in_executor(
                        None,
                        lambda: portal.events.TokenCreated().get_logs(
                            from_block=self._last_block + 1,
                            to_block=current_block,
                        )
                    )

                    for log in logs:
                        token_data = {
                            "address":      log["args"]["token"],
                            "name":         log["args"]["name"],
                            "symbol":       log["args"]["symbol"],
                            "meta":         log["args"]["meta"],
                            "deployer":     log["args"]["creator"],
                            "tx_hash":      log["transactionHash"].hex(),
                            "block_number": log["blockNumber"],
                        }
                        logger.info("New flap token: %s ($%s) %s",
                                    token_data["name"], token_data["symbol"],
                                    token_data["address"][:10])
                        await self._dispatch(token_data)

                    self._last_block = current_block

            except Exception as e:
                logger.error("Flap monitor poll error: %s — retrying", e)

            await asyncio.sleep(POLL_INTERVAL)

    async def stop(self) -> None:
        self._running = False
        logger.info("Flap monitor stopped")

    async def _dispatch(self, token_data: dict) -> None:
        if not self._handlers:
            return
        results = await asyncio.gather(
            *[h(token_data) for h in self._handlers],
            return_exceptions=True,
        )
        for r in results:
            if isinstance(r, Exception):
                logger.error("Handler error: %s", r)
