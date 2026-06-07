"""
SQLAlchemy 2.0 async models for MonadBlitz orchestrator.

Tables:
  agents            — registered AI agents
  queries           — user queries / tasks
  responses         — agent responses per round
  task_memories     — rolling memory per query (full history)
  orchestrator_events — audit log of all orchestrator actions
"""

from __future__ import annotations

import enum
import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Column,
    DateTime,
    Enum as SAEnum,
    Float,
    ForeignKey,
    Integer,
    JSON,
    String,
    Text,
)
from sqlalchemy.orm import DeclarativeBase, relationship


# ── Base ───────────────────────────────────────────────────────────────────────

class Base(DeclarativeBase):
    __allow_unmapped__ = True


# ── Enums ──────────────────────────────────────────────────────────────────────

class QueryStatus(str, enum.Enum):
    CREATED = "CREATED"
    ROUTING = "ROUTING"
    COLLECTING = "COLLECTING"
    PEER_REVIEW = "PEER_REVIEW"
    SCORING = "SCORING"
    ESCALATING = "ESCALATING"
    RESOLVING = "RESOLVING"
    SETTLED = "SETTLED"
    FAILED = "FAILED"


# ── Helpers ────────────────────────────────────────────────────────────────────

def _uuid() -> str:
    return str(uuid.uuid4())


def _now() -> datetime:
    return datetime.utcnow()


# ── Models ─────────────────────────────────────────────────────────────────────

class Agent(Base):
    """An AI agent registered in the marketplace."""

    __tablename__ = "agents"

    address = Column(String(42), primary_key=True, comment="Ethereum address (checksummed)")
    name = Column(String(100), nullable=False)
    capabilities = Column(JSON, nullable=False, default=list, comment="List of capability tags")
    tier = Column(String(20), default="beta", comment="alpha | beta | gamma")
    reputation = Column(Integer, default=5000, nullable=False)
    stake = Column(String(32), default="0", comment="Staked amount in wei as string")
    wins = Column(Integer, default=0, nullable=False)
    losses = Column(Integer, default=0, nullable=False)
    timeouts = Column(Integer, default=0, nullable=False)
    active = Column(Boolean, default=True, nullable=False)
    metadata_uri = Column(String(256), default="")
    registered_at = Column(DateTime, default=_now, nullable=False)

    responses: list["Response"] = relationship(
        "Response",
        back_populates="agent",
        foreign_keys="Response.agent_address",
    )

    @property
    def win_rate(self) -> float:
        total = self.wins + self.losses
        return round(self.wins / total, 3) if total > 0 else 0.0

    @property
    def total_responses(self) -> int:
        return self.wins + self.losses + self.timeouts

    def __repr__(self) -> str:
        return f"<Agent {self.name} {self.address[:8]}... rep={self.reputation}>"


class Query(Base):
    """A user query submitted to the marketplace."""

    __tablename__ = "queries"

    id = Column(String(36), primary_key=True, default=_uuid)
    chain_query_id = Column(BigInteger, nullable=True, comment="On-chain query ID from QueryEscrow")
    status = Column(
        SAEnum(QueryStatus, name="querystatus"),
        default=QueryStatus.CREATED,
        nullable=False,
    )
    bounty = Column(String(32), nullable=False, comment="Bounty amount in wei as string")
    requester = Column(String(42), nullable=False, comment="Requester Ethereum address")
    deadline = Column(DateTime, nullable=False)
    capabilities = Column(JSON, nullable=False, default=list, comment="Required capability tags")
    problem = Column(Text, nullable=False, comment="Full problem statement")
    question_hash = Column(String(66), nullable=True, comment="keccak256 of problem text")
    round = Column(Integer, default=1, nullable=False, comment="Current escalation round")
    winner_address = Column(String(42), nullable=True)
    tx_hash = Column(String(66), nullable=True, comment="Settlement transaction hash")
    memory_hash = Column(String(66), nullable=True, comment="keccak256 of task memory at resolution")
    created_at = Column(DateTime, default=_now, nullable=False)
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    responses: list["Response"] = relationship("Response", back_populates="query")
    memory: "TaskMemory" = relationship("TaskMemory", back_populates="query", uselist=False)

    def __repr__(self) -> str:
        return f"<Query {self.id[:8]}... status={self.status} round={self.round}>"


