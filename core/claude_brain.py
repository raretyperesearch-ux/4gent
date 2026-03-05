"""
4Gent — Claude Brain
Anthropic-powered decision engine for all 4Gent agents.
Replaces OpenAI calls in forked four-meme-agent brain.
"""
from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from typing import Optional

import anthropic
import asyncio

logger = logging.getLogger(__name__)

ARCHETYPE_PERSONAS = {
    "degen": (
        "You are a degen crypto trader on BSC. You ape fast, call high-risk plays, "
        "and live on adrenaline. You only call tokens with strong momentum signals. "
        "Short bursts, high energy, CAPS when excited."
    ),
    "analyst": (
        "You are a precision on-chain analyst. You score every launch 1-10. "
        "Only 7+ gets a call. You check wallet patterns, bonding curve velocity. "
        "Data-first, no hype, concise."
    ),
    "narrator": (
        "You are a crypto narrative specialist. You find the story behind every move. "
        "You connect tokens to cultural moments and meta trends. Flair and insight."
    ),
    "schemer": (
        "You are a pattern-recognition specialist. You find signals nobody else sees. "
        "You post rarely but precisely. Cryptic but compelling."
    ),
    "researcher": (
        "You are a deep researcher. You go long on things most people scroll past. "
        "Substantive, never filler."
    ),
    "custom": (
        "You are an autonomous AI agent. Follow your creator's mission exactly."
    ),
}


@dataclass
class TokenEvaluation:
    should_post: bool
    should_trade: bool
    score: float
    reasoning: str
    post_text: str
    trade_amount_bnb: float = 0.0


class ClaudeBrain:
    """One instance per active AgentRuntime."""

    def __init__(
        self,
        archetype: str,
        agent_name: str,
        agent_ticker: str,
        custom_prompt: str,
        trading_enabled: bool,
        max_trade_bnb: float,
        api_key: Optional[str] = None,
    ) -> None:
        self.archetype = archetype
        self.agent_name = agent_name
        self.agent_ticker = agent_ticker
        self.custom_prompt = custom_prompt
        self.trading_enabled = trading_enabled
        self.max_trade_bnb = max_trade_bnb
        self._client = anthropic.AsyncAnthropic(  # O-01: use async client to avoid blocking event loop
            api_key=api_key or os.environ["ANTHROPIC_API_KEY"]
        )

    def _system(self) -> str:
        persona = ARCHETYPE_PERSONAS.get(self.archetype, ARCHETYPE_PERSONAS["custom"])
        return (
            f"You are {self.agent_name} (${self.agent_ticker}), an autonomous AI agent on 4Gent.\n\n"
            f"PERSONA: {persona}\n\n"
            f"MISSION: {self.custom_prompt}\n\n"
            "Respond with valid JSON only. No markdown. No preamble."
        )

    async def _call(self, user_msg: str, max_tokens: int = 1024) -> dict:
        """O-01: async Anthropic call. O-03: json.loads inside try. O-07: correct model ID."""
        msg = await self._client.messages.create(
            model="claude-sonnet-4-5-20251001",  # O-07: correct model ID
            max_tokens=max_tokens,
            system=self._system(),
            messages=[{"role": "user", "content": user_msg}],
        )
        raw = msg.content[0].text.strip()
        # O-03: strip markdown fences Claude sometimes adds despite instructions
        if raw.startswith("```"):
            raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        return json.loads(raw)  # inside try in callers

    async def evaluate_token(self, token_data: dict, wallet_balance_bnb: float = 0.0) -> TokenEvaluation:  # O-01: now async
        """Evaluate a new four.meme token. Decide whether to post and/or trade."""
        prompt = f"""New token launched on four.meme (BSC):

Name: {token_data.get('name', 'unknown')}
Symbol: {token_data.get('symbol', 'unknown')}
Address: {token_data.get('address', '')}
Deployer: {token_data.get('deployer', '')}
Description: {token_data.get('description', '')}
Raise amount: {token_data.get('raise_amount', 0)} BNB
Block time: {token_data.get('block_time', '')}

Your wallet balance: {wallet_balance_bnb:.4f} BNB
Trading enabled: {self.trading_enabled}
Max trade size: {self.max_trade_bnb} BNB

Evaluate based on your persona and mission. Return JSON:
{{
  "should_post": true,
  "should_trade": false,
  "score": 7.5,
  "reasoning": "why",
  "post_text": "your telegram post (max 400 chars, match your voice exactly, empty string if not posting)",
  "trade_amount_bnb": 0.0
}}

Rules:
- Only set should_trade=true if should_post=true AND trading is enabled
- trade_amount_bnb must be <= {self.max_trade_bnb}
- post_text must sound exactly like your persona"""

        try:
            data = await self._call(prompt)
            return TokenEvaluation(
                should_post=bool(data.get("should_post", False)),
                should_trade=bool(data.get("should_trade", False)) and self.trading_enabled,
                score=float(data.get("score", 0)),
                reasoning=data.get("reasoning", ""),
                post_text=data.get("post_text", ""),
                trade_amount_bnb=min(float(data.get("trade_amount_bnb", 0)), self.max_trade_bnb),
            )
        except Exception as e:
            logger.error("[%s] Token eval failed: %s", self.agent_name, e)
            return TokenEvaluation(
                should_post=False, should_trade=False,
                score=0, reasoning=str(e), post_text=""
            )

    async def close(self) -> None:
        """P-08: close the underlying AsyncAnthropic httpx client to prevent resource leak."""
        try:
            await self._client.close()
        except Exception:
            pass

    async def generate_intro_posts(self) -> list[str]:  # O-01: now async
        """Generate 3 opening posts for channel launch."""
        prompt = """Generate exactly 3 opening Telegram posts for your channel launch.

Post 1: You're live. Introduce yourself. (max 300 chars)
Post 2: Explain your protocol — how you operate, what you call. (max 300 chars)
Post 3: Standby message — you're now watching the market. (max 200 chars)

Match your persona voice exactly. Return JSON:
{"posts": ["post1", "post2", "post3"]}"""

        try:
            data = await self._call(prompt)
            posts = data.get("posts", [])
            if len(posts) == 3:
                return posts
        except Exception as e:
            logger.error("[%s] Intro post generation failed: %s", self.agent_name, e)

        # Fallback
        return [
            f"{self.agent_name.upper()} IS LIVE. ${self.agent_ticker}. WATCHING.",
            f"PROTOCOL: {self.custom_prompt[:150]}",
            "STANDBY. FIRST CALL INCOMING.",
        ]
