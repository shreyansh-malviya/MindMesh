"""Query routes — POST/GET queries, submit responses, view memory."""
import asyncio
import hashlib
import uuid
from datetime import datetime, timedelta
from typing import Optional

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import settings
from ..database import get_db
from ..models import Agent, PeerReview, Query, QueryStatus, Response, SubQuery, SubQueryResponse, TaskMemory

router = APIRouter(prefix="/api/queries", tags=["queries"])


# ── Pydantic schemas ──────────────────────────────────────────────────────────

class CreateQueryRequest(BaseModel):
    problem: str = Field(..., min_length=10, max_length=5000)
    capabilities: list[str] = Field(..., min_length=1, max_items=5)
    bounty: str = Field(default="0", description="Bounty in wei as string")
    requester: str = Field(default="0x0000000000000000000000000000000000000000")
    deadline_minutes: int = Field(default=10, ge=1, le=60)
    chain_query_id: Optional[int] = None


class SubmitResponseRequest(BaseModel):
    agent_address: str
    response_text: str = Field(..., min_length=1, max_length=10000)
    reasoning: str = Field(default="")
    confidence: float = Field(default=0.7, ge=0.0, le=1.0)
    response_hash: Optional[str] = None


class QueryOut(BaseModel):
    id: str
    chain_query_id: Optional[int]
    status: str
    bounty: str
    requester: str
    deadline: datetime
    capabilities: list[str]
    problem: str
    round: int
    winner_address: Optional[str]
    tx_hash: Optional[str]
    memory_hash: Optional[str]
    created_at: datetime
    updated_at: datetime
    response_count: int = 0

    class Config:
        from_attributes = True


class ResponseOut(BaseModel):
    id: str
    agent_address: str
    response_text: str
    reasoning: str
    confidence: float
    response_hash: str
    score: Optional[float]
    score_reasoning: Optional[str]
    round: int
    submitted_at: datetime

    class Config:
        from_attributes = True


# ── Routes ────────────────────────────────────────────────────────────────────

