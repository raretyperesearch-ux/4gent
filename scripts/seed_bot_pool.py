"""
4Gent — Bot Pool Seeder
Run this once to populate bot_pool table from a CSV of bot tokens.

CSV format (no header):
  @4GentAgent1Bot,7123456789:AAF...token...

Usage:
  python scripts/seed_bot_pool.py bots.csv
"""
from __future__ import annotations

import csv
import os
import sys

from dotenv import load_dotenv
load_dotenv()

from supabase import create_client


def seed(csv_path: str) -> None:
    db = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_KEY"])

    with open(csv_path, newline="") as f:
        rows = list(csv.reader(f))

    records = []
    for row in rows:
        if len(row) < 2:
            continue
        username = row[0].strip().lstrip("@")
        token = row[1].strip()
        records.append({
            "bot_username": f"@{username}",
            "bot_token": token,
            "available": True,
        })

    if not records:
        print("No records found in CSV")
        return

    result = db.table("bot_pool").insert(records).execute()
    print(f"Inserted {len(records)} bots into bot_pool")
    for r in (result.data or []):
        print(f"  ✓ {r['bot_username']}")


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python seed_bot_pool.py bots.csv")
        sys.exit(1)
    seed(sys.argv[1])
