"""
All-in-one dev runner: orchestrator + 3 agents in one process.
Uses shared fakeredis so pub/sub works between orchestrator and agents.
No PostgreSQL, Redis, or Docker needed.

Usage:
    # Single machine (full stack)
    python scripts/dev_all.py

    # Network mode — first machine (orchestrator + agents)
    python scripts/dev_all.py --network --endpoint http://192.168.1.10:8000

    # Network mode — second machine (agents only, joins via bootstrap)
    python scripts/dev_all.py --agents-only --bootstrap http://192.168.1.10:8000

    # Custom port (run two instances locally for testing multi-node)
    python scripts/dev_all.py --port 8001 --endpoint http://localhost:8001
"""
import argparse
import asyncio
import os
import sys

# ── Load .env first so real contract addresses take precedence over defaults ──
try:
    from dotenv import load_dotenv
    _env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(_env):
        load_dotenv(_env, override=False)
except ImportError:
    pass

# ── Parse args first so env vars can reference them ─────────────────────────
parser = argparse.ArgumentParser(description="MindMesh dev runner")
parser.add_argument("--port", type=int, default=8000, help="Orchestrator HTTP port")
parser.add_argument("--network", action="store_true", help="Enable network/WiFi multi-node mode")
parser.add_argument("--agents-only", action="store_true", help="Start agents only (no orchestrator); use with --bootstrap")
parser.add_argument("--endpoint", default="", help="This node's public endpoint (e.g. http://192.168.1.10:8000)")
parser.add_argument("--bootstrap", default="", help="Bootstrap peer endpoint(s), comma-separated")
parser.add_argument("--no-beta", action="store_true", help="Skip Beta agent (requires OpenAI key)")
args = parser.parse_args()

# Render sets $PORT; respect it if present
PORT = int(os.environ.get("PORT", args.port))
AGENTS_ONLY = args.agents_only
NETWORK_MODE = args.network or bool(args.endpoint) or bool(args.bootstrap) or AGENTS_ONLY
NODE_MODE = "network" if NETWORK_MODE else "local"
NODE_ENDPOINT = args.endpoint or (f"http://localhost:{PORT}" if not AGENTS_ONLY else "")
BOOTSTRAP_NODES = args.bootstrap or os.environ.get("BOOTSTRAP_NODES", "")

# ── 0. Env defaults ───────────────────────────────────────────────────────────
# Always force SQLite for the query track — dev_all.py is a single-process dev/demo
# runner. Production postgres users should not use this script as the start command.
# Forcing (not setdefault) ensures any pre-existing DATABASE_URL env var (e.g. from
# a Render addon or CI) doesn't accidentally trigger asyncpg which may not be installed.
_current_db = os.environ.get("DATABASE_URL", "")
if not _current_db.startswith("sqlite"):
    os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///./dev_{PORT}.db"
os.environ["REDIS_URL"] = "redis://localhost:6379"
os.environ.setdefault("ORCHESTRATOR_HOST", "0.0.0.0")
os.environ["ORCHESTRATOR_PORT"] = str(PORT)
os.environ["ORCHESTRATOR_BASE_URL"] = f"http://localhost:{PORT}"
os.environ.setdefault("ESCALATION_THRESHOLD", "0.75")
os.environ.setdefault("MAX_ROUNDS", "3")
os.environ.setdefault("ROUND_TIMEOUT_SECONDS", "60")
os.environ.setdefault("AGENT_REGISTRY_ADDRESS", "0x0000000000000000000000000000000000000000")
os.environ.setdefault("QUERY_ESCROW_ADDRESS", "0x0000000000000000000000000000000000000000")
os.environ.setdefault("PROPOSAL_ESCROW_ADDRESS", "0x0000000000000000000000000000000000000000")
os.environ.setdefault("PROPOSAL_BIDDING_TIMEOUT", "25")
os.environ.setdefault("PROPOSAL_DISCUSSION_TIMEOUT", "60")
os.environ.setdefault("PROPOSAL_MAX_ROLES", "6")
os.environ.setdefault("PROPOSAL_DISCUSSION_ROUNDS", "3")
# Freelance track — shorter timeouts for local dev/demo
os.environ.setdefault("FREELANCE_BIDDING_TIMEOUT", "20")
os.environ.setdefault("FREELANCE_WORK_TIMEOUT", "60")
os.environ.setdefault("FREELANCE_MAX_TEAM_SIZE", "3")
os.environ.setdefault("FREELANCE_REVIEW_THRESHOLD", "0.65")
os.environ["NODE_MODE"] = NODE_MODE
os.environ["NODE_ENDPOINT"] = NODE_ENDPOINT
os.environ["BOOTSTRAP_NODES"] = BOOTSTRAP_NODES
os.environ["MDNS_ENABLED"] = "true" if NETWORK_MODE else "false"

