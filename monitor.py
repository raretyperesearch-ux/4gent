"""
4Gent — four.meme Launch Monitor
Single shared Bitquery websocket. Fans new token events to all active agents.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from typing import Callable, Awaitable

import httpx
import websockets

logger = logging.getLogger(__name__)

BITQUERY_WS  = "wss://streaming.bitquery.io/eap"
FOUMEME_CONTRACT = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"

# Watch for TokenCreated events on four.meme's TokenManager2
NEW_TOKEN_SUB = """
subscription {
  EVM(network: bsc) {
    Events(
      where: {
        Log: {
          SmartContract: { is: "%s" }
          Signature: { Name: { is: "TokenCreated" } }
        }
      }
    ) {
      Block { Time Number }
      Transaction { Hash From }
      Arguments {
        Name
        Value {
          ... on EVM_ABI_String_Value_Arg  { string }
          ... on EVM_ABI_Address_Value_Arg { address }
          ... on EVM_ABI_BigInt_Value_Arg  { bigInteger }
        }
      }
    }
  }
}
""" % FOUMEME_CONTRACT

TokenHandler = Callable[[dict], Awaitable[None]]


class FourMemeMonitor:
    """
    Maintains a single Bitquery websocket.
    Calls all registered handlers when a new four.meme token is detected.
    Handlers receive enriched token_data dict including metadata from four.meme API.
    """

    def __init__(self, api_key: str) -> None:
        self.api_key = api_key
        self._handlers: list[TokenHandler] = []
        self._running = False

    def register(self, handler: TokenHandler) -> None:
        self._handlers.append(handler)

    def unregister(self, handler: TokenHandler) -> None:
        self._handlers = [h for h in self._handlers if h != handler]

    async def start(self) -> None:
        self._running = True
        logger.info("four.meme monitor starting...")
        while self._running:
            try:
                await self._connect()
            except Exception as e:
                logger.error("Monitor disconnected: %s — reconnecting in 10s", e)
                await asyncio.sleep(10)

    async def stop(self) -> None:
        self._running = False

    async def _connect(self) -> None:
        headers = {"X-API-KEY": self.api_key}
        async with websockets.connect(
            BITQUERY_WS,
            additional_headers=headers,
            subprotocols=["graphql-ws"],
            ping_interval=30,
            ping_timeout=10,
        ) as ws:
            await ws.send(json.dumps({"type": "connection_init"}))
            ack = json.loads(await ws.recv())
            if ack.get("type") != "connection_ack":
                raise RuntimeError(f"Bitquery connection failed: {ack}")

            await ws.send(json.dumps({
                "id": "fourgent-monitor",
                "type": "start",
                "payload": {"query": NEW_TOKEN_SUB},
            }))
            logger.info("four.meme monitor connected ✓ watching BSC TokenCreated events")

            async for raw in ws:
                if not self._running:
                    break
                msg = json.loads(raw)
                if msg.get("type") == "data":
                    await self._on_data(msg.get("payload", {}))
                elif msg.get("type") == "error":
                    logger.error("Bitquery stream error: %s", msg)

    async def _on_data(self, payload: dict) -> None:
        events = payload.get("data", {}).get("EVM", {}).get("Events", [])
        for event in events:
            token_data = self._parse_event(event)
            if not token_data:
                continue
            # Enrich with four.meme API metadata
            enriched = await self._enrich(token_data)
            await self._dispatch(enriched)

    def _parse_event(self, event: dict) -> dict | None:
        try:
            args: dict = {}
            for a in event.get("Arguments", []):
                val = a.get("Value", {})
                args[a["Name"]] = (
                    val.get("string") or val.get("address") or val.get("bigInteger") or ""
                )
            return {
                "address":      args.get("token", ""),
                "deployer":     event["Transaction"]["From"],
                "tx_hash":      event["Transaction"]["Hash"],
                "block_time":   event["Block"]["Time"],
                "block_number": event["Block"]["Number"],
                "name":         args.get("name", ""),
                "symbol":       args.get("symbol", ""),
                "raise_amount": args.get("raisedAmount", 0),
            }
        except Exception as e:
            logger.warning("Failed to parse event: %s", e)
            return None

    async def _enrich(self, token_data: dict) -> dict:
        """Fetch additional metadata from four.meme public API."""
        try:
            address = token_data.get("address", "")
            if not address:
                return token_data
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    "https://four.meme/meme-api/v1/public/token/detail",
                    params={"address": address},
                )
                if r.status_code == 200:
                    detail = r.json().get("data", {})
                    token_data["name"]        = detail.get("name", token_data["name"])
                    token_data["symbol"]      = detail.get("symbol", token_data["symbol"])
                    token_data["description"] = detail.get("description", "")
                    token_data["image_url"]   = detail.get("imgUrl", "")
                    token_data["raise_amount"]= detail.get("raisedAmount", token_data["raise_amount"])
        except Exception as e:
            logger.debug("Enrichment failed for %s: %s", token_data.get("address"), e)
        return token_data

    async def _dispatch(self, token_data: dict) -> None:
        if not self._handlers:
            return
        await asyncio.gather(
            *[h(token_data) for h in self._handlers],
            return_exceptions=True,
        )
