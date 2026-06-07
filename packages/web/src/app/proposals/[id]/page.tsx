"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import {
  api,
  type Proposal,
  type DiscussionMessage,
  PROPOSAL_STATUS_LABEL,
  PROPOSAL_STATUS_DOT,
  shortAddr,
  shortId,
  fmtTime,
  timeAgo,
  explorerAddr,
  explorerTx,
} from "@/lib/api";
import { Markdown } from "@/lib/Markdown";
import { BotArena } from "@/components/BotArena";

const BASE =
  process.env.NEXT_PUBLIC_API_URL ||
  process.env.NEXT_PUBLIC_ORCHESTRATOR_URL ||
  "http://localhost:8000";

const PROPOSAL_ESCROW = process.env.NEXT_PUBLIC_PROPOSAL_ESCROW_ADDRESS || "0xDF6E43a9081c0E6D466aD8E82caF00881F6b7Bad";

const ROUND_LABEL: Record<string, string> = {
  initial: "Initial perspectives",
  response: "Responses",
  recommendation: "Final recommendations",
};

// ── Status badge ────────────────────────────────────────────────────────────
function StatusBadge({ status }: { status: string }) {
  const color = PROPOSAL_STATUS_DOT[status] ?? "#6b7280";
  return (
    <span
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 5,
        fontSize: "11px",
        fontWeight: 500,
        padding: "3px 8px",
        borderRadius: "var(--radius-sm)",
        background: `${color}18`,
        color,
        border: `1px solid ${color}40`,
      }}
    >
      <span
        style={{
          width: 6,
          height: 6,
          borderRadius: "50%",
          background: color,
          flexShrink: 0,
          animation: ["ROLE_DISCOVERY", "BIDDING", "DISCUSSING", "SYNTHESIZING"].includes(status)
            ? "pulse 1.4s infinite"
            : "none",
        }}
      />
      {PROPOSAL_STATUS_LABEL[status] ?? status}
    </span>
  );
}

// ── Pipeline progress bar ──────────────────────────────────────────────────
const PIPELINE_STEPS = [
  "CREATED",
  "ROLE_DISCOVERY",
  "BIDDING",
  "TEAM_FORMED",
  "DISCUSSING",
  "SYNTHESIZING",
  "SETTLED",
];

function Pipeline({ status }: { status: string }) {
  const idx = PIPELINE_STEPS.indexOf(status);
  const failed = status === "FAILED";
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 0, overflowX: "auto", paddingBottom: 4 }}>
      {PIPELINE_STEPS.map((step, i) => {
        const done = idx > i;
        const active = idx === i;
        const color = failed && i === idx
          ? "var(--red)"
          : done || active
          ? PROPOSAL_STATUS_DOT[step] ?? "var(--text-1)"
          : "var(--border)";
        return (
          <div key={step} style={{ display: "flex", alignItems: "center", flexShrink: 0 }}>
            <div
              style={{
                display: "flex",
                flexDirection: "column",
                alignItems: "center",
                gap: 4,
              }}
            >
              <div
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: done ? color : "transparent",
                  border: `2px solid ${color}`,
                  flexShrink: 0,
                }}
              />
              <span style={{ fontSize: "9px", color: active ? "var(--text-1)" : "var(--text-3)", whiteSpace: "nowrap", fontWeight: active ? 600 : 400 }}>
                {PROPOSAL_STATUS_LABEL[step] ?? step}
              </span>
            </div>
            {i < PIPELINE_STEPS.length - 1 && (
              <div
                style={{
                  height: 1,
                  width: 24,
                  background: done ? color : "var(--border)",
                  margin: "0 2px",
                  marginBottom: 14,
                  flexShrink: 0,
                }}
              />
            )}
          </div>
        );
      })}
    </div>
  );
}

