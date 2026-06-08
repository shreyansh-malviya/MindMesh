"""
Freelance Track State Machine — orchestrates real-work task delivery.

Flow:
  CREATED
    → TEAM_DISCOVERY  (agents discover the task and bid for subtask roles)
    → TEAM_FORMED     (best-fit bids selected, team announced via Redis)
    → IN_PROGRESS     (each agent generates its deliverable concurrently)
    → ASSEMBLING      (orchestrator assembles artifacts into final deliverable)
    → REVIEW          (LLM judges quality of assembled deliverable)
    → SETTLED         (IPFS upload + on-chain settlement + bounty distribution)
    / FAILED          (timeout, low quality, or no team formed)
    / DISPUTED        (agent challenges result — opens 24h dispute window)

Unlike the proposal track (which uses ChainEventStore), the freelance track
uses SQLite for local state (same as the query track) with optional chain
anchoring for the final settlement.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import Optional

import anthropic
import redis.asyncio as aioredis
from sqlalchemy import select, update

from .config import settings
from .database import db_session
from .ipfs_client import upload_text, ipfs_url
from .models import (
    Agent,
    FreelanceArtifact,
    FreelanceBid,
    FreelanceStatus,
    FreelanceTask,
)
from .websocket_manager import WebSocketManager

logger = logging.getLogger("orchestrator.freelance")

# ── Quality thresholds ─────────────────────────────────────────────────────────

REVIEW_PASS_THRESHOLD = 0.65   # minimum assembled-deliverable score to SETTLE
MIN_TEAM_SIZE = 1              # need at least 1 agent to proceed
ARTIFACT_TIMEOUT_BUFFER = 30   # extra seconds after work deadline before assembling

# ── LLM prompts ────────────────────────────────────────────────────────────────

REVIEW_SYSTEM = """You are a senior technical reviewer evaluating a multi-agent collaborative deliverable.

Score the assembled deliverable on a scale of 0.0 to 1.0 based on:
- Completeness (does it fully address the task? 35%)
- Quality (is it accurate, well-structured, production-ready? 30%)
- Cohesion (do the parts fit together into a unified whole? 20%)
- Actionability (is it immediately useful / usable? 15%)

Return ONLY valid JSON, no other text:
{"score": 0.82, "verdict": "PASS", "summary": "brief 2-sentence assessment", "weaknesses": ["item1", "item2"]}

verdict must be "PASS" (score >= 0.65) or "FAIL" (score < 0.65)."""

ASSEMBLY_SYSTEM = """You are a senior editor assembling a multi-agent collaborative deliverable into a single coherent document.

You will receive individual contributions from multiple agents, each covering a different aspect of the task.
Your job is to:
1. Merge them into one well-structured Markdown document
2. Add a brief ## Overview section at the top
3. Resolve any contradictions between sections
4. Ensure consistent terminology and tone
5. Add a ## Summary section at the end

