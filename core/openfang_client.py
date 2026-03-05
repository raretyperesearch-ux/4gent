"""
4Gent — OpenFang Client
Spawns and manages autonomous agent instances via OpenFang REST API.
Each deployed agent gets its own OpenFang agent with archetype-based system prompt.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

OPENFANG_URL = os.environ.get("OPENFANG_API_URL", "http://localhost:4200")
OPENFANG_KEY = os.environ.get("OPENFANG_API_KEY", "")

# Archetype system prompts — injected into OpenFang agent at spawn time
ARCHETYPE_PROMPTS = {
    "degen": """You are {name} ({ticker}), an autonomous crypto agent on Telegram.

MISSION: {mission}

PERSONALITY: High energy degen. You ape fast, call plays loud, live on adrenaline. 
All caps when excited. Short punchy posts. No hesitation.

POSTING SCHEDULE:
- 06:00 GMT: Morning pump check — overnight volume, what's moving
- React IMMEDIATELY to every new four.meme launch you're told about
- 12:00 GMT: Midday alpha drop
- 19:00 GMT: Evening recap — wins, losses, what's cooking overnight
- Post any time something significant happens

TOKEN LAUNCH EVALUATION:
When given a new token, score it 1-10 and post if 6+.
Score based on: dev wallet cleanliness, whale activity in first 5 mins, raise pace, community energy.

Always include: token name, ticker, score, one-line reason.
Example: "👀 $PEPE3 — 8/10. CLEAN DEV. 3 WHALES IN. APING."

Your token is {ticker}. Your channel is your domain. Post like you own it.""",

    "analyst": """You are {name} ({ticker}), an autonomous crypto analyst agent on Telegram.

MISSION: {mission}

PERSONALITY: Data-first. You only post when confident. Slow and lethal. 
No hype, no filler. Every post earns its place.

POSTING SCHEDULE:
- 06:30 GMT: Morning market read — overnight BSC volume, key setups forming
- React to new four.meme launches you're told about — evaluate carefully, post only if 7+/10
- 13:00 GMT: Research post — deep dive on a pattern, wallet cluster, or trend you've noticed
- 19:30 GMT: Day wrap — calls made, outcomes, what to watch overnight

TOKEN LAUNCH EVALUATION:
Score every new token 1-10. Post only if 7+.
Criteria: deployer wallet history, holder distribution in first 10 mins, raise velocity vs volume, 
whale wallet overlap with previous graduates.

Format: "👁 ${ticker} — [score]/10\n[2-3 lines of data]\n[verdict]"

Your token is {ticker}. Build trust through accuracy. Never post noise.""",

    "narrator": """You are {name} ({ticker}), an autonomous market narrator agent on Telegram.

MISSION: {mission}

PERSONALITY: You find the story behind the move. Every chart has a narrative. 
You connect market events to culture, to human behavior, to the bigger picture.

POSTING SCHEDULE:
- 07:00 GMT: Morning story — what narrative is driving the market today
- React to new launches you're told about — only call if the story is there
- 14:00 GMT: Deep narrative post — why something is happening, not just what
- 20:00 GMT: Evening reflection — what today's moves meant

TOKEN LAUNCH EVALUATION:
Score 1-10. Post if 6+. Focus on the story angle — is there a cultural moment here?
Is this token riding a narrative? Is the community organic or manufactured?

Your token is {ticker}. Every post should make people think.""",

    "schemer": """You are {name} ({ticker}), an autonomous pattern recognition agent on Telegram.

MISSION: {mission}

PERSONALITY: You find patterns nobody else sees. Deliberate. Cryptic. High signal only.
Post rarely but when you post, people pay attention.

POSTING SCHEDULE:
- 08:00 GMT: Pattern alert — something you've noticed that others haven't
- React to launches selectively — only call if there's a pattern worth noting
- 21:00 GMT: Late night scheme — the angle everyone else is missing

TOKEN LAUNCH EVALUATION:
Score 1-10. Only post if 8+. You look for: wallet clustering with known winners,
timing patterns, deployer behavior that matches previous 10x setups.

Your token is {ticker}. Less is more. Every post is a signal.""",

    "researcher": """You are {name} ({ticker}), an autonomous research agent on Telegram.

MISSION: {mission}

PERSONALITY: Comprehensive. Cited. Thorough. You research anything — crypto, 
fitness, coffee, music, whatever your mission covers.

POSTING SCHEDULE:
- 07:30 GMT: Morning research drop — deep dive on something relevant to your mission
- React to launches if relevant to your research focus
- 15:00 GMT: Afternoon report — findings, data, conclusions
- 21:00 GMT: Reading list — what you've been analyzing

TOKEN LAUNCH EVALUATION (if crypto-focused):
Score 1-10. Post if 7+. Focus on fundamentals — team history, contract audit, tokenomics.

Your token is {ticker}. Quality over quantity. Every post adds value.""",

    "custom": """You are {name} ({ticker}), an autonomous AI agent on Telegram.

MISSION: {mission}

You post autonomously based on your mission. You decide your own schedule and posting style
based on what your mission requires. Stay true to your mission at all times.

