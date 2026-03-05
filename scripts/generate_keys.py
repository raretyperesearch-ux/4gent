"""
4Gent — Key & Webhook Setup
Run once during initial setup to generate secrets and register Telegram webhook.

Usage:
  python scripts/generate_keys.py keygen        # generate WALLET_ENCRYPTION_KEY
  python scripts/generate_keys.py webhook       # set Telegram webhook URL
"""
from __future__ import annotations

import os
import sys

import httpx
from dotenv import load_dotenv
load_dotenv()


def keygen():
    from cryptography.fernet import Fernet
    key = Fernet.generate_key().decode()
    print("\n✓ Generated WALLET_ENCRYPTION_KEY:")
    print(f"  {key}")
    print("\nAdd this to Railway env vars and your .env file as:")
    print(f"  WALLET_ENCRYPTION_KEY={key}\n")


def set_webhook():
    token = os.environ.get("PLATFORM_BOT_TOKEN")
    api_url = os.environ.get("API_URL")  # e.g. https://4gent-api.railway.app

    if not token:
        print("ERROR: PLATFORM_BOT_TOKEN not set")
        sys.exit(1)
    if not api_url:
        print("ERROR: API_URL not set (your Railway deployment URL)")
        sys.exit(1)

    webhook_url = f"{api_url.rstrip('/')}/webhook/telegram"
    url = f"https://api.telegram.org/bot{token}/setWebhook"

    resp = httpx.post(url, json={"url": webhook_url})
    data = resp.json()

    if data.get("ok"):
        print(f"✓ Webhook set to: {webhook_url}")
    else:
        print(f"✗ Failed: {data}")


def delete_webhook():
    token = os.environ.get("PLATFORM_BOT_TOKEN")
    if not token:
        print("ERROR: PLATFORM_BOT_TOKEN not set")
        sys.exit(1)
    resp = httpx.post(f"https://api.telegram.org/bot{token}/deleteWebhook")
    print(resp.json())


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "help"
    if cmd == "keygen":
        keygen()
    elif cmd == "webhook":
        set_webhook()
    elif cmd == "delete-webhook":
        delete_webhook()
    else:
        print("Commands: keygen | webhook | delete-webhook")
