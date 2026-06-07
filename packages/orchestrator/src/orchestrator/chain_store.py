"""
ChainEventStore — replaces SQLite for the proposal track.

All proposal state is derived from Monad blockchain events:
  ProposalCreated, RolesAnnounced, BidPosted, TeamFormed,
  MessagePosted, StatusUpdated, ProposalSettled

Block-wait pattern (Monad = ~1s blocks):
  After a write tx, wait WAIT_BLOCKS, then read READ_WINDOW events.
  This gives finality before dependent reads.

Offline mode: in-memory dict — no chain, no SQLite.
"""
import asyncio
import hashlib
import logging
import time
import uuid
from collections import defaultdict
from typing import Any, Optional

logger = logging.getLogger("orchestrator.chain_store")

WAIT_BLOCKS = 2    # blocks to wait after a write before reading
READ_WINDOW = 30   # blocks of history to scan when reading events


# ── Minimal ABI for event-emitting functions ─────────────────────────────────

PROPOSAL_ESCROW_EVENTS_ABI = [
    # State-changing
    {
        "inputs": [
            {"name": "descriptionHash", "type": "bytes32"},
            {"name": "maxRoles", "type": "uint256"},
            {"name": "deadlineSeconds", "type": "uint256"},
        ],
        "name": "createProposal",
        "outputs": [{"type": "uint256"}],
        "stateMutability": "payable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "names", "type": "string[]"},
            {"name": "descriptions", "type": "string[]"},
        ],
        "name": "announceRoles",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "roleName", "type": "string"},
            {"name": "agent", "type": "address"},
            {"name": "fitScore100", "type": "uint256"},
            {"name": "reasoning", "type": "string"},
        ],
        "name": "postBid",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "roundNum", "type": "uint256"},
            {"name": "agent", "type": "address"},
            {"name": "role", "type": "string"},
            {"name": "content", "type": "string"},
            {"name": "roundType", "type": "string"},
        ],
        "name": "postMessage",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "newStatus", "type": "string"},
        ],
        "name": "updateStatus",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "agents", "type": "address[]"},
            {"name": "roles", "type": "string[]"},
        ],
        "name": "formTeam",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "reportHash", "type": "bytes32"},
            {"name": "reportIpfsCid", "type": "string"},
            {"name": "contributors", "type": "address[]"},
            {"name": "shares", "type": "uint256[]"},
        ],
        "name": "settleProposal",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    {
        "inputs": [
            {"name": "proposalId", "type": "uint256"},
            {"name": "reason", "type": "string"},
        ],
        "name": "failProposal",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    },
    # Events
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": True, "name": "requester", "type": "address"},
            {"indexed": False, "name": "descriptionHash", "type": "bytes32"},
            {"indexed": False, "name": "bounty", "type": "uint256"},
            {"indexed": False, "name": "maxRoles", "type": "uint256"},
            {"indexed": False, "name": "deadline", "type": "uint256"},
        ],
        "name": "ProposalCreated",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": False, "name": "names", "type": "string[]"},
            {"indexed": False, "name": "descriptions", "type": "string[]"},
        ],
        "name": "RolesAnnounced",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": False, "name": "roleName", "type": "string"},
            {"indexed": True, "name": "agent", "type": "address"},
            {"indexed": False, "name": "fitScore100", "type": "uint256"},
            {"indexed": False, "name": "reasoning", "type": "string"},
        ],
        "name": "BidPosted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": True, "name": "agents", "type": "address[]"},
            {"indexed": False, "name": "roles", "type": "string[]"},
        ],
        "name": "TeamFormed",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": True, "name": "roundNum", "type": "uint256"},
            {"indexed": True, "name": "agent", "type": "address"},
            {"indexed": False, "name": "role", "type": "string"},
            {"indexed": False, "name": "content", "type": "string"},
            {"indexed": False, "name": "roundType", "type": "string"},
        ],
        "name": "MessagePosted",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": False, "name": "newStatus", "type": "string"},
        ],
        "name": "StatusUpdated",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": False, "name": "reportHash", "type": "bytes32"},
            {"indexed": False, "name": "reportIpfsCid", "type": "string"},
            {"indexed": False, "name": "contributors", "type": "address[]"},
            {"indexed": False, "name": "shares", "type": "uint256[]"},
        ],
        "name": "ProposalSettled",
        "type": "event",
    },
    {
        "anonymous": False,
        "inputs": [
            {"indexed": True, "name": "proposalId", "type": "uint256"},
            {"indexed": False, "name": "reason", "type": "string"},
        ],
        "name": "ProposalFailed",
        "type": "event",
    },
]


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S+00:00", time.gmtime())


