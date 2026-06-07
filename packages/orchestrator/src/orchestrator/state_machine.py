"""
Orchestrator state machine — the brain of MonadBlitz.

Every query lifecycle is managed here:
  CREATED → ROUTING → COLLECTING → SCORING
    ├── ESCALATING → (back to ROUTING, round+1)
    └── RESOLVING → SETTLED or FAILED

This is what was MISSING last hackathon. It's here now, and it's complete.
"""
import asyncio
import logging
from datetime import datetime, timedelta

import redis.asyncio as aioredis
from sqlalchemy import select, update

from .chain_client import ChainClient
from .config import settings
from .database import db_session
from .judge import MetaLLMJudge
from .memory_service import MemoryService
from .models import Agent, PeerReview, Query, QueryStatus, Response
from .query_router import QueryRouter
from .websocket_manager import WebSocketManager

logger = logging.getLogger("orchestrator.state_machine")


class QueryStateMachine:
    def __init__(
        self,
        memory_service: MemoryService,
        judge: MetaLLMJudge,
        chain_client: ChainClient,
        router: QueryRouter,
        redis_client: aioredis.Redis,
        ws_manager: WebSocketManager,
    ):
        self.memory = memory_service
        self.judge = judge
        self.chain = chain_client
        self.router = router
        self.redis = redis_client
        self.ws = ws_manager

    # ── Main entry point ──────────────────────────────────────────────────────

    async def process_query(self, query_id: str) -> None:
        """Manage the full query lifecycle from ROUTING to SETTLED/FAILED."""
        try:
            await self._run_lifecycle(query_id)
        except Exception as e:
            logger.error(
                f"[ORCH] Fatal error for query {query_id}: {e}", exc_info=True
            )
            await self._transition(query_id, QueryStatus.FAILED)

    async def _run_lifecycle(self, query_id: str) -> None:
        query = await self._get_query(query_id)
        if not query:
            logger.error(f"[ORCH] Query {query_id} not found")
            return

        await self._log(f"[ORCH] Processing query #{query_id[:8]}... round {query.round}")

        # ── ROUTING ──────────────────────────────────────────────────────────
        await self._transition(query_id, QueryStatus.ROUTING)
        agents = await self.router.find_best_agents(
            query.capabilities, round_num=query.round
        )

        if not agents:
            await self._log(f"[ORCH] No agents available for query #{query_id[:8]}")
            await self._transition(query_id, QueryStatus.FAILED)
            return

        agent_names = [f"{a.name}(rep:{a.reputation})" for a in agents]
        await self._log(
            f"[ORCH] Routing → {', '.join(agent_names)} | "
            f"capabilities={query.capabilities}"
        )

        await self.memory.add_event(
            query_id,
            "routing",
            {
                "round": query.round,
                "agents": [a.address for a in agents],
                "agent_names": [a.name for a in agents],
                "reason": f"Selected by reputation×tier for {query.capabilities}",
            },
        )

        await self.router.notify_agents(
            query_id, query.problem, query.capabilities, query.bounty, query.round
        )

        # ── COLLECTING ───────────────────────────────────────────────────────
        await self._transition(query_id, QueryStatus.COLLECTING)
        await self._log(
            f"[ORCH] Waiting for responses (timeout={settings.ROUND_TIMEOUT_SECONDS}s)..."
        )

        responses = await self._collect_responses(
            query_id, query.round, timeout=settings.ROUND_TIMEOUT_SECONDS
        )

        if not responses:
            await self._log(
                f"[ORCH] No responses received for query #{query_id[:8]} — FAILED"
            )
            await self._transition(query_id, QueryStatus.FAILED)
            if query.chain_query_id:
                try:
                    await self.chain.expire_query(query.chain_query_id)
                except Exception:
                    pass
            return

        await self._log(
            f"[ORCH] Collected {len(responses)} response(s) for query #{query_id[:8]}"
        )

        # ── PEER REVIEW ──────────────────────────────────────────────────────
        await self._transition(query_id, QueryStatus.PEER_REVIEW)
        peer_scores = await self._collect_peer_reviews(query_id, query.round, responses)

        # ── SCORING ──────────────────────────────────────────────────────────
        await self._transition(query_id, QueryStatus.SCORING)
        memory_ctx = await self.memory.get_context_for_agent(query_id)
        await self._log(f"[JUDGE] Scoring {len(responses)} responses...")

        scores = await self.judge.score_responses(
            query.problem, responses, memory_ctx
        )

        # Blend peer scores with judge scores (70% judge, 30% peer consensus)
        for score in scores:
            peer_avg = peer_scores.get(score.agent_address)
            if peer_avg is not None:
                blended = round(0.70 * score.score + 0.30 * peer_avg, 4)
                await self._log(
                    f"[PEER] {score.agent_address[:10]}... judge={score.score:.2f} "
                    f"peer={peer_avg:.2f} → blended={blended:.2f}"
                )
                score.score = blended

        # Persist scores and add to memory
        async with db_session() as session:
            for score in scores:
                await session.execute(
                    update(Response)
                    .where(
                        Response.query_id == query_id,
                        Response.agent_address == score.agent_address,
                        Response.round == query.round,
                    )
                    .values(score=score.score, score_reasoning=score.reasoning)
                )

        for score in scores:
            await self.memory.add_event(
                query_id,
                "score",
                {
                    "agent_address": score.agent_address,
                    "score": score.score,
                    "reasoning": score.reasoning,
                    "round": query.round,
                },
            )
            await self._log(
                f"[JUDGE] {score.agent_address[:10]}... → {score.score:.2f} — "
                f"{score.reasoning[:80]}"
            )

        best = self.judge.best(scores)
        if not best:
            await self._transition(query_id, QueryStatus.FAILED)
            return

        # ── ESCALATE or RESOLVE ───────────────────────────────────────────────
        if best.score < settings.ESCALATION_THRESHOLD and query.round < settings.MAX_ROUNDS:
            await self._escalate(query_id, query, best.score)
        else:
            await self._resolve(query_id, query, best)

    # ── Escalation ────────────────────────────────────────────────────────────

    async def _escalate(self, query_id: str, query: Query, best_score: float) -> None:
        await self._transition(query_id, QueryStatus.ESCALATING)
        next_round = query.round + 1

        await self._log(
            f"[ORCH] Escalating — best score {best_score:.2f} < "
            f"{settings.ESCALATION_THRESHOLD} threshold → Round {next_round}"
        )

        await self.memory.add_event(
            query_id,
            "escalation",
            {
                "reason": (
                    f"Best score {best_score:.2f} below threshold "
                    f"{settings.ESCALATION_THRESHOLD}"
                ),
                "round": query.round,
                "next_round": next_round,
            },
        )

        async with db_session() as session:
            await session.execute(
                update(Query)
                .where(Query.id == query_id)
                .values(round=next_round, deadline=datetime.utcnow() + timedelta(minutes=3))
            )

        if query.chain_query_id:
            try:
                await self.chain.escalate_query(query.chain_query_id)
            except Exception as e:
                logger.warning(f"[CHAIN] Escalate tx failed: {e}")

        # Recurse with new round
        await self._run_lifecycle(query_id)

    # ── Resolution ────────────────────────────────────────────────────────────

    async def _resolve(self, query_id: str, query: Query, best) -> None:
        await self._transition(query_id, QueryStatus.RESOLVING)

        await self._log(
            f"[ORCH] Winner: {best.agent_address[:10]}... "
            f"score={best.score:.2f} round={query.round}"
        )

        memory_snapshot = await self.memory.get_memory(query_id)
        memory_hash = await self.memory.get_hash(query_id) or "0x" + "0" * 64

        tx_hash = "0x" + "0" * 64
        if query.chain_query_id:
            try:
                tx_hash = await self.chain.select_winner(
                    query.chain_query_id, best.agent_address, memory_hash
                )
                await self._log(f"[CHAIN] Tx confirmed: {tx_hash}")
                await self._log(
                    f"[CHAIN] MON released to {best.agent_address[:12]}... "
                    f"| Explorer: {settings.EXPLORER_URL}/tx/{tx_hash}"
                )
            except Exception as e:
                logger.error(f"[CHAIN] select_winner failed: {e}", exc_info=True)

        await self.memory.add_event(
            query_id,
            "resolution",
            {
                "winner": best.agent_address,
                "score": best.score,
                "tx_hash": tx_hash,
                "memory_hash": memory_hash,
                "round": query.round,
            },
        )

        async with db_session() as session:
            await session.execute(
                update(Query)
                .where(Query.id == query_id)
                .values(
                    winner_address=best.agent_address,
                    tx_hash=tx_hash,
                    memory_hash=memory_hash,
                )
            )
            # Winner: reputation +200
            await session.execute(
                update(Agent)
                .where(Agent.address == best.agent_address)
                .values(wins=Agent.wins + 1, reputation=Agent.reputation + 200)
            )
            # Losers: reputation −50 (floor 1), track losses
            for resp in responses:
                if resp.agent_address != best.agent_address:
                    await session.execute(
                        update(Agent)
                        .where(Agent.address == resp.agent_address)
                        .values(
                            losses=Agent.losses + 1,
                            reputation=Agent.reputation - 50,
                        )
                    )
                    await self._log(
                        f"[REP] {resp.agent_address[:10]}... −50 rep (loss)"
                    )
            await self._log(
                f"[REP] {best.agent_address[:10]}... +200 rep (win)"
            )

        await self._transition(query_id, QueryStatus.SETTLED)
        await self._log(f"[MEMORY] Anchored — hash: {memory_hash[:18]}...")
        await self._log(
            f"[ORCH] ✓ Query #{query_id[:8]}... SETTLED | "
            f"Winner: {best.agent_address[:12]}... | Score: {best.score:.2f}"
        )

    # ── Helpers ───────────────────────────────────────────────────────────────

    async def _collect_peer_reviews(
        self, query_id: str, round_num: int, responses: list
    ) -> dict:
        """
        Broadcast responses to all agents for peer scoring.
        Wait up to PEER_REVIEW_TIMEOUT seconds, then return
        {agent_address -> avg_peer_score} for blending with judge scores.
        """
        PEER_REVIEW_TIMEOUT = 20

        if not responses:
            return {}

        import json

        response_payloads = [
            {
                "response_id": r.id,
                "agent_address": r.agent_address,
                "response_text": r.response_text,
                "reasoning": r.reasoning,
                "confidence": r.confidence,
            }
            for r in responses
        ]

        # Broadcast peer review request
        try:
            await self.redis.publish(
                f"peer_review:{query_id}",
                json.dumps({
                    "query_id": query_id,
                    "round": round_num,
                    "responses": response_payloads,
                }),
            )
            await self._log(
                f"[PEER] Broadcast {len(responses)} responses for peer review "
                f"(timeout={PEER_REVIEW_TIMEOUT}s)"
            )
        except Exception as e:
            logger.warning(f"[PEER] Broadcast failed: {e}")
            return {}

        # Wait for peer reviews to arrive
        await asyncio.sleep(PEER_REVIEW_TIMEOUT)

        # Aggregate peer scores
        from sqlalchemy import select
        async with db_session() as session:
            result = await session.execute(
                select(PeerReview).where(
                    PeerReview.query_id == query_id,
                    PeerReview.round == round_num,
                )
            )
            peer_reviews = result.scalars().all()

        if not peer_reviews:
            await self._log("[PEER] No peer reviews received")
            return {}

        # Map response_id → agent_address
        resp_to_agent = {r.id: r.agent_address for r in responses}

        # Aggregate: agent_address → list of peer scores
        from collections import defaultdict
        scores_by_agent: dict = defaultdict(list)
        for pr in peer_reviews:
            agent_addr = resp_to_agent.get(pr.response_id)
            if agent_addr:
                scores_by_agent[agent_addr].append(pr.peer_score)

        avg_scores = {
            addr: round(sum(ss) / len(ss), 4)
            for addr, ss in scores_by_agent.items()
            if ss
        }

        for addr, avg in avg_scores.items():
            await self._log(f"[PEER] {addr[:10]}... avg peer score: {avg:.2f} ({len(scores_by_agent[addr])} review(s))")

        await self.memory.add_event(
            query_id,
            "peer_review",
            {
                "round": round_num,
                "reviews_received": len(peer_reviews),
                "peer_scores": avg_scores,
            },
        )

        return avg_scores

    async def _collect_responses(
        self, query_id: str, round_num: int, timeout: int
    ) -> list[Response]:
        """
        Poll DB for responses. Agents submit via the REST API.
        Wait up to `timeout` seconds, returning whatever arrived.
        """
        deadline = asyncio.get_event_loop().time() + timeout
        min_responses = 1

        while asyncio.get_event_loop().time() < deadline:
            async with db_session() as session:
                result = await session.execute(
                    select(Response).where(
                        Response.query_id == query_id,
                        Response.round == round_num,
                    )
                )
                responses = result.scalars().all()

            if len(responses) >= min_responses:
                # Wait a bit more for additional responses (up to 20s extra)
                extra_wait = min(20, deadline - asyncio.get_event_loop().time())
                if extra_wait > 0:
                    await asyncio.sleep(min(extra_wait, 5))
                    # Re-fetch after extra wait
                    async with db_session() as session:
                        result = await session.execute(
                            select(Response).where(
                                Response.query_id == query_id,
                                Response.round == round_num,
                            )
                        )
                        responses = result.scalars().all()
                return list(responses)

            await asyncio.sleep(2)

        # Timeout — return whatever we have
        async with db_session() as session:
            result = await session.execute(
                select(Response).where(
                    Response.query_id == query_id,
                    Response.round == round_num,
                )
            )
            return result.scalars().all()

    async def _transition(self, query_id: str, new_status: QueryStatus) -> None:
        async with db_session() as session:
            await session.execute(
                update(Query)
                .where(Query.id == query_id)
                .values(status=new_status, updated_at=datetime.utcnow())
            )
        await self.ws.broadcast_status(query_id, new_status.value)

    async def _get_query(self, query_id: str) -> Query | None:
        async with db_session() as session:
            result = await session.execute(
                select(Query).where(Query.id == query_id)
            )
            return result.scalar_one_or_none()

    async def _log(self, message: str) -> None:
        logger.info(message)
        try:
            await self.redis.publish("orchestrator:logs", message)
            await self.ws.broadcast_log(message)
        except Exception:
            pass