@router.post("/", status_code=201)
async def create_query(
    body: CreateQueryRequest,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    """Create a new query and kick off orchestration."""
    from ..main import get_orchestrator, get_memory_service

    question_hash = "0x" + hashlib.sha256(body.problem.encode()).hexdigest()
    deadline = datetime.utcnow() + timedelta(minutes=body.deadline_minutes)

    query = Query(
        problem=body.problem,
        bounty=body.bounty,
        requester=body.requester,
        deadline=deadline,
        capabilities=body.capabilities,
        chain_query_id=body.chain_query_id,
        question_hash=question_hash,
        status=QueryStatus.CREATED,
        round=1,
    )
    db.add(query)
    await db.flush()
    await db.refresh(query)
    query_id = query.id
    await db.commit()  # commit so background tasks can see the query

    # Initialize shared memory
    mem_svc = get_memory_service()
    await mem_svc.initialize(
        query_id=query_id,
        problem=body.problem,
        bounty=body.bounty,
        capabilities=body.capabilities,
        requester=body.requester,
    )

    # Start orchestration in background
    orchestrator = get_orchestrator()
    background_tasks.add_task(orchestrator.process_query, query_id)

    return {
        "id": query_id,
        "status": "CREATED",
        "message": "Query created and orchestration started",
        "capabilities": body.capabilities,
        "bounty": body.bounty,
        "memory_url": f"/api/memory/{query_id}",
    }


@router.get("/")
async def list_queries(
    status: Optional[str] = None,
    capability: Optional[str] = None,
    limit: int = 20,
    offset: int = 0,
    db: AsyncSession = Depends(get_db),
):
    stmt = select(Query).order_by(Query.created_at.desc()).limit(limit).offset(offset)
    if status:
        try:
            stmt = stmt.where(Query.status == QueryStatus(status.upper()))
        except ValueError:
            pass

    result = await db.execute(stmt)
    queries = result.scalars().all()

    if capability:
        queries = [q for q in queries if capability in (q.capabilities or [])]

    output = []
    for q in queries:
        resp_result = await db.execute(
            select(Response).where(Response.query_id == q.id)
        )
        resp_count = len(resp_result.scalars().all())
        output.append(
            {
                "id": q.id,
                "chain_query_id": q.chain_query_id,
                "status": q.status.value,
                "bounty": q.bounty,
                "requester": q.requester,
                "deadline": q.deadline.isoformat(),
                "capabilities": q.capabilities,
                "problem": q.problem[:200],
                "round": q.round,
                "winner_address": q.winner_address,
                "tx_hash": q.tx_hash,
                "memory_hash": q.memory_hash,
                "created_at": q.created_at.isoformat(),
                "response_count": resp_count,
            }
        )
    return output


@router.get("/{query_id}")
async def get_query(query_id: str, db: AsyncSession = Depends(get_db)):
    q = await db.get(Query, query_id)
    if not q:
        raise HTTPException(status_code=404, detail="Query not found")

    resp_result = await db.execute(
        select(Response).where(Response.query_id == query_id).order_by(Response.submitted_at)
    )
    responses = resp_result.scalars().all()

    mem_result = await db.execute(
        select(TaskMemory).where(TaskMemory.task_id == query_id)
    )
    mem = mem_result.scalar_one_or_none()

    return {
        "id": q.id,
        "chain_query_id": q.chain_query_id,
        "status": q.status.value,
        "bounty": q.bounty,
        "requester": q.requester,
        "deadline": q.deadline.isoformat(),
        "capabilities": q.capabilities,
        "problem": q.problem,
        "round": q.round,
        "winner_address": q.winner_address,
        "tx_hash": q.tx_hash,
        "memory_hash": q.memory_hash,
        "created_at": q.created_at.isoformat(),
        "updated_at": q.updated_at.isoformat(),
        "explorer_url": f"{settings.EXPLORER_URL}/tx/{q.tx_hash}" if q.tx_hash else None,
        "responses": [
            {
                "id": r.id,
                "agent_address": r.agent_address,
                "response_text": r.response_text,
                "reasoning": r.reasoning,
                "confidence": r.confidence,
                "response_hash": r.response_hash,
                "score": r.score,
                "score_reasoning": r.score_reasoning,
                "round": r.round,
                "submitted_at": r.submitted_at.isoformat(),
            }
            for r in responses
        ],
        "memory": mem.content if mem else None,
    }


@router.post("/{query_id}/respond", status_code=201)
async def submit_response(
    query_id: str,
    body: SubmitResponseRequest,
    db: AsyncSession = Depends(get_db),
):
    """Agent submits a response to a query."""
    q = await db.get(Query, query_id)
    if not q:
        raise HTTPException(status_code=404, detail="Query not found")

    if q.status not in (QueryStatus.COLLECTING, QueryStatus.ROUTING, QueryStatus.CREATED):
        raise HTTPException(
            status_code=409,
            detail=f"Query is in status {q.status.value} — not accepting responses",
        )

    # Ensure agent exists in DB
    agent = await db.get(Agent, body.agent_address)
    if not agent:
        agent = Agent(
            address=body.agent_address,
            name=body.agent_address[:10] + "...",
            capabilities=["general"],
            tier="beta",
        )
        db.add(agent)
        await db.flush()

    response_hash = body.response_hash or (
        "0x" + hashlib.sha256(body.response_text.encode()).hexdigest()
    )

    response = Response(
        query_id=query_id,
        agent_address=body.agent_address,
        response_text=body.response_text,
        reasoning=body.reasoning,
        confidence=body.confidence,
        response_hash=response_hash,
        round=q.round,
    )
    db.add(response)
    await db.flush()
    await db.refresh(response)

    # Add to shared memory
    from ..main import get_memory_service
    mem_svc = get_memory_service()
    await mem_svc.add_event(
        query_id,
        "agent_response",
        {
            "agent_address": body.agent_address,
            "round": q.round,
            "reasoning": body.reasoning[:200],
            "confidence": body.confidence,
        },
    )

    return {
        "id": response.id,
        "status": "submitted",
        "response_hash": response_hash,
        "round": q.round,
    }


@router.post("/{query_id}/peer-review", status_code=201)
async def submit_peer_review(
    query_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """
    Agent submits peer scores for other agents' responses.

    Body: {
        "reviewer_address": "0x...",
        "reviews": [{"response_id": "...", "score": 0.85, "reasoning": "..."}]
    }
    """
    q = await db.get(Query, query_id)
    if not q:
        raise HTTPException(status_code=404, detail="Query not found")

    reviewer = body.get("reviewer_address", "")
    reviews = body.get("reviews", [])

    created = []
    for rev in reviews:
        response_id = rev.get("response_id")
        score = float(rev.get("score", 0.5))
        reasoning = rev.get("reasoning", "")

        # Don't let agents review their own response
        resp = await db.get(Response, response_id)
        if not resp or resp.agent_address == reviewer:
            continue

        pr = PeerReview(
            query_id=query_id,
            round=q.round,
            reviewer_address=reviewer,
            response_id=response_id,
            peer_score=max(0.0, min(1.0, score)),
            reasoning=reasoning,
        )
        db.add(pr)
        created.append(response_id)

    await db.flush()
    return {"status": "submitted", "reviews_saved": len(created)}


class SubQueryRequest(BaseModel):
    requester_address: str
    sub_problem: str = Field(..., min_length=5, max_length=2000)
    capabilities: list[str] = Field(default=["general"])


@router.post("/{query_id}/sub-query")
async def create_sub_query(
    query_id: str,
    body: SubQueryRequest,
    db: AsyncSession = Depends(get_db),
):
    """
    Agent requests help from other agents on a sub-problem.
    Broadcasts to other agents via Redis and waits up to 8s for responses.
    Returns the best answer received.
    """
    q = await db.get(Query, query_id)
    if not q:
        raise HTTPException(status_code=404, detail="Parent query not found")

    sq = SubQuery(
        parent_query_id=query_id,
        requester_address=body.requester_address,
        sub_problem=body.sub_problem,
        capabilities=body.capabilities,
        status="pending",
    )
    db.add(sq)
    await db.flush()
    await db.refresh(sq)
    sub_id = sq.id
    await db.commit()

    # Broadcast to agents via Redis
    import json
    import redis.asyncio as aioredis
    try:
        redis_client = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
        await redis_client.publish(
            "sub_queries:broadcast",
            json.dumps({
                "sub_id": sub_id,
                "parent_query_id": query_id,
                "requester_address": body.requester_address,
                "sub_problem": body.sub_problem,
                "capabilities": body.capabilities,
            }),
        )
        await redis_client.aclose()
    except Exception:
        pass

    # Wait up to 8s for responses
    deadline = asyncio.get_event_loop().time() + 8
    best_answer = ""
    best_agent = ""
    best_confidence = 0.0

    while asyncio.get_event_loop().time() < deadline:
        await asyncio.sleep(1.5)
        result = await db.execute(
            select(SubQueryResponse)
            .where(SubQueryResponse.sub_query_id == sub_id)
            .order_by(SubQueryResponse.confidence.desc())
        )
        sq_responses = result.scalars().all()

        if sq_responses:
            top = sq_responses[0]
            if top.confidence >= best_confidence:
                best_confidence = top.confidence
                best_answer = top.answer
                best_agent = top.agent_address
            if len(sq_responses) >= 2:
                break

    # Mark answered
    await db.execute(
        update(SubQuery)
        .where(SubQuery.id == sub_id)
        .values(
            result=best_answer,
            result_agent=best_agent or None,
            status="answered" if best_answer else "timeout",
            answered_at=datetime.utcnow(),
        )
    )

    return {
        "sub_id": sub_id,
        "answer": best_answer or "No sub-agents responded in time.",
        "answered_by": best_agent,
        "confidence": best_confidence,
    }


@router.post("/{query_id}/sub-query/{sub_id}/respond", status_code=201)
async def respond_to_sub_query(
    query_id: str,
    sub_id: str,
    body: dict,
    db: AsyncSession = Depends(get_db),
):
    """Agent submits an answer to another agent's sub-query."""
    sq = await db.get(SubQuery, sub_id)
    if not sq or sq.parent_query_id != query_id:
        raise HTTPException(status_code=404, detail="Sub-query not found")

    agent_address = body.get("agent_address", "")
    if agent_address == sq.requester_address:
        raise HTTPException(status_code=400, detail="Cannot answer your own sub-query")

    sqr = SubQueryResponse(
        sub_query_id=sub_id,
        agent_address=agent_address,
        answer=body.get("answer", ""),
        confidence=float(body.get("confidence", 0.5)),
    )
    db.add(sqr)
    await db.flush()
    return {"status": "submitted"}


@router.get("/{query_id}/memory")
async def get_memory(query_id: str, db: AsyncSession = Depends(get_db)):
    """Get full task memory for a query."""
    result = await db.execute(
        select(TaskMemory).where(TaskMemory.task_id == query_id)
    )
    mem = result.scalar_one_or_none()
    if not mem:
        raise HTTPException(status_code=404, detail="Memory not found")

    from ..main import get_memory_service
    ctx = await get_memory_service().get_context_for_agent(query_id)

    return {
        "task_id": query_id,
        "content": mem.content,
        "memory_hash": mem.memory_hash,
        "context_string": ctx,
        "updated_at": mem.updated_at.isoformat(),
    }