class ChainEventStore:
    """
    On-chain event store for proposals.
    Online: writes txs to Monad, reads events via eth_getLogs.
    Offline: pure in-memory (no SQLite, no file IO).
    """

    def __init__(self, w3=None, account=None, contract_address: str = ""):
        self.w3 = w3
        self.account = account
        self.contract_address = contract_address
        self._online = bool(w3 and account and contract_address and contract_address != "0x" + "0" * 40)

        # In-memory state (used both as cache in online mode and primary in offline)
        self._proposals: dict[str, dict] = {}          # uuid → proposal dict
        self._chain_to_uuid: dict[int, str] = {}       # chain_id → uuid
        self._messages: dict[str, list] = defaultdict(list)   # uuid → [msg]
        self._bids: dict[str, list] = defaultdict(list)       # uuid → [bid]
        self._roles: dict[str, list] = defaultdict(list)      # uuid → [role]

        if self._online:
            logger.info("[ChainStore] Online mode — writing to Monad, reading events")
        else:
            logger.info("[ChainStore] Offline mode — in-memory only (no SQLite)")

    def _contract(self):
        return self.w3.eth.contract(
            address=self.w3.to_checksum_address(self.contract_address),
            abi=PROPOSAL_ESCROW_EVENTS_ABI,
        )

    # ── Block-wait helpers ────────────────────────────────────────────────────

    async def _current_block(self) -> int:
        return await self.w3.eth.block_number

    async def wait_blocks(self, n: int = WAIT_BLOCKS) -> int:
        """Wait N blocks. Returns block number after wait."""
        if not self._online:
            return 0
        start = await self._current_block()
        target = start + n
        while True:
            current = await self._current_block()
            if current >= target:
                return current
            await asyncio.sleep(0.4)  # Monad ~1s blocks, poll at 0.4s

    async def _send_tx(self, fn, value: int = 0) -> str:
        """Sign and send a tx. Returns tx hash."""
        nonce = await self.w3.eth.get_transaction_count(self.account.address)
        gas_price = await self.w3.eth.gas_price
        tx = await fn.build_transaction({
            "from": self.account.address,
            "nonce": nonce,
            "value": value,
            "gasPrice": int(gas_price * 1.1),
        })
        tx["gas"] = min(await self.w3.eth.estimate_gas(tx), 3_000_000)
        signed = self.account.sign_transaction(tx)
        tx_hash = await self.w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = await self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=30)
        return tx_hash.hex()

    # ── Write operations ──────────────────────────────────────────────────────

    async def create_proposal(
        self,
        title: str,
        description: str,
        max_roles: int,
        bounty_wei: int,
        lock_time: int,
        proposal_time: int,
        evaluation_time: int,
        requester: str = "0x0000000000000000000000000000000000000000",
        chain_proposal_id: Optional[int] = None,
    ) -> str:
        """Create a proposal. Returns UUID."""
        proposal_id = str(uuid.uuid4())
        desc_hash = "0x" + hashlib.sha256(description.encode()).hexdigest()
        deadline = lock_time + proposal_time + evaluation_time

        chain_id = chain_proposal_id
        tx_hash = None

        if self._online and chain_id is None:
            try:
                desc_bytes = bytes.fromhex(desc_hash[2:])
                fn = self._contract().functions.createProposal(desc_bytes, max_roles, deadline)
                tx_hash = await self._send_tx(fn, value=bounty_wei)
                # Wait and read the ProposalCreated event to get chain_id
                await self.wait_blocks(WAIT_BLOCKS)
                # Chain ID will be nextProposalId - 1 at the time of the call;
                # we parse it from events below
                chain_id = await self._read_latest_chain_id()
                logger.info(f"[ChainStore] createProposal on-chain: chain_id={chain_id} tx={tx_hash}")
            except Exception as e:
                logger.warning(f"[ChainStore] createProposal tx failed, continuing offline: {e}")

        proposal = {
            "id": proposal_id,
            "title": title,
            "description": description,
            "domain": "",
            "status": "CREATED",
            "bounty": str(bounty_wei),
            "requester": requester,
            "max_roles": max_roles,
            "lock_time": lock_time,
            "proposal_time": proposal_time,
            "evaluation_time": evaluation_time,
            "chain_proposal_id": chain_id,
            "roles_decided": [],
            "final_report": None,
            "report_ipfs_hash": None,
            "report_hash": None,
            "tx_hash": tx_hash,
            "created_at": _now_iso(),
            "updated_at": _now_iso(),
        }
        self._proposals[proposal_id] = proposal
        if chain_id is not None:
            self._chain_to_uuid[chain_id] = proposal_id
        return proposal_id

    async def _read_latest_chain_id(self) -> Optional[int]:
        """Read the most recent ProposalCreated event to get chain_id."""
        try:
            current = await self._current_block()
            contract = self._contract()
            logs = await contract.events.ProposalCreated.get_logs(
                fromBlock=max(0, current - READ_WINDOW), toBlock=current
            )
            if logs:
                return logs[-1]["args"]["proposalId"]
        except Exception as e:
            logger.warning(f"[ChainStore] _read_latest_chain_id error: {e}")
        return None

    async def set_status(self, proposal_id: str, status: str) -> None:
        """Update proposal status — emits StatusUpdated on-chain."""
        if proposal_id in self._proposals:
            self._proposals[proposal_id]["status"] = status
            self._proposals[proposal_id]["updated_at"] = _now_iso()

        chain_id = self._proposals.get(proposal_id, {}).get("chain_proposal_id")
        if self._online and chain_id is not None:
            try:
                fn = self._contract().functions.updateStatus(chain_id, status)
                await self._send_tx(fn)
                await self.wait_blocks(WAIT_BLOCKS)
            except Exception as e:
                logger.warning(f"[ChainStore] updateStatus tx failed: {e}")

    async def announce_roles(self, proposal_id: str, roles: list[dict]) -> None:
        """Save roles and emit RolesAnnounced on-chain."""
        role_records = [
            {
                "id": str(uuid.uuid4()),
                "role_name": r["name"],
                "role_description": r.get("description", ""),
                "agent_address": None,
                "agent_name": None,
                "assigned_at": None,
            }
            for r in roles
        ]
        self._roles[proposal_id] = role_records
        if proposal_id in self._proposals:
            self._proposals[proposal_id]["roles_decided"] = [r["name"] for r in roles]

        chain_id = self._proposals.get(proposal_id, {}).get("chain_proposal_id")
        if self._online and chain_id is not None:
            try:
                names = [r["name"] for r in roles]
                descs = [r.get("description", "") for r in roles]
                fn = self._contract().functions.announceRoles(chain_id, names, descs)
                await self._send_tx(fn)
                await self.wait_blocks(WAIT_BLOCKS)
                logger.info(f"[ChainStore] announceRoles on-chain: {names}")
            except Exception as e:
                logger.warning(f"[ChainStore] announceRoles tx failed: {e}")

    async def post_bid(
        self, proposal_id: str, role_name: str, agent_address: str,
        agent_name: str, fit_score: float, reasoning: str
    ) -> str:
        """Record a bid and emit BidPosted on-chain."""
        bid_id = str(uuid.uuid4())
        bid = {
            "id": bid_id,
            "proposal_id": proposal_id,
            "agent_address": agent_address,
            "agent_name": agent_name,
            "role_name": role_name,
            "fit_score": fit_score,
            "reasoning": reasoning,
            "created_at": _now_iso(),
        }
        self._bids[proposal_id].append(bid)

        chain_id = self._proposals.get(proposal_id, {}).get("chain_proposal_id")
        if self._online and chain_id is not None:
            try:
                score100 = int(fit_score * 100)
                fn = self._contract().functions.postBid(
                    chain_id, role_name,
                    self.w3.to_checksum_address(agent_address),
                    score100, reasoning[:500],
                )
                await self._send_tx(fn)
            except Exception as e:
                logger.warning(f"[ChainStore] postBid tx failed: {e}")
        return bid_id

    async def assign_role(self, proposal_id: str, role_name: str, agent_address: str, agent_name: str) -> None:
        """Mark a role as assigned to an agent."""
        for role in self._roles[proposal_id]:
            if role["role_name"] == role_name:
                role["agent_address"] = agent_address
                role["agent_name"] = agent_name
                role["assigned_at"] = _now_iso()
                break

    async def post_message(
        self, proposal_id: str, round_num: int, round_type: str,
        agent_address: str, agent_name: str, role_name: str, content: str
    ) -> str:
        """Store a discussion message and emit MessagePosted on-chain."""
        msg_id = str(uuid.uuid4())
        msg = {
            "id": msg_id,
            "proposal_id": proposal_id,
            "agent_address": agent_address,
            "agent_name": agent_name,
            "role_name": role_name,
            "round_num": round_num,
            "round_type": round_type,
            "content": content,
            "created_at": _now_iso(),
        }
        self._messages[proposal_id].append(msg)

        chain_id = self._proposals.get(proposal_id, {}).get("chain_proposal_id")
        if self._online and chain_id is not None:
            try:
                fn = self._contract().functions.postMessage(
                    chain_id, round_num,
                    self.w3.to_checksum_address(agent_address),
                    role_name, content[:2000], round_type,
                )
                tx = await self._send_tx(fn)
                logger.info(f"[ChainStore] MessagePosted on-chain: round={round_num} role={role_name} tx={tx[:12]}...")
                # Wait for block confirmation then continue (no need to read back)
                asyncio.ensure_future(self.wait_blocks(WAIT_BLOCKS))
            except Exception as e:
                logger.warning(f"[ChainStore] postMessage tx failed: {e}")
        return msg_id

    async def form_team_on_chain(self, proposal_id: str, team: dict[str, str]) -> None:
        """Call formTeam on ProposalEscrow with the selected team."""
        chain_id = self._proposals.get(proposal_id, {}).get("chain_proposal_id")
        if not self._online or chain_id is None:
            return
        try:
            agents = [self.w3.to_checksum_address(a) for a in team.values()]
            roles = list(team.keys())
            fn = self._contract().functions.formTeam(chain_id, agents, roles)
            await self._send_tx(fn)
            await self.wait_blocks(WAIT_BLOCKS)
            logger.info(f"[ChainStore] formTeam on-chain: {team}")
        except Exception as e:
            logger.warning(f"[ChainStore] formTeam tx failed: {e}")

    async def settle_on_chain(
        self, proposal_id: str, report_hash: str, ipfs_cid: str,
        agents: list[str], shares: list[int]
    ) -> str:
        """Settle proposal on-chain. Returns tx hash or zero-hash."""
        chain_id = self._proposals.get(proposal_id, {}).get("chain_proposal_id")
        if not self._online or chain_id is None:
            return "0x" + "0" * 64

        report_bytes = bytes.fromhex(report_hash[2:] if report_hash.startswith("0x") else report_hash)
        report_bytes32 = report_bytes[:32].ljust(32, b"\x00")
        agent_addrs = [self.w3.to_checksum_address(a) for a in agents]

        fn = self._contract().functions.settleProposal(
            chain_id, report_bytes32, ipfs_cid, agent_addrs, shares
        )
        tx = await self._send_tx(fn)
        await self.wait_blocks(WAIT_BLOCKS)

        if proposal_id in self._proposals:
            self._proposals[proposal_id]["tx_hash"] = tx
            self._proposals[proposal_id]["report_hash"] = report_hash
            self._proposals[proposal_id]["report_ipfs_hash"] = ipfs_cid
        return tx

    async def save_report(self, proposal_id: str, report: str, ipfs_cid: str, report_hash: str) -> None:
        """Save final report to in-memory state."""
        if proposal_id in self._proposals:
            self._proposals[proposal_id]["final_report"] = report
            self._proposals[proposal_id]["report_ipfs_hash"] = ipfs_cid
            self._proposals[proposal_id]["report_hash"] = report_hash

    # ── Read operations ───────────────────────────────────────────────────────

    async def get_proposal(self, proposal_id: str) -> Optional[dict]:
        """Get a proposal by UUID with its roles, bids, messages."""
        p = self._proposals.get(proposal_id)
        if not p:
            # Try to reload from chain events if online
            if self._online:
                await self._sync_from_chain(proposal_id)
                p = self._proposals.get(proposal_id)
        if not p:
            return None

        return {
            **p,
            "roles": self._roles.get(proposal_id, []),
            "bids": self._bids.get(proposal_id, []),
            "messages": sorted(
                self._messages.get(proposal_id, []),
                key=lambda m: (m["round_num"], m["created_at"])
            ),
        }

    async def list_proposals(self, limit: int = 20, offset: int = 0, status: Optional[str] = None) -> list[dict]:
        """List proposals newest-first, optionally filtered by status."""
        proposals = sorted(
            self._proposals.values(),
            key=lambda p: p["created_at"],
            reverse=True
        )
        if status:
            proposals = [p for p in proposals if p["status"] == status.upper()]

        result = []
        for p in proposals[offset: offset + limit]:
            pid = p["id"]
            result.append({
                **p,
                "roles": self._roles.get(pid, []),
                "bids": self._bids.get(pid, []),
                "messages": self._messages.get(pid, []),
            })
        return result

    async def get_messages(self, proposal_id: str) -> list[dict]:
        """Get all discussion messages for a proposal, sorted by round."""
        return sorted(
            self._messages.get(proposal_id, []),
            key=lambda m: (m["round_num"], m["created_at"])
        )

    async def get_bids(self, proposal_id: str, role_name: Optional[str] = None) -> list[dict]:
        """Get bids for a proposal, optionally filtered by role."""
        bids = self._bids.get(proposal_id, [])
        if role_name:
            bids = [b for b in bids if b["role_name"] == role_name]
        return sorted(bids, key=lambda b: b["fit_score"], reverse=True)

    async def _sync_from_chain(self, proposal_id: str) -> None:
        """Attempt to rebuild proposal state from chain events (best-effort)."""
        # Look up chain_id from any cached mapping
        for cid, uid in self._chain_to_uuid.items():
            if uid == proposal_id:
                await self._load_chain_id(cid)
                return

    async def _load_chain_id(self, chain_id: int) -> None:
        """Read all events for a chain_id and populate in-memory state."""
        if not self._online:
            return
        try:
            contract = self._contract()
            current = await self._current_block()
            from_block = max(0, current - 10000)

            # ProposalCreated
            created_logs = await contract.events.ProposalCreated.get_logs(
                fromBlock=from_block, toBlock=current,
                argument_filters={"proposalId": chain_id}
            )
            if not created_logs:
                return
            ev = created_logs[0]["args"]
            uid = self._chain_to_uuid.get(chain_id, str(uuid.uuid4()))
            self._chain_to_uuid[chain_id] = uid

            proposal = {
                "id": uid, "title": f"Proposal #{chain_id}",
                "description": "", "domain": "", "status": "CREATED",
                "bounty": str(ev["bounty"]), "requester": ev["requester"],
                "max_roles": ev["maxRoles"], "lock_time": 60, "proposal_time": 30,
                "evaluation_time": 300, "chain_proposal_id": chain_id,
                "roles_decided": [], "final_report": None,
                "report_ipfs_hash": None, "report_hash": None, "tx_hash": None,
                "created_at": _now_iso(), "updated_at": _now_iso(),
            }
            self._proposals[uid] = proposal

            # StatusUpdated — get latest status
            status_logs = await contract.events.StatusUpdated.get_logs(
                fromBlock=from_block, toBlock=current,
                argument_filters={"proposalId": chain_id}
            )
            if status_logs:
                proposal["status"] = status_logs[-1]["args"]["newStatus"]

            # RolesAnnounced
            role_logs = await contract.events.RolesAnnounced.get_logs(
                fromBlock=from_block, toBlock=current,
                argument_filters={"proposalId": chain_id}
            )
            if role_logs:
                ev_r = role_logs[-1]["args"]
                self._roles[uid] = [
                    {"id": str(uuid.uuid4()), "role_name": n, "role_description": d,
                     "agent_address": None, "agent_name": None, "assigned_at": None}
                    for n, d in zip(ev_r["names"], ev_r["descriptions"])
                ]

            # MessagePosted
            msg_logs = await contract.events.MessagePosted.get_logs(
                fromBlock=from_block, toBlock=current,
                argument_filters={"proposalId": chain_id}
            )
            for ml in msg_logs:
                a = ml["args"]
                self._messages[uid].append({
                    "id": str(uuid.uuid4()), "proposal_id": uid,
                    "agent_address": a["agent"], "agent_name": a["role"],
                    "role_name": a["role"], "round_num": a["roundNum"],
                    "round_type": a["roundType"], "content": a["content"],
                    "created_at": _now_iso(),
                })

            logger.info(f"[ChainStore] Synced chain_id={chain_id} from events")
        except Exception as e:
            logger.warning(f"[ChainStore] _load_chain_id error: {e}")


# ── Singleton ─────────────────────────────────────────────────────────────────

_store: Optional[ChainEventStore] = None


def init_chain_store(w3=None, account=None, contract_address: str = "") -> ChainEventStore:
    global _store
    _store = ChainEventStore(w3=w3, account=account, contract_address=contract_address)
    return _store


def get_store() -> ChainEventStore:
    global _store
    if _store is None:
        _store = ChainEventStore()
    return _store