# Well-known Hardhat test keys (public domain — safe for local dev)
os.environ.setdefault("DEPLOYER_PRIVATE_KEY",  "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80")
os.environ.setdefault("ALPHA_PRIVATE_KEY",  "0x59c6995e998f97a5a0044966f0945389dc9e86dae88c7a8412f4603b6b78690d")
os.environ.setdefault("BETA_PRIVATE_KEY",   "0x5de4111afa1a4b94908f83103eb1f1706367c2e68ca870fc3fb9a804cdab365a")
os.environ.setdefault("GAMMA_PRIVATE_KEY",  "0x7c852118294e51e653712a81e05800f419141751be58f605c371e15141b007a6")

# ── 1. Shared fakeredis (local mode) or real Redis (network mode) ─────────────
if not NETWORK_MODE:
    import fakeredis
    import fakeredis.aioredis
    import redis.asyncio as _aioredis

    FAKE_SERVER = fakeredis.FakeServer()

    def _fake_from_url(url, *args, **kwargs):
        kwargs.pop("encoding", None)
        return fakeredis.aioredis.FakeRedis(
            server=FAKE_SERVER,
            decode_responses=kwargs.get("decode_responses", False),
        )

    _aioredis.Redis.from_url = staticmethod(_fake_from_url)
    import redis.asyncio
    redis.asyncio.from_url = _fake_from_url
    print("[dev] Shared fakeredis server initialized")
else:
    print("[dev] Network mode — using system Redis or fakeredis as fallback")
    try:
        import redis.asyncio as _aioredis_check
        import fakeredis
        import fakeredis.aioredis
        # Check if real Redis is running; fall back to fakeredis if not
        import redis as _sync_redis
        _r = _sync_redis.Redis.from_url(os.environ["REDIS_URL"])
        _r.ping()
        _r.close()
        print(f"[dev] Connected to real Redis at {os.environ['REDIS_URL']}")
    except Exception:
        import fakeredis
        import fakeredis.aioredis
        import redis.asyncio as _aioredis
        _FAKE = fakeredis.FakeServer()
        def _fake_from_url2(url, *a, **kw):
            kw.pop("encoding", None)
            return fakeredis.aioredis.FakeRedis(server=_FAKE, decode_responses=kw.get("decode_responses", False))
        _aioredis.Redis.from_url = staticmethod(_fake_from_url2)
        import redis.asyncio
        redis.asyncio.from_url = _fake_from_url2
        print("[dev] Real Redis not available, using shared fakeredis")

print(f"[dev] DATABASE_URL = {os.environ.get('DATABASE_URL')} (query + freelance track; proposal track uses ChainEventStore)")

# ── 2. Paths ──────────────────────────────────────────────────────────────────
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "packages", "orchestrator", "src"))
sys.path.insert(0, os.path.join(ROOT, "packages", "agents", "src"))

# ── 3. Fix WebSocket relay bytes vs str ──────────────────────────────────────
import orchestrator.websocket_manager as _wsm

_orig_relay = _wsm.WebSocketManager.relay_redis_logs

