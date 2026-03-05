"""
4Gent — Launch Pipeline Test
Tests every step of the launch pipeline in isolation (dry-run mode).
Does NOT submit real transactions or post to Telegram.

Usage:
  python scripts/test_launch.py
"""
from __future__ import annotations

import asyncio
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv
load_dotenv()


async def test_wallet_creation():
    print("\n── Test 1: Wallet Creation ──────────────────")
    from core.wallet import create_agent_wallet, decrypt_key
    wallet = create_agent_wallet()
    assert wallet.address.startswith("0x"), "Address must start with 0x"
    assert len(wallet.address) == 42, "Address must be 42 chars"
    decrypted = decrypt_key(wallet.encrypted_key)
    assert decrypted == wallet.private_key, "Decrypt must match original key"
    print(f"  ✓ Wallet: {wallet.address}")
    print(f"  ✓ Encryption/decryption: OK")
    return wallet


async def test_claude_brain():
    print("\n── Test 2: Claude Brain ─────────────────────")
    from core.claude_brain import ClaudeBrain
    brain = ClaudeBrain(
        archetype="analyst",
        agent_name="TestAgent",
        agent_ticker="TEST",
        custom_prompt="Call only high-quality BSC launches with strong fundamentals",
        trading_enabled=False,
        max_trade_bnb=0.1,
    )

    token_data = {
        "address": "0xDEADBEEF0000000000000000000000000000DEAD",
        "deployer": "0x1234567890abcdef1234567890abcdef12345678",
        "name": "PepeBSC",
        "symbol": "PEPEBSC",
        "description": "The degen pepe on BSC",
        "raise_amount": 0.05,
        "block_time": "2026-03-05T00:00:00Z",
    }

    evaluation = brain.evaluate_token(token_data)
    print(f"  ✓ Evaluation: score={evaluation.score:.1f} post={evaluation.should_post}")
    print(f"  ✓ Reasoning: {evaluation.reasoning[:80]}...")

    posts = brain.generate_intro_posts()
    assert len(posts) == 3, f"Expected 3 intro posts, got {len(posts)}"
    print(f"  ✓ Intro posts: {len(posts)} generated")
    for i, p in enumerate(posts):
        print(f"    Post {i+1}: {p[:80]}...")


async def test_supabase_connection():
    print("\n── Test 3: Supabase Connection ──────────────")
    from supabase import create_client
    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    # Test read
    resp = db.table("agents").select("id").limit(1).execute()
    print(f"  ✓ agents table: reachable")

    resp = db.table("bot_pool").select("id").limit(1).execute()
    print(f"  ✓ bot_pool table: reachable ({len(resp.data or [])} rows)")

    resp = db.table("agent_posts").select("id").limit(1).execute()
    print(f"  ✓ agent_posts table: reachable")


async def test_telegram_bot():
    print("\n── Test 4: Telegram Platform Bot ────────────")
    import httpx
    token = os.environ.get("PLATFORM_BOT_TOKEN", "")
    if not token:
        print("  ⚠ PLATFORM_BOT_TOKEN not set — skipping")
        return

    async with httpx.AsyncClient(timeout=10) as client:
        r = await client.get(f"https://api.telegram.org/bot{token}/getMe")
        data = r.json()
        if data.get("ok"):
            bot = data["result"]
            print(f"  ✓ Bot: @{bot['username']} (id={bot['id']})")
        else:
            print(f"  ✗ Bot auth failed: {data}")


async def test_api_health():
    print("\n── Test 5: API Health (if running locally) ──")
    import httpx
    api_url = os.environ.get("API_URL", "http://localhost:8000")
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(f"{api_url}/health")
            print(f"  ✓ Health: {r.json()}")
    except Exception as e:
        print(f"  ⚠ API not reachable at {api_url}: {e}")
        print("    (Start server with: uvicorn api.server:app --reload)")


async def main():
    print("=" * 50)
    print("4Gent Launch Pipeline Test")
    print("=" * 50)

    required = ["ANTHROPIC_API_KEY", "SUPABASE_URL", "SUPABASE_SERVICE_KEY", "WALLET_ENCRYPTION_KEY"]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        print(f"\n✗ Missing env vars: {', '.join(missing)}")
        print("  Copy .env.example to .env and fill in values")
        sys.exit(1)

    try:
        await test_wallet_creation()
        await test_claude_brain()
        await test_supabase_connection()
        await test_telegram_bot()
        await test_api_health()
        print("\n" + "=" * 50)
        print("✓ All tests passed")
        print("=" * 50)
    except Exception as e:
        print(f"\n✗ Test failed: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(main())