// ── Team panel ──────────────────────────────────────────────────────────────
function TeamPanel({ proposal }: { proposal: Proposal }) {
  const { roles, bids } = proposal;
  if (roles.length === 0) return null;

  return (
    <section
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        overflow: "hidden",
        marginBottom: 16,
      }}
    >
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-subtle)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span style={{ fontSize: "12px", fontWeight: 600 }}>Expert Team</span>
        <span style={{ fontSize: "11px", color: "var(--text-3)" }}>
          {roles.filter((r) => r.agent_address).length}/{roles.length} assigned
        </span>
      </div>
      <div style={{ padding: "8px 0" }}>
        {roles.map((role) => {
          const roleBids = bids.filter((b) => b.role_name === role.role_name).sort((a, b) => b.fit_score - a.fit_score);
          return (
            <div
              key={role.id}
              style={{
                display: "flex",
                justifyContent: "space-between",
                alignItems: "flex-start",
                padding: "8px 14px",
                borderBottom: "1px solid var(--border)",
              }}
            >
              <div style={{ flex: 1 }}>
                <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 2 }}>
                  <span style={{ fontSize: "12px", fontWeight: 600 }}>{role.role_name}</span>
                  {role.agent_address ? (
                    <span
                      style={{
                        fontSize: "10px",
                        padding: "1px 6px",
                        borderRadius: "var(--radius-sm)",
                        background: "var(--bg-subtle)",
                        border: "1px solid var(--border)",
                        color: "var(--text-2)",
                      }}
                    >
                      {role.agent_name ?? shortAddr(role.agent_address)}
                    </span>
                  ) : (
                    <span style={{ fontSize: "10px", color: "var(--text-3)" }}>unassigned</span>
                  )}
                </div>
                <div style={{ fontSize: "11px", color: "var(--text-3)", lineHeight: 1.4 }}>
                  {role.role_description}
                </div>
                {roleBids.length > 0 && (
                  <div style={{ display: "flex", gap: 6, marginTop: 6, flexWrap: "wrap" }}>
                    {roleBids.slice(0, 4).map((b) => (
                      <span
                        key={b.id}
                        title={b.reasoning}
                        style={{
                          fontSize: "10px",
                          padding: "1px 6px",
                          borderRadius: "var(--radius-sm)",
                          border: `1px solid ${b.agent_address === role.agent_address ? "var(--text-1)" : "var(--border)"}`,
                          background: b.agent_address === role.agent_address ? "var(--text-1)" : "transparent",
                          color: b.agent_address === role.agent_address ? "var(--bg)" : "var(--text-3)",
                        }}
                      >
                        {b.agent_name} {(b.fit_score * 100).toFixed(0)}%
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ── Single message bubble ──────────────────────────────────────────────────
function MessageBubble({ msg }: { msg: DiscussionMessage }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, marginBottom: 14 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
        <span
          style={{
            display: "inline-flex",
            alignItems: "center",
            justifyContent: "center",
            width: 24,
            height: 24,
            borderRadius: "50%",
            background: "var(--text-1)",
            color: "var(--bg)",
            fontSize: "9px",
            fontWeight: 700,
            flexShrink: 0,
          }}
        >
          {msg.role_name.slice(0, 2).toUpperCase()}
        </span>
        <span style={{ fontSize: "12px", fontWeight: 600 }}>{msg.role_name}</span>
        <span style={{ fontSize: "11px", color: "var(--text-3)" }}>
          {msg.agent_name ?? shortAddr(msg.agent_address)}
        </span>
        <span style={{ fontSize: "10px", color: "var(--text-3)", marginLeft: "auto" }}>
          {fmtTime(msg.created_at)}
        </span>
      </div>
      <div
        style={{
          marginLeft: 32,
          padding: "10px 12px",
          background: "var(--bg-subtle)",
          borderRadius: "var(--radius-sm)",
          border: "1px solid var(--border)",
        }}
      >
        <Markdown content={msg.content} />
      </div>
    </div>
  );
}

// ── Discussion section ──────────────────────────────────────────────────────
function DiscussionSection({ messages, status }: { messages: DiscussionMessage[]; status: string }) {
  const rounds = [1, 2, 3];

  return (
    <section style={{ marginBottom: 16 }}>
      <div style={{ fontSize: "12px", fontWeight: 600, marginBottom: 12 }}>Discussion</div>
      {rounds.map((rn) => {
        const roundMsgs = messages.filter((m) => m.round_num === rn);
        const types = ["initial", "response", "recommendation"];
        const roundType = types[rn - 1] ?? "initial";
        const isActive = status === "DISCUSSING";
        const isComplete = roundMsgs.length > 0;
        const isFuture = !isComplete && !isActive;

        return (
          <div key={rn} style={{ marginBottom: 20 }}>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 10,
                marginBottom: 10,
              }}
            >
              <div
                style={{
                  width: 20,
                  height: 20,
                  borderRadius: "50%",
                  background: isComplete ? "var(--text-1)" : "transparent",
                  border: `1.5px solid ${isComplete ? "var(--text-1)" : "var(--border)"}`,
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  flexShrink: 0,
                  fontSize: "9px",
                  color: isComplete ? "var(--bg)" : "var(--text-3)",
                  fontWeight: 600,
                }}
              >
                {isComplete ? "✓" : rn}
              </div>
              <span
                style={{
                  fontSize: "12px",
                  fontWeight: 500,
                  color: isFuture ? "var(--text-3)" : "var(--text-1)",
                }}
              >
                Round {rn} — {ROUND_LABEL[roundType] ?? roundType}
              </span>
              {roundMsgs.length > 0 && (
                <span style={{ fontSize: "10px", color: "var(--text-3)" }}>
                  {roundMsgs.length} message{roundMsgs.length !== 1 ? "s" : ""}
                </span>
              )}
            </div>

            {roundMsgs.length > 0 ? (
              <div style={{ paddingLeft: 30 }}>
                {roundMsgs.map((msg) => (
                  <MessageBubble key={msg.id} msg={msg} />
                ))}
              </div>
            ) : (
              <div
                style={{
                  marginLeft: 30,
                  padding: "10px 12px",
                  border: "1px dashed var(--border)",
                  borderRadius: "var(--radius-sm)",
                  fontSize: "11px",
                  color: "var(--text-3)",
                }}
              >
                {isActive ? "Waiting for agent responses…" : "Pending"}
              </div>
            )}
          </div>
        );
      })}
    </section>
  );
}

// ── Final report ────────────────────────────────────────────────────────────
function FinalReport({ proposal }: { proposal: Proposal }) {
  const [report, setReport] = useState<string | null>(proposal.final_report ?? null);
  const [ipfsUrl, setIpfsUrl] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (proposal.status === "SETTLED" && !report) {
      setLoading(true);
      api.getProposalReport(proposal.id)
        .then((d) => {
          setReport(d.report);
          setIpfsUrl(d.ipfs_url);
        })
        .catch(() => {})
        .finally(() => setLoading(false));
    }
  }, [proposal.id, proposal.status, report]);

  if (proposal.status !== "SETTLED" && proposal.status !== "SYNTHESIZING") return null;

  return (
    <section
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        overflow: "hidden",
        marginBottom: 16,
      }}
    >
      <div
        style={{
          padding: "10px 14px",
          borderBottom: "1px solid var(--border)",
          background: "var(--bg-subtle)",
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
        }}
      >
        <span style={{ fontSize: "12px", fontWeight: 600 }}>
          {proposal.status === "SYNTHESIZING" ? "Synthesizing report…" : "Final Report"}
        </span>
        <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
          {proposal.report_ipfs_hash && (
            <span
              style={{
                fontSize: "10px",
                color: "var(--text-3)",
                fontFamily: "var(--font-mono)",
              }}
            >
              {proposal.report_ipfs_hash.slice(0, 20)}…
            </span>
          )}
          {ipfsUrl && (
            <a
              href={ipfsUrl}
              target="_blank"
              rel="noopener noreferrer"
              style={{
                fontSize: "11px",
                color: "var(--text-2)",
                textDecoration: "none",
                border: "1px solid var(--border)",
                padding: "2px 8px",
                borderRadius: "var(--radius-sm)",
              }}
            >
              IPFS ↗
            </a>
          )}
        </div>
      </div>
      <div style={{ padding: "16px" }}>
        {loading ? (
          <div className="skeleton" style={{ height: 200, borderRadius: "var(--radius-sm)" }} />
        ) : proposal.status === "SYNTHESIZING" ? (
          <div style={{ fontSize: "12px", color: "var(--text-3)", textAlign: "center", padding: "40px 0" }}>
            Claude Sonnet is synthesizing the discussion into a structured report…
          </div>
        ) : report ? (
          <Markdown content={report} />
        ) : (
          <div style={{ fontSize: "12px", color: "var(--text-3)" }}>Report not available.</div>
        )}
      </div>
      {proposal.tx_hash && (
        <div
          style={{
            padding: "8px 14px",
            borderTop: "1px solid var(--border)",
            background: "var(--bg-subtle)",
            fontSize: "11px",
            color: "var(--text-3)",
            display: "flex",
            alignItems: "center",
            gap: 8,
          }}
        >
          <span>On-chain:</span>
          <span className="mono">{proposal.tx_hash.slice(0, 20)}…</span>
        </div>
      )}
    </section>
  );
}