async def _fixed_relay(self, redis_client):
    pubsub = redis_client.pubsub()
    await pubsub.subscribe("orchestrator:logs")
    async for message in pubsub.listen():
        if message["type"] == "message":
            data = message["data"]
            if isinstance(data, bytes):
                data = data.decode()
            await self.broadcast_log(data)

_wsm.WebSocketManager.relay_redis_logs = _fixed_relay

# ── 4. Import orchestrator ────────────────────────────────────────────────────
if not AGENTS_ONLY:
    from orchestrator.main import app as fastapi_app

# ── 5. Import agents ──────────────────────────────────────────────────────────
from agents.alpha import AlphaAgent
from agents.beta import BetaAgent
from agents.gamma import GammaAgent

# ── 6. Agent runner ───────────────────────────────────────────────────────────
async def run_agents():
    """Wait for orchestrator, then start all agents."""
    import aiohttp

    orchestrator_url = NODE_ENDPOINT if AGENTS_ONLY else f"http://localhost:{PORT}"
    if not orchestrator_url:
        orchestrator_url = f"http://localhost:{PORT}"

    print(f"[agents] Waiting for orchestrator at {orchestrator_url} ...")
    for _ in range(40):
        try:
            async with aiohttp.ClientSession() as s:
                async with s.get(f"{orchestrator_url}/health", timeout=aiohttp.ClientTimeout(total=2)) as r:
                    if r.status == 200:
                        print("[agents] Orchestrator ready!")
                        break
        except Exception:
            pass
        await asyncio.sleep(0.5)

    # Override agent orchestrator URL if connecting to remote
    if AGENTS_ONLY and NODE_ENDPOINT:
        os.environ["ORCHESTRATOR_BASE_URL"] = NODE_ENDPOINT

    alpha = AlphaAgent(private_key=os.environ["ALPHA_PRIVATE_KEY"])
    gamma = GammaAgent(private_key=os.environ["GAMMA_PRIVATE_KEY"])
    print(f"[agents] Alpha: {alpha.address}")
    print(f"[agents] Gamma: {gamma.address}")

    coroutines = [alpha.start(), gamma.start()]

    if not args.no_beta and os.environ.get("OPENAI_API_KEY"):
        beta = BetaAgent(private_key=os.environ["BETA_PRIVATE_KEY"])
        print(f"[agents] Beta:  {beta.address}")
        coroutines.append(beta.start())
    else:
        print("[agents] Beta skipped (no OPENAI_API_KEY or --no-beta)")

    print("[agents] All running, listening for queries and proposals...")
    await asyncio.gather(*coroutines, return_exceptions=True)


# ── 7. Main ───────────────────────────────────────────────────────────────────
async def main():
    import uvicorn

    mode_str = f"Network ({NODE_ENDPOINT})" if NETWORK_MODE else "Local (fakeredis)"
    bootstrap_str = f"\n  Bootstrap   -> {BOOTSTRAP_NODES}" if BOOTSTRAP_NODES else ""

    print()
    print("=" * 66)
    print("  MindMesh — Decentralized AI Agent Coordination")
    if AGENTS_ONLY:
        print(f"  Mode: Agents-only node | {mode_str}")
    else:
        print(f"  Mode: {mode_str}")
    print("=" * 66)
    if not AGENTS_ONLY:
        print(f"  API Docs    -> http://localhost:{PORT}/docs")
        print(f"  Health      -> http://localhost:{PORT}/health")
        print(f"  Web UI      -> http://localhost:3000  (run: cd packages/web && npx next dev)")
    print(f"  Endpoint    -> {NODE_ENDPOINT or '(no external endpoint)'}{bootstrap_str}")
    print("=" * 66)
    print()

    if AGENTS_ONLY:
        await run_agents()
    else:
        config = uvicorn.Config(
            fastapi_app,
            host="0.0.0.0",
            port=PORT,
            log_level="info",
            loop="asyncio",
        )
        server = uvicorn.Server(config)
        await asyncio.gather(
            server.serve(),
            run_agents(),
        )


if __name__ == "__main__":
    asyncio.run(main())