class Response(Base):
    """An agent's response to a query in a specific round."""

    __tablename__ = "responses"

    id = Column(String(36), primary_key=True, default=_uuid)
    query_id = Column(String(36), ForeignKey("queries.id"), nullable=False)
    agent_address = Column(String(42), ForeignKey("agents.address"), nullable=False)
    response_text = Column(Text, nullable=False)
    reasoning = Column(Text, default="")
    confidence = Column(Float, default=0.5)
    response_hash = Column(String(66), nullable=False, comment="keccak256 of response_text")
    score = Column(Float, nullable=True, comment="Judge score 0.0–1.0")
    score_reasoning = Column(Text, nullable=True, comment="Judge explanation")
    round = Column(Integer, default=1, nullable=False)
    submitted_at = Column(DateTime, default=_now, nullable=False)

    query: "Query" = relationship("Query", back_populates="responses")
    agent: "Agent" = relationship(
        "Agent",
        back_populates="responses",
        foreign_keys=[agent_address],
    )

    def __repr__(self) -> str:
        return f"<Response {self.id[:8]}... agent={self.agent_address[:8]}... score={self.score}>"


class TaskMemory(Base):
    """Rolling JSON memory for a query — stores full event history across all rounds."""

    __tablename__ = "task_memories"

    task_id = Column(String(36), ForeignKey("queries.id"), primary_key=True)
    content = Column(
        JSON,
        nullable=False,
        default=dict,
        comment="Full structured history: {problem, bounty, capabilities, events:[...]}",
    )
    memory_hash = Column(String(66), nullable=True, comment="keccak256 of current content")
    updated_at = Column(DateTime, default=_now, onupdate=_now, nullable=False)

    query: "Query" = relationship("Query", back_populates="memory")

    def __repr__(self) -> str:
        return f"<TaskMemory task_id={self.task_id} hash={self.memory_hash}>"


class OrchestratorEvent(Base):
    """Append-only audit log of all orchestrator-level events."""

    __tablename__ = "orchestrator_events"

    id = Column(String(36), primary_key=True, default=_uuid)
    event_type = Column(String(64), nullable=False, comment="routing|score|escalation|resolution|chain_event")
    query_id = Column(String(36), nullable=True)
    payload = Column(JSON, nullable=False, default=dict)
    tx_hash = Column(String(66), nullable=True)
    block_number = Column(BigInteger, nullable=True)
    created_at = Column(DateTime, default=_now, nullable=False)

    def __repr__(self) -> str:
        return f"<OrchestratorEvent {self.event_type} query={self.query_id}>"


class PeerReview(Base):
    """Peer score given by one agent to another agent's response."""

    __tablename__ = "peer_reviews"

    id = Column(String(36), primary_key=True, default=_uuid)
    query_id = Column(String(36), ForeignKey("queries.id"), nullable=False)
    round = Column(Integer, default=1, nullable=False)
    reviewer_address = Column(String(42), ForeignKey("agents.address"), nullable=False)
    response_id = Column(String(36), ForeignKey("responses.id"), nullable=False)
    peer_score = Column(Float, nullable=False, comment="Peer's score 0.0–1.0")
    reasoning = Column(Text, default="")
    created_at = Column(DateTime, default=_now, nullable=False)

    def __repr__(self) -> str:
        return (
            f"<PeerReview reviewer={self.reviewer_address[:8]}... "
            f"response={self.response_id[:8]}... score={self.peer_score}>"
        )


class SubQuery(Base):
    """An agent-to-agent sub-query: one agent requests help from others."""

    __tablename__ = "sub_queries"

    id = Column(String(36), primary_key=True, default=_uuid)
    parent_query_id = Column(String(36), ForeignKey("queries.id"), nullable=False)
    requester_address = Column(String(42), nullable=False, comment="Agent that needs help")
    sub_problem = Column(Text, nullable=False)
    capabilities = Column(JSON, nullable=False, default=list)
    # Best answer collected from other agents
    result = Column(Text, nullable=True)
    result_agent = Column(String(42), nullable=True, comment="Which agent answered best")
    status = Column(String(20), default="pending", comment="pending|answered|timeout")
    created_at = Column(DateTime, default=_now, nullable=False)
    answered_at = Column(DateTime, nullable=True)

    def __repr__(self) -> str:
        return f"<SubQuery {self.id[:8]}... parent={self.parent_query_id[:8]}... status={self.status}>"


class SubQueryResponse(Base):
    """A response to a sub-query from a helper agent."""

    __tablename__ = "sub_query_responses"

    id = Column(String(36), primary_key=True, default=_uuid)
    sub_query_id = Column(String(36), ForeignKey("sub_queries.id"), nullable=False)
    agent_address = Column(String(42), nullable=False)
    answer = Column(Text, nullable=False)
    confidence = Column(Float, default=0.5)
    created_at = Column(DateTime, default=_now, nullable=False)

    def __repr__(self) -> str:
        return f"<SubQueryResponse agent={self.agent_address[:8]}... sq={self.sub_query_id[:8]}...>"