// ── Chain info panel ─────────────────────────────────────────────────────────
function ExplorerLink({ href, label, color = "var(--text-2)" }: { href: string | null; label: string; color?: string }) {
  if (!href) return <code style={{ fontSize: "10px", color, fontFamily: "monospace" }}>{label}</code>;
  return (
    <a href={href} target="_blank" rel="noopener noreferrer"
      style={{ fontSize: "10px", color, fontFamily: "monospace", textDecoration: "none", borderBottom: "1px dashed currentColor" }}
    >
      {label} ↗
    </a>
  );
}

function CopyBtn({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <button
      onClick={() => {
        navigator.clipboard.writeText(text).then(() => {
          setCopied(true);
          setTimeout(() => setCopied(false), 1500);
        });
      }}
      title="Copy"
      style={{
        background: "none",
        border: "none",
        cursor: "pointer",
        fontSize: "10px",
        color: copied ? "var(--green)" : "var(--text-3)",
        padding: "0 2px",
        lineHeight: 1,
      }}
    >
      {copied ? "✓" : "⎘"}
    </button>
  );
}

function ChainInfo({ proposal }: { proposal: Proposal }) {
  const isActive = !["SETTLED", "FAILED"].includes(proposal.status);
  const statusColor = proposal.status === "SETTLED"
    ? "#22c55e"
    : proposal.status === "FAILED"
    ? "#ef4444"
    : "#f59e0b";

  return (
    <div
      style={{
        border: "1px solid var(--border)",
        borderRadius: "var(--radius)",
        marginBottom: 16,
        overflow: "hidden",
      }}
    >
      <div
        style={{
          padding: "8px 14px",
          background: "var(--bg-subtle)",
          borderBottom: "1px solid var(--border)",
          display: "flex",
          alignItems: "center",
          gap: 8,
        }}
      >
        <span style={{ fontSize: "11px", fontWeight: 600 }}>On-Chain</span>
        <span style={{ fontSize: "10px", color: "var(--text-3)" }}>Monad Devnet · Chain 143</span>
        <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 5 }}>
          {isActive && (
            <span
              style={{
                width: 6,
                height: 6,
                borderRadius: "50%",
                background: statusColor,
                display: "inline-block",
                animation: "pulse 1.4s infinite",
              }}
            />
          )}
          <span style={{ fontSize: "10px", color: statusColor, fontWeight: 600 }}>
            {proposal.status.replace(/_/g, " ")}
          </span>
        </span>
      </div>

      <div style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
        {/* Proposal UUID */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: "10px", color: "var(--text-3)", width: 120, flexShrink: 0 }}>Proposal ID</span>
          <code style={{ fontSize: "10px", color: "var(--text-1)", fontFamily: "monospace" }}>
            {proposal.id}
          </code>
          <CopyBtn text={proposal.id} />
        </div>

        {/* On-chain escrow ID */}
        {proposal.chain_proposal_id != null && (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: "10px", color: "var(--text-3)", width: 120, flexShrink: 0 }}>Escrow ID</span>
            <code style={{ fontSize: "10px", color: "var(--text-1)", fontFamily: "monospace" }}>
              #{proposal.chain_proposal_id}
            </code>
            <CopyBtn text={String(proposal.chain_proposal_id)} />
          </div>
        )}

        {/* ProposalEscrow contract */}
        <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
          <span style={{ fontSize: "10px", color: "var(--text-3)", width: 120, flexShrink: 0 }}>Contract</span>
          <ExplorerLink href={explorerAddr(PROPOSAL_ESCROW)} label={`${PROPOSAL_ESCROW.slice(0, 10)}…${PROPOSAL_ESCROW.slice(-8)}`} color="var(--text-2)" />
          <CopyBtn text={PROPOSAL_ESCROW} />
        </div>

        {/* Settlement tx hash — hide if null or all-zero placeholder */}
        {proposal.tx_hash && !/^0x0+$/.test(proposal.tx_hash) && (
          <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
            <span style={{ fontSize: "10px", color: "var(--text-3)", width: 120, flexShrink: 0 }}>Settlement Tx</span>
            <ExplorerLink href={explorerTx(proposal.tx_hash)} label={`${proposal.tx_hash.slice(0, 10)}…${proposal.tx_hash.slice(-8)}`} color="var(--green)" />
            <CopyBtn text={proposal.tx_hash} />
          </div>
        )}
      </div>
    </div>
  );
}

