"""
four.meme authentication — nonce + wallet signature login flow.
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
            "Authorization": f"Bearer {self.access_token}",
            "Content-Type": "application/json",
        }

    def is_expired(self) -> bool:
        return time.time() >= self.expires_at - 30


class FourMemeAuth:
    """
    Handles wallet-based login to four.meme.

    Login flow:
      1. GET  /v1/public/user/login/nonce  -> nonce string
      2. Sign "You are sign in Meme {nonce}" with private key
      3. POST /v1/public/user/login        -> accessToken
    """

    def __init__(self, private_key: str) -> None:
        pk = private_key if private_key.startswith("0x") else f"0x{private_key}"
        self._account = Account.from_key(pk)
        self._http = httpx.AsyncClient(base_url=BASE_URL, timeout=30)
        self._session: Session | None = None

    @property
    def address(self) -> str:
        return self._account.address

    async def _fetch_nonce(self) -> str:
        resp = await self._http.get(
            "/v1/public/user/login/nonce",
            params={"address": self.address},
        )
        resp.raise_for_status()
        return resp.json()["data"]["nonce"]

    async def _login(self, nonce: str) -> Session:
        message = encode_defunct(text=f"You are sign in Meme {nonce}")
        signed = self._account.sign_message(message)
        resp = await self._http.post(
            "/v1/public/user/login",
            json={
                "address": self.address,
                "signature": signed.signature.hex(),
                "nonce": nonce,
            },
        )
        resp.raise_for_status()
        data = resp.json()["data"]
        return Session(
            access_token=data["accessToken"],
            expires_at=time.time() + data.get("expiresIn", 3600),
        )

    async def get_session(self) -> Session:
        if self._session is None or self._session.is_expired():
            nonce = await self._fetch_nonce()
            self._session = await self._login(nonce)
        return self._session

    async def close(self) -> None:
        await self._http.aclose()
