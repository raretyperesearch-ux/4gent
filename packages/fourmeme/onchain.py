"""
BSC on-chain submission — calls TokenManager2.createToken().
"""
from __future__ import annotations

import logging

from eth_account import Account
from web3 import Web3
from web3.middleware import ExtraDataToPOAMiddleware

logger = logging.getLogger(__name__)

# four.meme TokenManager2 on BSC Mainnet
TOKEN_MANAGER2_ADDRESS = "0x5c952063c7fc8610FFDB798152D69F0B9550762b"

TOKEN_MANAGER2_ABI = [
    {
        "inputs": [
            {"internalType": "bytes", "name": "createArg", "type": "bytes"},
            {"internalType": "bytes", "name": "signature", "type": "bytes"},
        ],
        "name": "createToken",
        "outputs": [{"internalType": "address", "name": "", "type": "address"}],
        "stateMutability": "payable",
        "type": "function",
    }
]


class BSCChain:
    """
    Submits signed token creation args to TokenManager2 on BSC.

    Args:
        private_key: deployer wallet private key
        rpc_url:     BSC RPC endpoint (defaults to public node)
    """

    DEFAULT_RPC = "https://bsc-dataseed1.binance.org/"

    def __init__(self, private_key: str, rpc_url: str = DEFAULT_RPC) -> None:
        pk = private_key if private_key.startswith("0x") else f"0x{private_key}"
        self._account = Account.from_key(pk)
        self._w3 = Web3(Web3.HTTPProvider(rpc_url))
        self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        self._contract = self._w3.eth.contract(
            address=Web3.to_checksum_address(TOKEN_MANAGER2_ADDRESS),
            abi=TOKEN_MANAGER2_ABI,
        )

    @property
    def address(self) -> str:
        return self._account.address

    def get_balance(self) -> float:
        """Return wallet BNB balance."""
        wei = self._w3.eth.get_balance(self.address)
        return float(self._w3.from_wei(wei, "ether"))

    def submit_create_token(
        self,
        create_arg: str,
        signature: str,
        raise_amount_bnb: float = 0,
        gas: int = 500_000,
        gas_price_gwei: float = 3.0,
    ) -> str:
        """
        Call TokenManager2.createToken() on-chain.

        Args:
            create_arg:       hex bytes from four.meme /token/create response
            signature:        hex bytes from four.meme /token/create response
            raise_amount_bnb: BNB to send as initial seed raise
            gas:              gas limit
            gas_price_gwei:   gas price in Gwei

        Returns:
            Transaction hash (hex string).
        """
        value_wei = self._w3.to_wei(raise_amount_bnb, "ether")
        nonce = self._w3.eth.get_transaction_count(self.address)

        tx = self._contract.functions.createToken(
            bytes.fromhex(create_arg.removeprefix("0x")),
            bytes.fromhex(signature.removeprefix("0x")),
        ).build_transaction(
            {
                "from": self.address,
                "value": value_wei,
                "gas": gas,
                "gasPrice": self._w3.to_wei(gas_price_gwei, "gwei"),
                "nonce": nonce,
                "chainId": 56,
            }
        )

        signed = self._account.sign_transaction(tx)
        tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
        logger.info("TX submitted: %s", tx_hash.hex())
        return tx_hash.hex()

    def wait_for_receipt(self, tx_hash: str, timeout: int = 120) -> dict:
        """Poll until tx is confirmed, return receipt."""
        receipt = self._w3.eth.wait_for_transaction_receipt(
            tx_hash, timeout=timeout
        )
        token_address = receipt.get("contractAddress") or receipt["logs"][0].get("address", "")
        logger.info("Confirmed — token address: %s", token_address)
        return dict(receipt)