// ── Main page ────────────────────────────────────────────────────────────────
export default function ProposalDetailPage({ params }: { params: { id: string } }) {
  const { id } = params;
  const [proposal, setProposal] = useState<Proposal | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  async function fetchProposal() {
    try {
      const data = await api.getProposal(id);
      setProposal(data);
      setError("");
    } catch {
      setError("Failed to load proposal.");
    } finally {
      setLoading(false);
    }
  }

  // Start websocket for live updates
  function startWs() {
    if (wsRef.current) wsRef.current.close();
    const url = BASE.replace(/^http/, "ws") + "/ws";
    const ws = new WebSocket(url);
    ws.onmessage = (evt) => {
      try {
        const msg = JSON.parse(evt.data);
        if (msg.type === "proposal_update" && msg.data?.id === id) {
          setProposal((prev) => (prev ? { ...prev, ...msg.data } : msg.data));
        }
      } catch {}
    };
    ws.onclose = () => {
      // Fall back to polling
    };
    wsRef.current = ws;
  }

  useEffect(() => {
    fetchProposal();
    startWs();
    // Always poll as fallback
    pollRef.current = setInterval(fetchProposal, 6000);
    return () => {
      wsRef.current?.close();
      if (pollRef.current) clearInterval(pollRef.current);
    };
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  if (loading) {
    return (
      <div style={{ padding: "24px 20px", maxWidth: 780, margin: "0 auto" }}>
        <div className="skeleton" style={{ height: 24, width: 200, borderRadius: "var(--radius-sm)", marginBottom: 20 }} />
        <div className="skeleton" style={{ height: 80, borderRadius: "var(--radius)", marginBottom: 12 }} />
        <div className="skeleton" style={{ height: 200, borderRadius: "var(--radius)" }} />
      </div>
    );
  }

  if (error || !proposal) {
    return (
      <div style={{ padding: "24px 20px", maxWidth: 780, margin: "0 auto" }}>
        <Link href="/proposals" style={{ fontSize: "12px", color: "var(--text-2)", textDecoration: "none" }}>
          ← Back
        </Link>
        <div style={{ marginTop: 40, textAlign: "center", color: "var(--text-3)" }}>
          {error || "Proposal not found."}
        </div>
      </div>
    );
  }

  const isTerminal = ["SETTLED", "FAILED"].includes(proposal.status);

  /* Left column — pipeline, chain info, discussion, report */
  const leftCol = (
    <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 0 }}>
      {/* Header */}
      <div style={{ marginBottom: 16 }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: 12, marginBottom: 8 }}>
          <h1 style={{ fontSize: "17px", fontWeight: 600, lineHeight: 1.4, flex: 1 }}>
            {proposal.title}
          </h1>
          <StatusBadge status={proposal.status} />
        </div>
        <div style={{ fontSize: "12px", color: "var(--text-2)", marginBottom: 10, lineHeight: 1.6 }}>
          {proposal.description}
        </div>
        <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: "11px", color: "var(--text-3)" }}>
          <span>{shortId(proposal.id)}</span>
          <span>Max {proposal.max_roles} roles</span>
          {proposal.bounty !== "0" && (
            <span>{(Number(proposal.bounty) / 1e18).toFixed(4)} MON bounty</span>
          )}
          {proposal.domain && <span>{proposal.domain}</span>}
          <span>{timeAgo(proposal.created_at)}</span>
          {!isTerminal && (
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "var(--green)", animation: "pulse 1.4s infinite", display: "inline-block" }} />
              live
            </span>
          )}
        </div>
      </div>

      {/* Pipeline */}
      <div style={{ padding: "12px 16px", background: "var(--bg-subtle)", borderRadius: "var(--radius)", marginBottom: 16, overflowX: "auto" }}>
        <Pipeline status={proposal.status} />
        {proposal.status === "FAILED" && (
          <div style={{ fontSize: "11px", color: "var(--red)", marginTop: 8 }}>
            Proposal failed — insufficient agent participation or pipeline error.
          </div>
        )}
      </div>

      {/* Chain info */}
      <ChainInfo proposal={proposal} />

      {/* Final report (settled) */}
      {proposal.status === "SETTLED" && <FinalReport proposal={proposal} />}

      {/* Team */}
      <TeamPanel proposal={proposal} />

      {/* Discussion */}
      {proposal.messages.length > 0 || ["DISCUSSING", "SYNTHESIZING", "SETTLED"].includes(proposal.status) ? (
        <DiscussionSection messages={proposal.messages} status={proposal.status} />
      ) : (
        !["CREATED", "FAILED"].includes(proposal.status) && (
          <div style={{ padding: "20px", border: "1px dashed var(--border)", borderRadius: "var(--radius)", textAlign: "center", fontSize: "12px", color: "var(--text-3)", marginBottom: 16 }}>
            Discussion will begin once the team is formed
          </div>
        )
      )}

      {/* Synthesizing */}
      {proposal.status === "SYNTHESIZING" && <FinalReport proposal={proposal} />}
    </div>
  );

  return (
    <div style={{ padding: "24px 20px", maxWidth: 1200, margin: "0 auto" }}>
      {/* Back */}
      <Link href="/proposals" style={{ fontSize: "12px", color: "var(--text-2)", textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 4, marginBottom: 20 }}>
        ← Proposals
      </Link>

      {/* Two-column layout */}
      <div style={{
        display: "grid",
        gridTemplateColumns: "1fr 340px",
        gap: 24,
        alignItems: "start",
      }}>
        {/* Left — detail */}
        {leftCol}

        {/* Right — bot arena (sticky) */}
        <div style={{ position: "sticky", top: 80 }}>
          <BotArena proposal={proposal} />
        </div>
      </div>
    </div>
  );
}
