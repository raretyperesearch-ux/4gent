"""
4Gent — Flap.sh On-chain Integration (BSC)

Flap portal contract: 0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0
No auth. No API key. No platform wallet required for launch.
User's MetaMask calls newTokenV2 directly from the frontend.

This module is used for:
  1. Watching TokenCreated events from the flap portal (monitor)
  2. Parsing token metadata from the chain
"""
from __future__ import annotations

import logging
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)

# Flap portal contract on BSC Mainnet
FLAP_PORTAL = "0xe2cE6ab80874Fa9Fa2aAE65D277Dd6B8e65C9De0"

PORTAL_ABI = [
    {
        "anonymous": False,
        "inputs": [
            {"indexed": False, "name": "ts",     "type": "uint256"},
            {"indexed": True,  "name": "creator","type": "address"},
            {"indexed": False, "name": "nonce",  "type": "uint256"},
            {"indexed": True,  "name": "token",  "type": "address"},
            {"indexed": False, "name": "name",   "type": "string"},
            {"indexed": False, "name": "symbol", "type": "string"},
            {"indexed": False, "name": "meta",   "type": "string"},
        ],
        "name": "TokenCreated",
        "type": "event",
    },
]


def get_w3(rpc_url: str = "https://bsc-dataseed1.binance.org/") -> Web3:
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
    return w3


def get_portal(w3: Web3):
    return w3.eth.contract(
        address=Web3.to_checksum_address(FLAP_PORTAL),
        abi=PORTAL_ABI,
    )


def parse_token_created_receipt(w3: Web3, tx_hash: str) -> dict | None:
    """
    Given a tx hash from the user's MetaMask launch tx,
    parse the TokenCreated event and return token info.
    Returns None if not found or tx reverted.
    """
    try:
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        if receipt.get("status") == 0:
            raise RuntimeError(f"Launch tx reverted: {tx_hash}")

        portal = get_portal(w3)
        logs = portal.events.TokenCreated().process_receipt(receipt)
        if not logs:
            logger.warning("No TokenCreated event in tx %s", tx_hash)
            return None

        event = logs[0]["args"]
        return {
            "token_address": logs[0]["args"]["token"],
            "name":          event["name"],
            "symbol":        event["symbol"],
            "meta":          event["meta"],
            "creator":       event["creator"],
            "tx_hash":       tx_hash,
        }
    except Exception as e:
        logger.error("parse_token_created_receipt failed: %s", e)
        return None
