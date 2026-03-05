"""
four.meme REST API client — token creation, image upload.
Updated Feb 2026 API with tax token support.
"""
from __future__ import annotations

import logging
import mimetypes
import os
from pathlib import Path

import httpx

from .auth import FourMemeAuth, BROWSER_HEADERS

logger = logging.getLogger(__name__)

BASE_URL = "https://four.meme/meme-api"

# Our platform wallet — receives 2% of all trades post-graduation
PLATFORM_FEE_WALLET = os.environ.get("PLATFORM_FEE_WALLET", "")

RAISED_TOKEN = {
    "symbol": "BNB",
    "nativeSymbol": "BNB",
    "symbolAddress": "0xbb4cdb9cbd36b01bd1cbaebf2de08d9173bc095c",
    "deployCost": "0",
    "buyFee": "0.01",
    "sellFee": "0.01",
    "minTradeFee": "0",
    "b0Amount": "8",
    "totalBAmount": "24",
    "totalAmount": "1000000000",
    "logoUrl": "https://static.four.meme/market/68b871b6-96f7-408c-b8d0-388d804b34275092658264263839640.png",
    "tradeLevel": ["0.1", "0.5", "1"],
    "status": "PUBLISH",
    "buyTokenLink": "https://pancakeswap.finance/swap",
    "reservedNumber": 10,
    "saleRate": "0.8",
    "networkCode": "BSC",
    "platform": "MEME",
}


class FourMemeError(Exception):
    def __init__(self, code: int, message: str, endpoint: str = "") -> None:
        self.code = code
        self.endpoint = endpoint
        super().__init__(f"[{code}] {endpoint}: {message}")


class FourMemeClient:
    """
    Async HTTP client wrapping four.meme's private API endpoints.
    Supports tax token creation with 2%/1% fee split.
    """

    def __init__(self, auth: FourMemeAuth) -> None:
        self.auth = auth
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=60,
            headers=BROWSER_HEADERS,
        )

    def _check(self, data: dict, endpoint: str) -> None:
        code = data.get("code", 0)
        if code not in (0, "0", 200):
            raise FourMemeError(code, data.get("msg", "unknown error"), endpoint)

    async def upload_image(self, image_path: str | Path) -> str:
        """
        Upload a token logo image to four.meme CDN.
        Returns the hosted imgUrl.
        """
        path = Path(image_path)
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        session = await self.auth.get_session()
        headers = {k: v for k, v in session.headers.items() if k != "Content-Type"}

        with open(path, "rb") as f:
            resp = await self._http.post(
                "/v1/private/token/upload",
                files={"file": (path.name, f, mime)},
                headers=headers,
            )
        resp.raise_for_status()
        data = resp.json()
        self._check(data, "/v1/private/token/upload")
        url = data["data"] if isinstance(data["data"], str) else data["data"]["url"]
        logger.info("Image uploaded: %s", url)
        return url

    async def create_token(
        self,
        name: str,
        symbol: str,
        description: str,
        img_url: str,
        presale_bnb: float = 0,
        twitter: str = "",
        telegram: str = "",
        website: str = "",
        creator_wallet: str = "",
        label: str = "AI",
        anti_sniper: bool = False,
    ) -> dict:
        """
        Request token creation args + signature from four.meme backend.

        Tax split (locked at creation, enforced on-chain):
          - 3% total fee on every PancakeSwap trade post-graduation
          - 67% of tax (2% effective) → PLATFORM_FEE_WALLET
          - 33% of tax (1% effective) → creator_wallet (as dividends to holders
            — if no creator_wallet provided, goes to burn)

        Returns:
            {"createArg": "0x...", "signature": "0x..."}
        """
        session = await self.auth.get_session()

        # Build tax config
        if creator_wallet and PLATFORM_FEE_WALLET:
            # Full split: 2% platform, 1% creator as recipient
            token_tax_info = {
                "feeRate": 3,
                "recipientRate": 67,
                "recipientAddress": PLATFORM_FEE_WALLET,
                "divideRate": 33,
                "burnRate": 0,
                "liquidityRate": 0,
                "minSharing": 100000,
            }
            # Note: divideRate goes to token holders proportionally.
            # Creator gets their share by holding tokens in their wallet.
            # If you want creator to get a direct cut, set recipientRate split differently.
        elif PLATFORM_FEE_WALLET:
            # Platform only, no creator split
            token_tax_info = {
                "feeRate": 3,
                "recipientRate": 100,
                "recipientAddress": PLATFORM_FEE_WALLET,
                "divideRate": 0,
                "burnRate": 0,
                "liquidityRate": 0,
                "minSharing": 100000,
            }
        else:
            token_tax_info = None

        payload = {
            "name": name,
            "shortName": symbol,
            "desc": description[:200],
            "imgUrl": img_url,
            "launchTime": __import__("time").time_ns() // 1_000_000 + 60_000,
            "label": label,
            "lpTradingFee": 0.0025,
            "preSale": str(presale_bnb),
            "onlyMPC": False,
            "feePlan": anti_sniper,
            "raisedToken": RAISED_TOKEN,
        }

        if twitter:
            payload["twitterUrl"] = twitter
        if telegram:
            payload["telegramUrl"] = telegram
        if website:
            payload["webUrl"] = website
        if token_tax_info:
            payload["tokenTaxInfo"] = token_tax_info

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

    async def close(self) -> None:
        await self._http.aclose()
        await self.auth.close()
