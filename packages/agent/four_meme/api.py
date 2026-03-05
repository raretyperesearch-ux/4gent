"""
four.meme API client — re-export from packages/fourmeme (canonical implementation).

B-02: Wrong upload endpoint (/tool/upload) fixed — now uses /token/upload.
B-03: Wrong create_token() payload (wrong field names, missing fixed params) fixed.

This module re-exports from packages/fourmeme/client.py to avoid duplication.
"""
from fourmeme.client import FourMemeClient, FourMemeError  # noqa: F401
