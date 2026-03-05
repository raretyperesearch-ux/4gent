"""
4Gent — ERC-8004 Agent Wallet Registration
Mints the ERC-8004 NFT for a new agent wallet on BSC.
Required for four.meme insider phase access.

NOTE: four.meme's official ERC-8004 contract address is not yet published.
This module is fully wired and ready — just update ERC8004_CONTRACT_ADDRESS
when four.meme releases it. Until then it logs a warning and skips gracefully.
"""
from __future__ import annotations

import logging
import os

from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)

# UPDATE THIS when four.meme publishes the official ERC-8004 contract on BSC
ERC8004_CONTRACT_ADDRESS = os.environ.get("ERC8004_CONTRACT_ADDRESS", "")

ERC8004_ABI = [
    {
        "inputs": [{"name": "agentURI", "type": "string"}],
        "name": "register",
        "outputs": [{"name": "agentId", "type": "uint256"}],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


async def register_agent_wallet(
    wallet_address: str,
    private_key: str,
    agent_name: str,
    agent_id: str,
) -> dict:
    """
    Register a wallet as an ERC-8004 agent on BSC.
    Returns {"agent_nft_id": ..., "tx_hash": ..., "skipped": bool}
    """
    if not ERC8004_CONTRACT_ADDRESS:
        logger.warning(
            "ERC8004_CONTRACT_ADDRESS not set — skipping registration for %s. "
            "Set this env var once four.meme publishes the contract.",
            wallet_address,
        )
        return {"agent_nft_id": None, "tx_hash": None, "skipped": True}

    rpc_url = os.environ.get("BSC_RPC_URL", "https://bsc-dataseed1.binance.org/")
    w3 = Web3(Web3.HTTPProvider(rpc_url))
    w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

    contract = w3.eth.contract(
        address=Web3.to_checksum_address(ERC8004_CONTRACT_ADDRESS),
        abi=ERC8004_ABI,
    )

    agent_uri = f"https://4gent.io/agents/{agent_id}/metadata.json"
    pk = private_key if private_key.startswith("0x") else f"0x{private_key}"

    # N-03 fix: web3 calls are synchronous (blocking I/O) — run in executor to avoid
    # blocking the asyncio event loop for up to 120s during tx wait.
    import asyncio
    loop = asyncio.get_running_loop()

    def _submit():
        tx = contract.functions.register(agent_uri).build_transaction({
            "from": wallet_address,
            "nonce": w3.eth.get_transaction_count(wallet_address),
            "gas": 200_000,
            "gasPrice": w3.eth.gas_price,
            "chainId": 56,
        })
        signed = w3.eth.account.sign_transaction(tx, pk)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
        return tx_hash, receipt

    tx_hash, receipt = await loop.run_in_executor(None, _submit)

    logger.info("ERC-8004 registered: wallet=%s tx=%s", wallet_address, tx_hash.hex())
    return {
        "agent_nft_id": None,  # parse from receipt logs once ABI is confirmed
        "tx_hash": tx_hash.hex(),
        "skipped": False,
    }
