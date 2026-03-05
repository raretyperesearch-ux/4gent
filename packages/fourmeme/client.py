"""
four.meme REST API client — token creation, image upload, market data.
"""
from __future__ import annotations

import logging
import mimetypes
from pathlib import Path

import httpx

from .auth import FourMemeAuth

logger = logging.getLogger(__name__)

BASE_URL = "https://four.meme/meme-api"


class FourMemeError(Exception):
    def __init__(self, code: int, message: str, endpoint: str = "") -> None:
        self.code = code
        self.endpoint = endpoint
        super().__init__(f"[{code}] {endpoint}: {message}")


class FourMemeClient:
    """
    Async HTTP client wrapping four.meme's private + public API endpoints.

    Usage:
        auth   = FourMemeAuth(private_key=os.environ["WALLET_PRIVATE_KEY"])
        client = FourMemeClient(auth)
        img_url = await client.upload_image("logo.png")
        result  = await client.create_token(name="MyToken", symbol="MTK", ...)
        await client.close()
    """

    def __init__(self, auth: FourMemeAuth) -> None:
        self.auth = auth
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=60,
            headers={
                "User-Agent": "fourmeme-py/0.1.0",
                "Origin": "https://four.meme",
                "Referer": "https://four.meme/",
            },
        )

    def _check(self, data: dict, endpoint: str) -> None:
        code = data.get("code", 0)
        if code not in (0, 200):
            raise FourMemeError(code, data.get("msg", "unknown error"), endpoint)

    # ── Public ────────────────────────────────────────────────────────────────

    async def get_sys_config(self) -> dict:
        """Fetch platform config (chain IDs, contract addresses, fee params)."""
        resp = await self._http.get("/v1/public/sys/config")
        resp.raise_for_status()
        data = resp.json()
        self._check(data, "/v1/public/sys/config")
        return data["data"]

    async def get_ticker(self, page: int = 1, page_size: int = 20) -> dict:
        """Fetch currently trading tokens (bonding curve stage)."""
        resp = await self._http.get(
            "/v1/public/ticker",
            params={"pageNo": page, "pageSize": page_size, "status": "TRADING"},
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def get_token_detail(self, address: str) -> dict:
        """Fetch on-chain + market details for a deployed token."""
        resp = await self._http.get(
            "/v1/public/token/detail",
            params={"address": address},
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    # ── Private ───────────────────────────────────────────────────────────────

    async def upload_image(self, image_path: str | Path) -> str:
        """
        Upload a token logo image.

        Returns:
            imgUrl string to pass into create_token().
        """
        path = Path(image_path)
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        session = await self.auth.get_session()
        headers = {k: v for k, v in session.headers.items() if k != "Content-Type"}

        with open(path, "rb") as f:
            resp = await self._http.post(
                "/v1/private/tool/upload",
                files={"file": (path.name, f, mime)},
                headers=headers,
            )
        resp.raise_for_status()
        data = resp.json()
        self._check(data, "/v1/private/tool/upload")
        url = data["data"]["url"]
        logger.info("Image uploaded: %s", url)
        return url

    async def create_token(
        self,
        name: str,
        symbol: str,
        description: str,
        img_url: str,
        raised_amount: float = 0,
        raised_token_symbol: str = "BNB",
        twitter: str = "",
        telegram: str = "",
        website: str = "",
    ) -> dict:
        """
        Request token creation args + signature from four.meme backend.

        The returned dict contains:
            createArg  — ABI-encoded constructor args
            signature  — server signature authorising the on-chain call

        These must be submitted on-chain to TokenManager2.createToken()
        (see onchain.py).

        Returns:
            {"createArg": "0x...", "signature": "0x..."}
        """
        session = await self.auth.get_session()
        payload = {
            "name": name,
            "symbol": symbol,
            "description": description,
            "imgUrl": img_url,
            "raisedTokenSymbol": raised_token_symbol,
            "raisedAmount": str(raised_amount),
            "twitter": twitter,
            "telegram": telegram,
            "website": website,
        }
        resp = await self._http.post(
            "/v1/private/token/create",
            json=payload,
            headers=session.headers,
        )
        resp.raise_for_status()
        data = resp.json()
        self._check(data, "/v1/private/token/create")
        logger.info("Token creation args received for %s (%s)", name, symbol)
        return data["data"]

    async def get_my_tokens(self) -> list[dict]:
        """List all tokens launched by the authenticated wallet."""
        session = await self.auth.get_session()
        resp = await self._http.get(
            "/v1/private/token/my/list",
            headers=session.headers,
        )
        resp.raise_for_status()
        return resp.json().get("data", {}).get("list", [])

    async def close(self) -> None:
        await self._http.aclose()
        await self.auth.close()
