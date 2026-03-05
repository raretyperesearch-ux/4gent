"""
four.meme REST API client — token creation, image upload, market data.

Endpoints (official API-CreateToken docs, 02-02-2026):
  POST /v1/private/token/upload   — upload image, returns CDN url
  POST /v1/private/token/create   — get createArg + signature for on-chain tx
  Auth header: meme-web-access: {token}
"""
from __future__ import annotations

import logging
import mimetypes
import time
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
    Async HTTP client wrapping four.meme's private API endpoints.

    Usage:
        auth   = FourMemeAuth(private_key=os.environ["WALLET_PRIVATE_KEY"])
        client = FourMemeClient(auth)
        img_url = await client.upload_image("logo.png")
        result  = await client.create_token(name="MyToken", short_name="MTK", ...)
        await client.close()
    """

    def __init__(self, auth: FourMemeAuth) -> None:
        self.auth = auth
        self._http = httpx.AsyncClient(
            base_url=BASE_URL,
            timeout=60,
            headers={
                "User-Agent": "Mozilla/5.0",
                "Origin": "https://four.meme",
                "Referer": "https://four.meme/",
            },
        )

    def _check(self, data: dict, endpoint: str) -> None:
        code = str(data.get("code", "0"))
        if code not in ("0", "200"):
            raise FourMemeError(int(code) if code.isdigit() else -1,
                                data.get("msg", "unknown error"), endpoint)

    # ── Public ────────────────────────────────────────────────────────────────

    async def get_public_config(self) -> dict:
        """Fetch platform config."""
        resp = await self._http.get("/v1/public/config")
        resp.raise_for_status()
        return resp.json().get("data", {})

    # ── Private ───────────────────────────────────────────────────────────────

    async def upload_image(self, image_path: str | Path) -> str:
        """
        Upload a token logo image to four.meme CDN.

        Returns:
            imgUrl string (four.meme CDN URL) to pass into create_token().
        """
        path = Path(image_path)
        mime = mimetypes.guess_type(str(path))[0] or "image/png"
        session = await self.auth.get_session()

        # Multipart upload — Content-Type must NOT be set manually (httpx sets boundary)
        auth_headers = {"meme-web-access": session.access_token}

        with open(path, "rb") as f:
            resp = await self._http.post(
                "/v1/private/token/upload",
                files={"file": (path.name, f, mime)},
                headers=auth_headers,
            )
        resp.raise_for_status()
        data = resp.json()
        self._check(data, "/v1/private/token/upload")
        url = data["data"]
        logger.info("Image uploaded: %s", url)
        return url

    async def upload_image_bytes(self, image_bytes: bytes, filename: str = "token.png", mime: str = "image/png") -> str:
        """
        Upload raw image bytes to four.meme CDN.

        Returns:
            imgUrl string (four.meme CDN URL).
        """
        session = await self.auth.get_session()
        auth_headers = {"meme-web-access": session.access_token}

        resp = await self._http.post(
            "/v1/private/token/upload",
            files={"file": (filename, image_bytes, mime)},
            headers=auth_headers,
        )
        resp.raise_for_status()
        data = resp.json()
        self._check(data, "/v1/private/token/upload")
        url = data["data"]
        logger.info("Image uploaded: %s", url)
        return url

    async def create_token(
        self,
        name: str,
        short_name: str,
        description: str,
        img_url: str,
        pre_sale: float = 0.0,
        label: str = "Others",
        twitter: str = "",
        telegram: str = "",
        website: str = "",
        only_mpc: bool = False,
        fee_plan: bool = False,
        token_tax_info: dict | None = None,
    ) -> dict:
        """
        Request token creation args + signature from four.meme backend.

        Args:
            name:            Token name (e.g. "RELEASE")
            short_name:      Token ticker/symbol (e.g. "RELS")
            description:     Token description
            img_url:         CDN URL from upload_image()
            pre_sale:        BNB amount for creator seed buy (0 = no presale)
            label:           Category — one of: Meme/AI/Defi/Games/Infra/De-Sci/Social/Depin/Charity/Others
            twitter:         Twitter/X URL
            telegram:        Telegram URL
            website:         Website URL
            only_mpc:        X Mode token (Binance wallet exclusive)
            fee_plan:        AntiSniperFeeMode (dynamic fee at launch)
            token_tax_info:  Tax token config dict (None = standard token)

        Returns:
            {"createArg": "0x...", "signature": "0x..."}
        """
        session = await self.auth.get_session()
        raised_token = {
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

        payload = {
            "name": name,
            "shortName": short_name,
            "desc": description,
            "imgUrl": img_url,
            "launchTime": int(time.time() * 1000),
            "label": label,
            "lpTradingFee": 0.0025,
            "webUrl": website,
            "twitterUrl": twitter,
            "telegramUrl": telegram,
            "preSale": str(pre_sale),
            "onlyMPC": only_mpc,
            "feePlan": fee_plan,
            "raisedAmount": "24",
            "raisedToken": raised_token,
        }

        if token_tax_info:
            payload["tokenTaxInfo"] = token_tax_info

        logger.info("create_token payload: %s", payload)
        resp = await self._http.post(
            "/v1/private/token/create",
            json=payload,
            headers=session.headers,
        )
        if resp.status_code >= 400:
            logger.error("four.meme create_token error %s: %s", resp.status_code, resp.text)
        resp.raise_for_status()
        data = resp.json()
        self._check(data, "/v1/private/token/create")
        logger.info("Token creation args received for %s ($%s)", name, short_name)
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

    async def get_token_info(self, address: str) -> dict:
        """Fetch token info including taxInfo, feePlan, aiCreator flags."""
        resp = await self._http.get(
            "/v1/private/token/get",
            params={"address": address},
        )
        resp.raise_for_status()
        return resp.json().get("data", {})

    async def close(self) -> None:
        await self._http.aclose()
        await self.auth.close()
