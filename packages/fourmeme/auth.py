"""
four.meme authentication — nonce + wallet signature login flow.

Official endpoints (API-CreateToken docs, 02-02-2026):
  POST /v1/private/user/nonce/generate   → nonce
  POST /v1/private/user/login/dex        → access token
  Header: meme-web-access: {token}
"""
from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from eth_account import Account
from eth_account.messages import encode_defunct


BASE_URL = "https://four.meme/meme-api"


@dataclass
class Session:
    access_token: str
    expires_at: float

    @property
    def headers(self) -> dict:
        return {
            "meme-web-access": self.access_token,
            "Content-Type": "application/json",
        }

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 30


class FourMemeAuth:
    """
    Handles wallet-based login to four.meme.

    Login flow (from official API docs):
      1. POST /v1/private/user/nonce/generate  → nonce string
      2. Sign "You are sign in Meme {nonce}" with private key
      3. POST /v1/private/user/login/dex       → access token
    """

    def __init__(self, private_key: str) -> None:
        pk = private_key if private_key.startswith("0x") else f"0x{private_key}"
        self._account = Account.from_key(pk)
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=30,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://four.meme",
                "Referer": "https://four.meme/",
            },
        )
        self._session: Session | None = None

    @property
    def address(self) -> str:
        return self._account.address

    async def _fetch_nonce(self) -> str:
        resp = await self._http.post(
            "/v1/private/user/nonce/generate",
            json={
                "accountAddress": self.address,
                "verifyType": "LOGIN",
                "networkCode": "BSC",
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if str(body.get("code", "0")) != "0":
            raise RuntimeError(f"Nonce generation failed: {body}")
        return body["data"]

    async def _login(self, nonce: str) -> Session:
        message = encode_defunct(text=f"You are sign in Meme {nonce}")
        signed = self._account.sign_message(message)
        resp = await self._http.post(
            "/v1/private/user/login/dex",
            json={
                "region": "WEB",
                "langType": "EN",
                "loginIp": "",
                "inviteCode": "",
                "verifyInfo": {
                    "address": self.address,
                    "networkCode": "BSC",
                    "signature": signed.signature.hex(),
                    "verifyType": "LOGIN",
                },
                "walletName": "MetaMask",
            },
        )
        resp.raise_for_status()
        body = resp.json()
        if str(body.get("code", "0")) != "0":
            raise RuntimeError(f"Login failed: {body}")
        token = body["data"]
        return Session(
            access_token=token,
            expires_at=time.time() + 3600,
        )

    async def get_session(self) -> Session:
        if self._session is None or self._session.is_expired():
            nonce = await self._fetch_nonce()
            self._session = await self._login(nonce)
        return self._session

    async def close(self) -> None:
        await self._http.aclose()
