"""
Base agent class for MonadBlitz AI agent nodes.

Each agent:
1. Registers itself with the orchestrator on startup
2. Subscribes to Redis channels for queries matching its capabilities
3. Reads full shared task memory before responding
4. Calls its LLM to generate a structured response
5. Submits response back to orchestrator via HTTP
"""
import asyncio
import hashlib
import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Optional

import aiohttp
import redis.asyncio as aioredis
from eth_account import Account

from .config import settings

logger = logging.getLogger("agents")


class BaseAgent(ABC):
    # Override in subclasses
    name: str = "BaseAgent"
    capabilities: list[str] = ["general"]
    tier: str = "beta"

    def __init__(self, private_key: Optional[str] = None):
        self.private_key = private_key or "0x" + "0" * 63 + "1"
        self.account = Account.from_key(self.private_key)
        self.address = self.account.address
        self.logger = logging.getLogger(f"agents.{self.name.lower()}")
        self._running = False

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    async def start(self) -> None:
        self.logger.info(
            f"[{self.name}] Starting | address: {self.address} | "
            f"tier: {self.tier} | capabilities: {self.capabilities}"
        )
        self._running = True

        await self._register()

        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        pubsub = redis_client.pubsub()

        channels = (
            ["queries:all", "sub_queries:broadcast"]
            + [f"queries:{cap}" for cap in self.capabilities]
        )
        await pubsub.subscribe(*channels)
        # Use psubscribe for dynamic peer_review channels (peer_review:<query_id>)
        await pubsub.psubscribe("peer_review:*")
        self.logger.info(f"[{self.name}] Subscribed to: {channels} + peer_review:*")

        async for message in pubsub.listen():
            if not self._running:
                break
            msg_type = message.get("type", "")
            if msg_type not in ("message", "pmessage"):
                continue
            try:
                data = json.loads(message["data"])
                channel = message.get("channel", "") or message.get("pattern", "")
                if channel == "sub_queries:broadcast":
                    asyncio.create_task(self._handle_sub_query(data))
                elif isinstance(channel, str) and channel.startswith("peer_review:"):
                    asyncio.create_task(self._handle_peer_review_request(data))
                else:
                    asyncio.create_task(self._handle_query(data))
            except json.JSONDecodeError:
                pass
            except Exception as e:
                self.logger.error(
                    f"[{self.name}] Error handling message: {e}", exc_info=True
                )

        await redis_client.aclose()

    async def stop(self) -> None:
        self._running = False

    # ── Registration ──────────────────────────────────────────────────────────

    async def _register(self) -> None:
        async with aiohttp.ClientSession() as session:
            # Check if already registered
            try:
                async with session.get(
                    f"{settings.ORCHESTRATOR_BASE_URL}/api/agents/{self.address}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("active"):
                            self.logger.info(f"[{self.name}] Already registered ✓")
                            return
            except Exception:
                pass

            # Register
            try:
                payload = {
                    "address": self.address,
                    "name": self.name,
                    "capabilities": self.capabilities,
                    "tier": self.tier,
                    "metadata_uri": f"ipfs://monadblitz/{self.name.lower()}",
                    "private_key": self.private_key,
                }
                async with session.post(
                    f"{settings.ORCHESTRATOR_BASE_URL}/api/agents/register",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 201):
                        self.logger.info(f"[{self.name}] Registered ✓")
                    else:
                        body = await resp.text()
                        self.logger.warning(
                            f"[{self.name}] Registration {resp.status}: {body[:200]}"
                        )
            except Exception as e:
                self.logger.warning(f"[{self.name}] Registration error: {e}")

    # ── Query handling ────────────────────────────────────────────────────────

    async def _handle_query(self, data: dict) -> None:
        query_id = data.get("query_id")
        problem = data.get("problem", "")
        round_num = data.get("round", 1)

        if not query_id or not problem:
            return

        self.logger.info(
            f"[{self.name}] Query #{query_id[:8]}... round {round_num} received"
        )

        # Fetch shared memory context
        memory_context = await self._fetch_memory(query_id)
        if memory_context:
            self.logger.info(f"[{self.name}] Memory context loaded ({len(memory_context)} chars)")

        # Generate response
        self.logger.info(f"[{self.name}] Calling LLM...")
        try:
            response = await asyncio.wait_for(
                self.generate_response(problem, memory_context, round_num),
                timeout=settings.RESPONSE_TIMEOUT,
            )
        except asyncio.TimeoutError:
            self.logger.error(f"[{self.name}] LLM timeout for query #{query_id[:8]}")
            return
        except Exception as e:
            self.logger.error(
                f"[{self.name}] LLM error for query #{query_id[:8]}: {e}", exc_info=True
            )
            return

        self.logger.info(
            f"[{self.name}] Response generated "
            f"(confidence={response.get('confidence', 0):.2f})"
        )

        # Submit
        await self._submit_response(query_id, response)

    async def _fetch_memory(self, query_id: str) -> str:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{settings.ORCHESTRATOR_BASE_URL}/api/memory/{query_id}",
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return data.get("context_string", "")
        except Exception as e:
            self.logger.debug(f"[{self.name}] Memory fetch failed: {e}")
        return ""

    async def _submit_response(self, query_id: str, response: dict) -> None:
        response_text = response.get("answer", "") or response.get("response", "")
        reasoning = response.get("reasoning", "")
        confidence = float(response.get("confidence", 0.5))

        response_hash = "0x" + hashlib.sha256(response_text.encode()).hexdigest()

        payload = {
            "agent_address": self.address,
            "response_text": response_text,
            "reasoning": reasoning,
            "confidence": confidence,
            "response_hash": response_hash,
        }

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{settings.ORCHESTRATOR_BASE_URL}/api/queries/{query_id}/respond",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status in (200, 201):
                        self.logger.info(
                            f"[{self.name}] Response submitted for #{query_id[:8]}... ✓"
                        )
                    else:
                        body = await resp.text()
                        self.logger.error(
                            f"[{self.name}] Submit failed {resp.status}: {body[:200]}"
                        )
        except Exception as e:
            self.logger.error(f"[{self.name}] Submit error: {e}")

    # ── Sub-query: request help from other agents ─────────────────────────────

    async def request_sub_query(
        self,
        parent_query_id: str,
        sub_problem: str,
        capabilities: list[str] | None = None,
    ) -> str:
        """
        Ask other agents to help with a sub-problem.
        Returns the best answer within ~8s, or empty string on timeout.
        """
        payload = {
            "requester_address": self.address,
            "sub_problem": sub_problem,
            "capabilities": capabilities or self.capabilities,
        }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{settings.ORCHESTRATOR_BASE_URL}/api/queries/{parent_query_id}/sub-query",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=15),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        answer = data.get("answer", "")
                        by = data.get("answered_by", "unknown")
                        if answer:
                            self.logger.info(
                                f"[{self.name}] Sub-query answered by {by[:10]}..."
                            )
                        return answer
        except Exception as e:
            self.logger.debug(f"[{self.name}] Sub-query failed: {e}")
        return ""

    async def _handle_sub_query(self, data: dict) -> None:
        """Handle an incoming sub-query from another agent."""
        sub_id = data.get("sub_id")
        parent_query_id = data.get("parent_query_id")
        requester = data.get("requester_address", "")
        sub_problem = data.get("sub_problem", "")
        caps = data.get("capabilities", [])

        # Skip if we are the requester or don't have required capabilities
        if requester == self.address:
            return
        if caps and not any(c in self.capabilities for c in caps):
            return

        self.logger.info(
            f"[{self.name}] Sub-query from {requester[:10]}...: {sub_problem[:60]}"
        )

        try:
            result = await asyncio.wait_for(
                self.generate_response(sub_problem, "", 1),
                timeout=min(settings.RESPONSE_TIMEOUT, 8),
            )
        except (asyncio.TimeoutError, Exception) as e:
            self.logger.debug(f"[{self.name}] Sub-query response error: {e}")
            return

        answer = result.get("answer", "")
        confidence = float(result.get("confidence", 0.5))

        if not answer:
            return

        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{settings.ORCHESTRATOR_BASE_URL}/api/queries/"
                    f"{parent_query_id}/sub-query/{sub_id}/respond",
                    json={
                        "agent_address": self.address,
                        "answer": answer,
                        "confidence": confidence,
                    },
                    timeout=aiohttp.ClientTimeout(total=5),
                )
        except Exception as e:
            self.logger.debug(f"[{self.name}] Sub-query respond error: {e}")

    # ── Peer review: score other agents' responses ─────────────────────────────

    async def _handle_peer_review_request(self, data: dict) -> None:
        """Receive a broadcast of responses and submit peer scores."""
        query_id = data.get("query_id")
        round_num = data.get("round", 1)
        responses = data.get("responses", [])

        if not query_id or not responses:
            return

        # Don't review if we only have our own response in the list
        others = [r for r in responses if r.get("agent_address") != self.address]
        if not others:
            return

        self.logger.info(
            f"[{self.name}] Peer review: scoring {len(others)} response(s) for #{query_id[:8]}"
        )

        try:
            reviews = await asyncio.wait_for(
                self.peer_review_responses(query_id, others),
                timeout=min(settings.RESPONSE_TIMEOUT, 15),
            )
        except (asyncio.TimeoutError, Exception) as e:
            self.logger.warning(f"[{self.name}] Peer review failed: {e}")
            return

        if not reviews:
            return

        payload = {"reviewer_address": self.address, "reviews": reviews}
        try:
            async with aiohttp.ClientSession() as session:
                await session.post(
                    f"{settings.ORCHESTRATOR_BASE_URL}/api/queries/{query_id}/peer-review",
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=10),
                )
                self.logger.info(
                    f"[{self.name}] Submitted {len(reviews)} peer review(s) for #{query_id[:8]}"
                )
        except Exception as e:
            self.logger.debug(f"[{self.name}] Peer review submit failed: {e}")

    async def peer_review_responses(
        self, query_id: str, responses: list[dict]
    ) -> list[dict]:
        """
        Score other agents' responses. Override in subclasses.
        Must return: [{"response_id": str, "score": float, "reasoning": str}]
        Default: no-op (skips peer review for this agent).
        """
        return []

    # ── Helpers ───────────────────────────────────────────────────────────────

    @abstractmethod
    async def generate_response(
        self, problem: str, memory_context: str, round_num: int
    ) -> dict:
        """
        Generate a response. Must return:
        {
            "reasoning": str,  # step-by-step thinking
            "answer": str,     # the actual answer
            "confidence": float  # 0.0-1.0
        }
        """

    def _parse_json_response(self, raw: str) -> dict:
        """Extract JSON dict from LLM output, even if wrapped in markdown."""
        raw = re.sub(r"```(?:json)?", "", raw).strip().rstrip("```").strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            # Try to extract JSON object
            match = re.search(r"\{[\s\S]*\}", raw)
            if match:
                try:
                    return json.loads(match.group())
                except json.JSONDecodeError:
                    pass
        # Fallback: treat whole text as answer
        return {"reasoning": "Direct response", "answer": raw, "confidence": 0.5}

    def _build_prompt(self, problem: str, memory_context: str, round_num: int) -> str:
        round_note = ""
        if round_num > 1:
            round_note = (
                f"\n\n⚠ THIS IS ROUND {round_num}. Previous rounds scored too low. "
                "You MUST significantly improve on what came before. "
                "Read the memory context carefully and address the gaps."
            )

        ctx_section = f"\n\n{memory_context}" if memory_context else ""

        return (
            f"Problem:{round_note}\n{problem}"
            f"{ctx_section}\n\n"
            "Respond ONLY in this JSON format:\n"
            '{"reasoning": "your step-by-step thinking", '
            '"answer": "your complete answer", '
            '"confidence": 0.90}'
        )
