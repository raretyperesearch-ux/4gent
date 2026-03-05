# 4Gent — AI Agent Launchpad

Launch autonomous AI agents with tokens on four.meme in 60 seconds.

## Quick Start

```bash
git clone https://github.com/raretyperesearch-ux/4gent.git
cd 4gent
pip install -r requirements.txt
cp .env.example .env
# Fill in .env values
```

## First-Time Setup

**1. Generate wallet encryption key**
```bash
python scripts/generate_keys.py keygen
# Copy output → WALLET_ENCRYPTION_KEY in .env + Railway
```

**2. Seed bot pool** (after creating bots via @BotFather)
```bash
# Create bots.csv:  @4GentAgent1Bot,7123456789:AAF...token
python scripts/seed_bot_pool.py bots.csv
```

**3. Set Telegram webhook** (after Railway deploy)
```bash
API_URL=https://your-app.railway.app python scripts/generate_keys.py webhook
```

**4. Run tests**
```bash
python scripts/test_launch.py
```

## Run Locally

```bash
uvicorn api.server:app --reload
# API at http://localhost:8000
# Docs at http://localhost:8000/docs
```

## Deploy

**Railway** — connect GitHub repo, set env vars from `.env.example`, deploy.  
**Vercel** — connect same repo, set root directory to `web/`.

## Structure

```
4gent/
├── core/
│   ├── launch.py       ← full agent launch pipeline
│   ├── monitor.py      ← Bitquery websocket (four.meme stream)
│   ├── scheduler.py    ← 24/7 agent runtime
│   ├── telegram.py     ← bot pool + owner commands
│   ├── claude_brain.py ← Claude-powered decision engine
│   ├── erc8004.py      ← ERC-8004 registration
│   └── wallet.py       ← BSC wallet creation + encryption
├── api/
│   └── server.py       ← FastAPI
├── packages/
│   ├── fourmeme/       ← forked fourmeme-py
│   └── agent/          ← forked four-meme-agent
├── supabase/
│   └── schema.sql      ← DB schema (already deployed)
├── scripts/
│   ├── seed_bot_pool.py
│   ├── generate_keys.py
│   └── test_launch.py
├── web/                ← Next.js wizard (connect to /launch API)
├── requirements.txt
├── railway.toml
└── Procfile
```

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| GET | /health | Health check |
| POST | /launch | Launch new agent |
| GET | /agent/:id | Poll agent status |
| POST | /agent/:id/pause | Pause agent |
| POST | /agent/:id/resume | Resume agent |
| POST | /agent/:id/delete | Delete agent |
| GET | /agent/:id/stats | Agent stats |
| POST | /verify-channel | Pre-launch Telegram check |
| POST | /webhook/telegram | @4GentBot webhook |
| GET | /admin/agents | List all agents |
| GET | /admin/bot-pool | Bot pool status |

## Supabase Project

Project ID: `seartddspffufwiqzwvh` (us-east-1)  
Schema: deployed ✓

## Notes

- **ERC-8004**: Contract address not yet published by four.meme. Set `ERC8004_CONTRACT_ADDRESS` env var once available — registration will activate automatically.
- **Bot pool**: Pre-create bots via @BotFather. Each agent gets one assigned at launch.
- **Trading**: PancakeSwap V2 Router. Set `trading_enabled=false` for caller-only agents.
- **Fees**: 3% tax — 2% platform, 1% owner. Fee payout script TBD (phase 2).
