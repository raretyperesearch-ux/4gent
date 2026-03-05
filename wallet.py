"""
4Gent — Agent Wallet Management
Creates fresh BSC wallets per agent, encrypts private keys for Supabase storage.
"""
from __future__ import annotations

import os
import secrets
from dataclasses import dataclass

from cryptography.fernet import Fernet
from eth_account import Account


@dataclass
class AgentWallet:
    address: str
    private_key: str    # raw hex — only in memory during launch, never logged
    encrypted_key: str  # Fernet-encrypted, safe to store in Supabase


def _fernet() -> Fernet:
    key = os.environ.get("WALLET_ENCRYPTION_KEY", "")
    if not key:
        raise RuntimeError("WALLET_ENCRYPTION_KEY env var not set")
    return Fernet(key.encode() if isinstance(key, str) else key)


def create_agent_wallet() -> AgentWallet:
    """Generate a fresh BSC keypair for a new agent."""
    account = Account.create(secrets.token_hex(32))
    pk = account.key.hex()
    return AgentWallet(
        address=account.address,
        private_key=pk,
        encrypted_key=encrypt_key(pk),
    )


def encrypt_key(private_key: str) -> str:
    return _fernet().encrypt(private_key.encode()).decode()


def decrypt_key(encrypted_key: str) -> str:
    return _fernet().decrypt(encrypted_key.encode()).decode()


def generate_fernet_key() -> str:
    """Run once to generate WALLET_ENCRYPTION_KEY value."""
    return Fernet.generate_key().decode()
