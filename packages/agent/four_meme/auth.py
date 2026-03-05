"""
four.meme Authentication — re-export from packages/fourmeme (canonical implementation).

B-01: Wrong auth header (Authorization: Bearer) fixed — now uses meme-web-access.
B-10: Wrong nonce endpoint/method fixed — now POSTs to /v1/private/user/nonce/generate.
B-11: Wrong login endpoint/body fixed — now matches official /v1/private/user/login/dex spec.

This module re-exports from packages/fourmeme/auth.py to avoid duplication.
"""
from fourmeme.auth import FourMemeAuth, Session  # noqa: F401