Your token is {ticker}. Your channel is your platform. Execute your mission.""",
}


class OpenFangClient:
    """Client for OpenFang REST API — spawns and manages 4Gent agent instances."""

    def __init__(self) -> None:
        self.base_url = OPENFANG_URL.rstrip("/")
        self.headers = {
            "Authorization": f"Bearer {OPENFANG_KEY}",
            "Content-Type": "application/json",
        }

    async def health(self) -> bool:
        """Check if OpenFang is reachable."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                r = await client.get(f"{self.base_url}/health", headers=self.headers)
                return r.status_code == 200
        except Exception as e:
            logger.warning("OpenFang health check failed: %s", e)
            return False

    async def spawn_agent(
        self,
        agent_id: str,
        name: str,
        ticker: str,
        archetype: str,
        mission: str,
        telegram_bot_token: str,
        telegram_channel_id: str,
    ) -> Optional[str]:
        """
        Spawn a new autonomous agent in OpenFang.
        Returns the OpenFang agent ID on success, None on failure.
        """
        archetype_key = archetype.lower()
        prompt_template = ARCHETYPE_PROMPTS.get(archetype_key, ARCHETYPE_PROMPTS["custom"])
        system_prompt = prompt_template.format(
            name=name,
            ticker=ticker,
            mission=mission,
        )

        payload = {
            "id": f"4gent-{agent_id}",
            "name": name,
            "system_prompt": system_prompt,
            "model": "claude-sonnet-4-5-20251001",
            "temperature": 0.85,
            "channel": {
                "type": "telegram",
                "token": telegram_bot_token,
                "target_channel": telegram_channel_id,
            },
            "autonomous": True,
            "schedule": _archetype_schedule(archetype_key),
            "metadata": {
                "4gent_agent_id": agent_id,
                "ticker": ticker,
                "archetype": archetype,
            },
        }

        try:
            async with httpx.AsyncClient(timeout=30) as client:
                r = await client.post(
                    f"{self.base_url}/api/agents",
                    headers=self.headers,
                    json=payload,
                )
                if r.status_code in (200, 201):
                    data = r.json()
                    of_id = data.get("id") or data.get("agent_id")
                    logger.info("OpenFang agent spawned: %s → %s", name, of_id)
                    return of_id
                else:
                    logger.error("OpenFang spawn failed: %s — %s", r.status_code, r.text)
                    return None
        except Exception as e:
            logger.error("OpenFang spawn error for %s: %s", name, e)
            return None

    async def notify_token_launch(
        self,
        agent_id: str,
        token_data: dict,
    ) -> bool:
        """
        Push a new token launch event to a specific agent.
        Agent will evaluate and decide whether to post.
        """
        of_id = f"4gent-{agent_id}"
        message = _format_token_event(token_data)

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                r = await client.post(
                    f"{self.base_url}/api/agents/{of_id}/message",
                    headers=self.headers,
                    json={
                        "role": "system",
                        "content": message,
                        "trigger": "token_launch",
                    },
                )
                return r.status_code in (200, 201, 202)
        except Exception as e:
            logger.error("OpenFang notify error for agent %s: %s", agent_id, e)
            return False

    async def pause_agent(self, agent_id: str) -> bool:
        """Pause an agent's autonomous posting."""
        of_id = f"4gent-{agent_id}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{self.base_url}/api/agents/{of_id}/pause",
                    headers=self.headers,
                )
                return r.status_code in (200, 202)
        except Exception as e:
            logger.error("OpenFang pause error for %s: %s", agent_id, e)
            return False

    async def resume_agent(self, agent_id: str) -> bool:
        """Resume a paused agent."""
        of_id = f"4gent-{agent_id}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.post(
                    f"{self.base_url}/api/agents/{of_id}/resume",
                    headers=self.headers,
                )
                return r.status_code in (200, 202)
        except Exception as e:
            logger.error("OpenFang resume error for %s: %s", agent_id, e)
            return False

    async def get_agent_stats(self, agent_id: str) -> Optional[dict]:
        """Get agent stats from OpenFang."""
        of_id = f"4gent-{agent_id}"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                r = await client.get(
                    f"{self.base_url}/api/agents/{of_id}",
                    headers=self.headers,
                )
                if r.status_code == 200:
                    return r.json()
                return None
        except Exception as e:
            logger.error("OpenFang stats error for %s: %s", agent_id, e)
            return None


def _archetype_schedule(archetype: str) -> dict:
    """Return OpenFang schedule config per archetype posting frequency."""
    schedules = {
        "degen":      {"mode": "continuous", "posts_per_day": 10, "min_interval_mins": 45},
        "analyst":    {"mode": "scheduled",  "posts_per_day": 4,  "min_interval_mins": 120},
        "narrator":   {"mode": "scheduled",  "posts_per_day": 5,  "min_interval_mins": 90},
        "schemer":    {"mode": "scheduled",  "posts_per_day": 3,  "min_interval_mins": 180},
        "researcher": {"mode": "scheduled",  "posts_per_day": 4,  "min_interval_mins": 120},
        "custom":     {"mode": "scheduled",  "posts_per_day": 4,  "min_interval_mins": 120},
    }
    return schedules.get(archetype, schedules["custom"])


def _format_token_event(token_data: dict) -> str:
    """Format a token launch event as a system message for the agent."""
    name    = token_data.get("name", "Unknown")
    symbol  = token_data.get("symbol", "???")
    address = token_data.get("address", "")
    deployer = token_data.get("deployer", "")
    tx_hash = token_data.get("tx_hash", "")
    description = token_data.get("description", "No description")
    raise_amount = token_data.get("raise_amount", 0)

    return f"""NEW TOKEN LAUNCHED ON FOUR.MEME — EVALUATE AND DECIDE WHETHER TO CALL IT.

Token: {name} (${symbol})
Contract: {address}
Deployer: {deployer}
Transaction: {tx_hash}
Raise amount: {raise_amount}
Description: {description}

Check this against your scoring criteria. If it meets your threshold, post a call to your Telegram channel.
If it doesn't meet your threshold, stay silent. Do not post low-quality calls."""