Return the assembled document as clean Markdown only — no JSON wrapper, no preamble."""


class FreelanceStateMachine:
    """Orchestrates the full lifecycle of a freelance task."""

    def __init__(
        self,
        redis_client: aioredis.Redis,
        ws_manager: WebSocketManager,
        chain_client=None,
    ):
        self.redis = redis_client
        self.ws = ws_manager
        self.chain = chain_client
        self._anthropic = anthropic.AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)
        self._active: dict[str, asyncio.Task] = {}

    # ── Public API ─────────────────────────────────────────────────────────────

    async def start_task(self, task_id: str) -> None:
        """Launch the state machine for a new freelance task in the background."""
        if task_id in self._active:
            logger.warning(f"[FL] Task {task_id[:8]} already running")
            return
        t = asyncio.create_task(self._run(task_id), name=f"fl-{task_id[:8]}")
        self._active[task_id] = t
        t.add_done_callback(lambda _: self._active.pop(task_id, None))

    async def submit_bid(
        self,
        task_id: str,
        agent_address: str,
        agent_name: str,
        proposed_role: str,
        proposed_subtask: str,
        fit_score: float,
        reasoning: str,
    ) -> FreelanceBid:
        """Record an agent's bid — called by the /bid endpoint."""
        async with db_session() as session:
            task = await session.get(FreelanceTask, task_id)
            if not task or task.status not in (
                FreelanceStatus.CREATED, FreelanceStatus.TEAM_DISCOVERY
            ):
                raise ValueError(f"Task {task_id} is not accepting bids (status={task.status if task else 'not found'})")

            # One bid per agent per task
            existing = (await session.execute(
                select(FreelanceBid).where(
                    FreelanceBid.task_id == task_id,
                    FreelanceBid.agent_address == agent_address,
                )
            )).scalar_one_or_none()

            if existing:
                existing.proposed_role = proposed_role
                existing.proposed_subtask = proposed_subtask
                existing.fit_score = fit_score
                existing.reasoning = reasoning
                bid = existing
            else:
                bid = FreelanceBid(
                    task_id=task_id,
                    agent_address=agent_address,
                    agent_name=agent_name,
                    proposed_role=proposed_role,
                    proposed_subtask=proposed_subtask,
                    fit_score=fit_score,
                    reasoning=reasoning,
                )
                session.add(bid)

            await session.commit()
            await session.refresh(bid)

        await self._broadcast(task_id, "freelance_bid", {
            "agent": agent_name or agent_address[:8],
            "role": proposed_role,
            "fit": fit_score,
        })
        logger.info(f"[FL] Bid: {agent_name} → {proposed_role} (fit={fit_score:.2f})")
        return bid

    async def submit_artifact(
        self,
        task_id: str,
        agent_address: str,
        agent_name: str,
        role: str,
        subtask_description: str,
        content: str,
        content_type: str = "markdown",
    ) -> FreelanceArtifact:
        """Record a delivered artifact — called by the /submit endpoint."""
        async with db_session() as session:
            task = await session.get(FreelanceTask, task_id)
            if not task or task.status not in (FreelanceStatus.TEAM_FORMED, FreelanceStatus.IN_PROGRESS):
                raise ValueError(f"Task {task_id} is not accepting artifacts (status={getattr(task, 'status', 'not found')})")

            # One artifact per agent per task
            existing = (await session.execute(
                select(FreelanceArtifact).where(
                    FreelanceArtifact.task_id == task_id,
                    FreelanceArtifact.agent_address == agent_address,
                )
            )).scalar_one_or_none()

            if existing:
                existing.content = content
                existing.content_type = content_type
                existing.submitted_at = datetime.utcnow()
                artifact = existing
            else:
                artifact = FreelanceArtifact(
                    task_id=task_id,
                    agent_address=agent_address,
                    agent_name=agent_name,
                    role=role,
                    subtask_description=subtask_description,
                    content=content,
                    content_type=content_type,
                )
                session.add(artifact)

            await session.commit()
            await session.refresh(artifact)

        await self._broadcast(task_id, "freelance_artifact", {
            "agent": agent_name or agent_address[:8],
            "role": role,
            "len": len(content),
        })
        logger.info(f"[FL] Artifact submitted: {agent_name} → {role} ({len(content)} chars)")
        return artifact

    # ── State machine ──────────────────────────────────────────────────────────

    async def _run(self, task_id: str) -> None:
        try:
            await self._phase_team_discovery(task_id)
            formed = await self._phase_team_formation(task_id)
            if not formed:
                return
            await self._phase_in_progress(task_id)
            deliverable = await self._phase_assembling(task_id)
            if not deliverable:
                return
            passed = await self._phase_review(task_id, deliverable)
            await self._phase_settle(task_id, deliverable, passed)
        except Exception as exc:
            logger.exception(f"[FL] Task {task_id[:8]} crashed: {exc}")
            await self._set_status(task_id, FreelanceStatus.FAILED)
            await self._broadcast(task_id, "freelance_failed", {"reason": str(exc)})

    async def _phase_team_discovery(self, task_id: str) -> None:
        """Broadcast task to agents and wait for bids."""
        await self._set_status(task_id, FreelanceStatus.TEAM_DISCOVERY)

        async with db_session() as session:
            task = await session.get(FreelanceTask, task_id)
            payload = {
                "type": "freelance_task",
                "task_id": task_id,
                "title": task.title,
                "description": task.description,
                "task_type": task.task_type,
                "skills_required": task.skills_required or [],
                "budget": task.budget,
            }

        await self.redis.publish("freelance:broadcast", json.dumps(payload))
        await self._broadcast(task_id, "freelance_team_discovery", {"task_id": task_id})
        logger.info(f"[FL] {task_id[:8]} TEAM_DISCOVERY — waiting {settings.FREELANCE_BIDDING_TIMEOUT}s")
        await asyncio.sleep(settings.FREELANCE_BIDDING_TIMEOUT)

    async def _phase_team_formation(self, task_id: str) -> bool:
        """Select best bid per role (up to MAX_TEAM_SIZE agents)."""
        await self._set_status(task_id, FreelanceStatus.TEAM_FORMED)

        async with db_session() as session:
            bids_result = await session.execute(
                select(FreelanceBid)
                .where(FreelanceBid.task_id == task_id)
                .order_by(FreelanceBid.fit_score.desc())
            )
            all_bids: list[FreelanceBid] = list(bids_result.scalars().all())

        if not all_bids:
            logger.warning(f"[FL] {task_id[:8]} — no bids received, failing")
            await self._set_status(task_id, FreelanceStatus.FAILED)
            await self._broadcast(task_id, "freelance_failed", {"reason": "No agents bid on this task"})
            return False

        # Pick top bid per unique agent (deduplicate), up to MAX_TEAM_SIZE
        seen_agents: set[str] = set()
        selected: list[FreelanceBid] = []
        for bid in all_bids:
            if bid.agent_address not in seen_agents:
                seen_agents.add(bid.agent_address)
                selected.append(bid)
            if len(selected) >= settings.FREELANCE_MAX_TEAM_SIZE:
                break

        team_json = [
            {
                "agent_address": b.agent_address,
                "agent_name": b.agent_name,
                "role": b.proposed_role,
                "subtask_description": b.proposed_subtask,
            }
            for b in selected
        ]

        async with db_session() as session:
            await session.execute(
                update(FreelanceTask)
                .where(FreelanceTask.id == task_id)
                .values(team=team_json, status=FreelanceStatus.TEAM_FORMED)
            )
            for bid in selected:
                await session.execute(
                    update(FreelanceBid)
                    .where(FreelanceBid.id == bid.id)
                    .values(accepted=True)
                )
            await session.commit()

        # Notify selected agents via Redis
        for member in team_json:
            payload = {
                "type": "freelance_assigned",
                "task_id": task_id,
                "agent_address": member["agent_address"],
                "role": member["role"],
                "subtask_description": member["subtask_description"],
            }
            await self.redis.publish("freelance:broadcast", json.dumps(payload))

        await self._broadcast(task_id, "freelance_team_formed", {
            "team": [{"name": m["agent_name"], "role": m["role"]} for m in team_json]
        })
        logger.info(f"[FL] {task_id[:8]} TEAM_FORMED — {len(selected)} agent(s): "
                    + ", ".join(f"{m['agent_name']}→{m['role']}" for m in team_json))
        return True

    async def _phase_in_progress(self, task_id: str) -> None:
        """Wait for agents to submit their artifacts."""
        await self._set_status(task_id, FreelanceStatus.IN_PROGRESS)
        await self._broadcast(task_id, "freelance_in_progress", {"task_id": task_id})
        logger.info(f"[FL] {task_id[:8]} IN_PROGRESS — waiting {settings.FREELANCE_WORK_TIMEOUT}s")
        await asyncio.sleep(settings.FREELANCE_WORK_TIMEOUT)

    async def _phase_assembling(self, task_id: str) -> Optional[str]:
        """Collect all submitted artifacts and assemble into final deliverable."""
        await self._set_status(task_id, FreelanceStatus.ASSEMBLING)

        async with db_session() as session:
            task = await session.get(FreelanceTask, task_id)
            arts_result = await session.execute(
                select(FreelanceArtifact).where(FreelanceArtifact.task_id == task_id)
            )
            artifacts: list[FreelanceArtifact] = list(arts_result.scalars().all())

        if not artifacts:
            logger.warning(f"[FL] {task_id[:8]} — no artifacts submitted, failing")
            await self._set_status(task_id, FreelanceStatus.FAILED)
            await self._broadcast(task_id, "freelance_failed", {"reason": "No artifacts submitted"})
            return None

        logger.info(f"[FL] {task_id[:8]} ASSEMBLING — {len(artifacts)} artifact(s)")

        # If only one artifact, use it directly (no assembly needed)
        if len(artifacts) == 1:
            assembled = artifacts[0].content
        else:
            # Use Claude to assemble into a cohesive document
            sections = "\n\n---\n\n".join(
                f"## Contribution from {a.agent_name} ({a.role})\n\n{a.content}"
                for a in artifacts
            )
            user_prompt = (
                f"Task: {task.title}\n\nDescription: {task.description}\n\n"
                f"Individual contributions to assemble:\n\n{sections}"
            )
            try:
                resp = await self._anthropic.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=4096,
                    system=ASSEMBLY_SYSTEM,
                    messages=[{"role": "user", "content": user_prompt}],
                )
                assembled = resp.content[0].text.strip()
            except Exception as e:
                logger.warning(f"[FL] Assembly LLM failed ({e}), concatenating raw artifacts")
                assembled = sections

        # Upload to IPFS if available
        ipfs_hash: Optional[str] = None
        if settings.ipfs_available:
            try:
                ipfs_hash = await upload_text(assembled, name=f"freelance-{task_id[:8]}.md")
                logger.info(f"[FL] IPFS upload: {ipfs_hash}")
            except Exception as e:
                logger.warning(f"[FL] IPFS upload failed: {e}")

        deliverable_hash = "0x" + hashlib.sha256(assembled.encode()).hexdigest()

        async with db_session() as session:
            await session.execute(
                update(FreelanceTask)
                .where(FreelanceTask.id == task_id)
                .values(
                    deliverable=assembled,
                    deliverable_ipfs_hash=ipfs_hash,
                    deliverable_hash=deliverable_hash,
                )
            )
            await session.commit()

        await self._broadcast(task_id, "freelance_assembled", {
            "artifacts": len(artifacts),
            "ipfs_hash": ipfs_hash,
            "len": len(assembled),
        })
        return assembled

    async def _phase_review(self, task_id: str, deliverable: str) -> bool:
        """LLM judge reviews the assembled deliverable."""
        await self._set_status(task_id, FreelanceStatus.REVIEW)

        async with db_session() as session:
            task = await session.get(FreelanceTask, task_id)

        user_prompt = (
            f"Original task: {task.title}\n"
            f"Task description: {task.description}\n"
            f"Required skills: {', '.join(task.skills_required or [])}\n\n"
            f"Assembled deliverable:\n\n{deliverable[:6000]}"  # truncate for token safety
        )

        score = 0.0
        notes = "Review unavailable"
        try:
            resp = await self._anthropic.messages.create(
                model="claude-haiku-4-5-20251001",
                max_tokens=512,
                system=REVIEW_SYSTEM,
                messages=[{"role": "user", "content": user_prompt}],
            )
            raw = resp.content[0].text.strip()
            # Strip markdown fences if present
            if raw.startswith("```"):
                raw = raw.split("```")[1].lstrip("json").strip()
            data = json.loads(raw)
            score = float(data.get("score", 0.0))
            verdict = data.get("verdict", "FAIL")
            summary = data.get("summary", "")
            weaknesses = data.get("weaknesses", [])
            notes = f"**{verdict}** — {summary}\n\nWeaknesses: " + "; ".join(weaknesses) if weaknesses else summary
        except Exception as e:
            logger.warning(f"[FL] Review LLM failed ({e}), defaulting score=0.5")
            score = 0.5
            notes = f"Automated review unavailable: {e}"

        async with db_session() as session:
            await session.execute(
                update(FreelanceTask)
                .where(FreelanceTask.id == task_id)
                .values(review_score=score, review_notes=notes)
            )
            await session.commit()

        passed = score >= REVIEW_PASS_THRESHOLD
        await self._broadcast(task_id, "freelance_reviewed", {
            "score": round(score, 3),
            "passed": passed,
            "notes": notes,
        })
        logger.info(f"[FL] {task_id[:8]} REVIEW — score={score:.3f} {'✓ PASS' if passed else '✗ FAIL'}")
        return passed

    async def _phase_settle(self, task_id: str, deliverable: str, passed: bool) -> None:
        """Settle the task — distribute bounty if passed, fail if not."""
        if not passed:
            await self._set_status(task_id, FreelanceStatus.FAILED)
            await self._broadcast(task_id, "freelance_failed", {
                "reason": f"Quality below threshold ({REVIEW_PASS_THRESHOLD})"
            })
            logger.info(f"[FL] {task_id[:8]} FAILED — quality too low")
            return

        # Optional: on-chain settlement
        tx_hash: Optional[str] = None
        if self.chain and settings.contracts_deployed:
            try:
                async with db_session() as session:
                    task = await session.get(FreelanceTask, task_id)
                    ipfs_cid = task.deliverable_ipfs_hash or ""
                    dlv_hash = task.deliverable_hash or "0x" + "0" * 64
                # FreelanceEscrow.settleTask would go here when contract is deployed
                logger.info(f"[FL] On-chain settle skipped — FreelanceEscrow not yet deployed")
            except Exception as e:
                logger.warning(f"[FL] On-chain settle failed: {e}")

        async with db_session() as session:
            await session.execute(
                update(FreelanceTask)
                .where(FreelanceTask.id == task_id)
                .values(status=FreelanceStatus.SETTLED, tx_hash=tx_hash)
            )
            await session.commit()

        await self._broadcast(task_id, "freelance_settled", {
            "task_id": task_id,
            "tx_hash": tx_hash,
        })
        logger.info(f"[FL] {task_id[:8]} SETTLED ✓")

    # ── Helpers ────────────────────────────────────────────────────────────────

    async def _set_status(self, task_id: str, status: FreelanceStatus) -> None:
        async with db_session() as session:
            await session.execute(
                update(FreelanceTask)
                .where(FreelanceTask.id == task_id)
                .values(status=status, updated_at=datetime.utcnow())
            )
            await session.commit()

    async def _broadcast(self, task_id: str, event: str, payload: dict) -> None:
        data = {"event": event, "task_id": task_id, **payload}
        # WebSocket broadcast
        await self.ws.broadcast({"type": event, "task_id": task_id, **payload})
        # Redis log for CLI/agent subscribers
        log_line = f"[freelance:{task_id[:8]}] {event} {json.dumps(payload)}"
        await self.redis.publish("orchestrator:logs", log_line)


# ── Singleton ──────────────────────────────────────────────────────────────────

_machine: Optional[FreelanceStateMachine] = None


def init_freelance_machine(
    redis_client: aioredis.Redis,
    ws_manager: WebSocketManager,
    chain_client=None,
) -> FreelanceStateMachine:
    global _machine
    _machine = FreelanceStateMachine(redis_client, ws_manager, chain_client)
    return _machine


def get_freelance_machine() -> FreelanceStateMachine:
    if _machine is None:
        raise RuntimeError("FreelanceStateMachine not initialised — call init_freelance_machine() first")
    return _machine
