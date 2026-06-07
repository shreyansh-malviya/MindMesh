"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import {
  api,
  type Query,
  type QueryResponse,
  shortId,
  shortAddr,
  fmtTime,
  timeAgo,
  scoreColor,
  CAPABILITIES,
} from "@/lib/api";
import { StatusBadge } from "@/components/StatusBadge";

const FILTERS = ["all", "routing", "collecting", "peer_review", "scoring", "escalating", "settled", "failed"];

/* ─── Score bar ───────────────────────────────────────────────────── */
function ScoreBar({ score }: { score: number | null }) {
  if (score === null)
    return <span style={{ color: "var(--text-3)", fontSize: 11 }}>—</span>;
  const pct = Math.round(score * 100);
  const color = scoreColor(score);
  return (
    <div className="score-bar-wrap">
      <span className="mono" style={{ fontSize: 11, color, minWidth: 28 }}>
        {score.toFixed(2)}
      </span>
      <div className="score-bar-track" style={{ width: 48 }}>
        <div className="score-bar-fill" style={{ width: `${pct}%`, background: color }} />
      </div>
    </div>
  );
}

/* ─── Response item (collapsible) ──────────────────────────────────── */
function ResponseItem({
  r,
  agentNames,
  isWinner,
}: {
  r: QueryResponse;
  agentNames: Map<string, string>;
  isWinner: boolean;
}) {
  const [open, setOpen] = useState(false);
  const name = agentNames.get(r.agent_address) ?? shortAddr(r.agent_address);

  return (
    <div
      className="response-item"
      style={isWinner ? { borderColor: "var(--green)" } : undefined}
    >
      <div
        className="response-header"
        onClick={() => setOpen((v) => !v)}
        role="button"
        aria-expanded={open}
        tabIndex={0}
        onKeyDown={(e) => e.key === "Enter" && setOpen((v) => !v)}
      >
        <span style={{ flex: 1, fontWeight: 500 }}>{name}</span>
        {isWinner && (
          <span
            style={{
              fontSize: 10,
              color: "var(--green)",
              fontFamily: "var(--mono)",
              fontWeight: 600,
              letterSpacing: "0.05em",
            }}
          >
            WINNER
          </span>
        )}
        <ScoreBar score={r.score} />
        <span style={{ fontSize: 10, color: "var(--text-3)", marginLeft: 4 }}>
          {open ? "▲" : "▼"}
        </span>
      </div>
      {open && (
        <div className="response-body">
          {r.reasoning && (
            <div style={{ marginBottom: 10 }}>
              <div
                style={{
                  fontSize: 10,
                  color: "var(--text-3)",
                  marginBottom: 4,
                  fontWeight: 500,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                Reasoning
              </div>
              <div style={{ color: "var(--text-2)" }}>{r.reasoning}</div>
            </div>
          )}
          <div
            style={{
              fontSize: 10,
              color: "var(--text-3)",
              marginBottom: 4,
              fontWeight: 500,
              textTransform: "uppercase",
              letterSpacing: "0.06em",
            }}
          >
            Response
          </div>
          <div style={{ color: "var(--text-1)" }}>{r.response_text}</div>
          {r.score_reasoning && (
            <div
              style={{
                marginTop: 10,
                paddingTop: 10,
                borderTop: "1px solid var(--border)",
              }}
            >
              <div
                style={{
                  fontSize: 10,
                  color: "var(--text-3)",
                  marginBottom: 4,
                  fontWeight: 500,
                  textTransform: "uppercase",
                  letterSpacing: "0.06em",
                }}
              >
                Judge notes
              </div>
              <div style={{ color: "var(--text-2)" }}>{r.score_reasoning}</div>
            </div>
          )}
        </div>
      )}
    </div>
  );
}

/* ─── Memory timeline ──────────────────────────────────────────────── */
function MemoryTimeline({ memory }: { memory: Record<string, unknown> | undefined }) {
  const events = (memory?.events as Array<Record<string, unknown>>) ?? [];
  if (events.length === 0) {
    return (
      <p style={{ fontSize: 12, color: "var(--text-3)" }}>No events recorded yet.</p>
    );
  }
  return (
    <div className="timeline" role="list">
      {events.map((ev, i) => {
        const type = (ev.type as string) ?? "event";
        const ts = ev.timestamp as string | undefined;
        const parts = [
          ev.agent_address ? `agent:${shortAddr(ev.agent_address as string)}` : null,
          ev.round !== undefined ? `rd${ev.round}` : null,
          ev.status ? `→ ${ev.status}` : null,
          ev.winner_address ? `winner:${shortAddr(ev.winner_address as string)}` : null,
          typeof ev.score === "number"
            ? `score:${(ev.score as number).toFixed(2)}`
            : null,
        ]
          .filter(Boolean)
          .join(" · ");
        return (
          <div key={i} className="timeline-item" role="listitem">
            <div className="timeline-dot" />
            <div className="timeline-content">
              <div className="timeline-type">{type}</div>
              {parts && <div className="timeline-meta">{parts}</div>}
              {ts && <div className="timeline-meta">{fmtTime(ts)}</div>}
            </div>
          </div>
        );
      })}
    </div>
  );
}

/* ─── Query detail ─────────────────────────────────────────────────── */
function QueryDetail({
  query,
  loading,
}: {
  query: Query | null;
  loading: boolean;
}) {
  const [activeRound, setActiveRound] = useState(1);
  const [logsOpen, setLogsOpen] = useState(false);
  const [logs, setLogs] = useState<string[]>([]);
  const logRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (query) setActiveRound(query.round);
  }, [query?.id]);

  useEffect(() => {
    let ws: WebSocket;
    try {
      ws = new WebSocket(
        (process.env.NEXT_PUBLIC_WS_URL as string) ?? "ws://localhost:8000/ws"
      );
      ws.onmessage = (e) => {
        const line = typeof e.data === "string" ? e.data : JSON.stringify(e.data);
        setLogs((prev) => [...prev.slice(-199), line]);
        setTimeout(() => {
          logRef.current?.scrollTo(0, logRef.current.scrollHeight);
        }, 10);
      };
    } catch {}
    return () => ws?.close();
  }, []);

  if (loading) {
    return (
      <div className="detail-scroll" style={{ padding: 16 }}>
        {[80, 92, 70].map((w, i) => (
          <div
            key={i}
            className="skeleton"
            style={{ height: 13, marginBottom: 12, width: `${w}%` }}
          />
        ))}
      </div>
    );
  }

  if (!query) {
    return (
      <div className="detail-placeholder">
        <div>
          <div style={{ fontSize: 20, marginBottom: 6, textAlign: "center" }}>←</div>
          <div>Select a query to view details</div>
        </div>
      </div>
    );
  }

  const responses = query.responses ?? [];
  const rounds = Array.from(new Set(responses.map((r) => r.round))).sort((a, b) => a - b);
  const roundResponses = responses.filter((r) => r.round === activeRound);

  const agentNames = new Map<string, string>();
  responses.forEach((r) => agentNames.set(r.agent_address, shortAddr(r.agent_address)));

  function logClass(line: string): string {
    const l = line.toLowerCase();
    if (l.includes("[alpha]")) return "log-line alpha";
    if (l.includes("[gamma]")) return "log-line gamma";
    if (l.includes("[beta]")) return "log-line beta";
    if (l.includes("[judge]")) return "log-line judge";
    if (l.includes("error")) return "log-line error";
    return "log-line orch";
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", overflow: "hidden" }}>
      {/* Header */}
      <div className="detail-header">
        <div className="detail-id">{query.id}</div>
        <div className="detail-meta">
          <StatusBadge status={query.status} />
          <span>Round {query.round}</span>
          <span style={{ color: "var(--text-3)" }}>·</span>
          <span>{responses.length} response{responses.length !== 1 ? "s" : ""}</span>
          <span style={{ color: "var(--text-3)" }}>·</span>
          <span>{timeAgo(query.created_at)}</span>
        </div>
      </div>

      {/* Scrollable body */}
      <div className="detail-scroll">
        <div className="detail-body">

          {/* Problem statement */}
          <div className="detail-section">
            <div className="section-label">Problem</div>
            <p style={{ fontSize: 13, lineHeight: 1.65 }}>{query.problem}</p>
          </div>

          {/* Winner */}
          {query.winner_address && (
            <div className="detail-section">
              <div className="section-label">Winner</div>
              <div className="mono" style={{ fontSize: 12, color: "var(--green)" }}>
                {query.winner_address}
              </div>
            </div>
          )}

          {/* Responses */}
          {responses.length > 0 && (
            <div className="detail-section">
              <div className="section-label">Responses</div>
              {rounds.length > 1 && (
                <div className="round-tabs" role="tablist">
                  {rounds.map((r) => (
                    <button
                      key={r}
                      role="tab"
                      aria-selected={activeRound === r}
                      className={`round-tab${activeRound === r ? " active" : ""}`}
                      onClick={() => setActiveRound(r)}
                    >
                      Round {r}
                    </button>
                  ))}
                </div>
              )}
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                {roundResponses.length === 0 ? (
                  <p style={{ fontSize: 12, color: "var(--text-3)" }}>
                    No responses for this round.
                  </p>
                ) : (
                  roundResponses.map((r) => (
                    <ResponseItem
                      key={r.id}
                      r={r}
                      agentNames={agentNames}
                      isWinner={r.agent_address === query.winner_address}
                    />
                  ))
                )}
              </div>
            </div>
          )}

          {/* Memory timeline */}
          <div className="detail-section">
            <div className="section-label">Memory timeline</div>
            <MemoryTimeline memory={query.memory} />
          </div>

          {/* Metadata */}
          <div className="detail-section">
            <div className="section-label">Metadata</div>
            <div className="meta-grid">
              <span className="meta-key">Bounty</span>
              <span className="meta-val">{query.bounty} MON</span>
              <span className="meta-key">Capabilities</span>
              <span className="meta-val">{query.capabilities.join(", ")}</span>
              <span className="meta-key">Deadline</span>
              <span className="meta-val">
                {new Date(query.deadline).toLocaleString([], {
                  dateStyle: "short",
                  timeStyle: "short",
                })}
              </span>
              <span className="meta-key">Created</span>
              <span className="meta-val">
                {new Date(query.created_at).toLocaleString([], {
                  dateStyle: "short",
                  timeStyle: "medium",
                })}
              </span>
              {query.memory_hash && (
                <>
                  <span className="meta-key">Memory hash</span>
                  <span className="meta-val">{query.memory_hash.slice(0, 20)}…</span>
                </>
              )}
              {query.tx_hash && (
                <>
                  <span className="meta-key">Tx hash</span>
                  <span className="meta-val">{query.tx_hash.slice(0, 20)}…</span>
                </>
              )}
            </div>
          </div>
        </div>
      </div>

      {/* Live log panel */}
      <div className="log-panel">
        <div
          className="log-header"
          onClick={() => setLogsOpen((v) => !v)}
          role="button"
          aria-expanded={logsOpen}
          tabIndex={0}
          onKeyDown={(e) => e.key === "Enter" && setLogsOpen((v) => !v)}
        >
          <span className="log-live-dot" />
          Live logs
          <span style={{ marginLeft: "auto" }}>{logsOpen ? "▲" : "▼"}</span>
        </div>
        {logsOpen && (
          <div
            className="log-body"
            ref={logRef}
            role="log"
            aria-live="polite"
          >
            {logs.length === 0 ? (
              <span className="log-line" style={{ color: "var(--text-3)" }}>
                Waiting for events…
              </span>
            ) : (
              logs.map((l, i) => (
                <div key={i} className={logClass(l)}>
                  {l}
                </div>
              ))
            )}
          </div>
        )}
      </div>
    </div>
  );
}

/* ─── New query modal ──────────────────────────────────────────────── */
function NewQueryModal({
  onClose,
  onCreated,
}: {
  onClose: () => void;
  onCreated: (id: string) => void;
}) {
  const [problem, setProblem] = useState("");
  const [caps, setCaps] = useState<string[]>(["general"]);
  const [bounty, setBounty] = useState("0");
  const [deadline, setDeadline] = useState(10);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const handler = (e: KeyboardEvent) => { if (e.key === "Escape") onClose(); };
    document.addEventListener("keydown", handler);
    return () => document.removeEventListener("keydown", handler);
  }, [onClose]);

  function toggleCap(c: string) {
    setCaps((prev) =>
      prev.includes(c) ? prev.filter((x) => x !== c) : [...prev, c]
    );
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    if (problem.trim().length < 10) {
      setError("Problem must be at least 10 characters.");
      return;
    }
    if (caps.length === 0) {
      setError("Select at least one capability.");
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const res = await api.createQuery({
        problem: problem.trim(),
        capabilities: caps,
        bounty,
        deadline_minutes: deadline,
      });
      onCreated(res.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Failed to submit query.");
    } finally {
      setLoading(false);
    }
  }

  const canSubmit = problem.trim().length >= 10 && caps.length > 0;

  return (
    <div
      className="modal-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="New query"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      <div className="modal">
        <div className="modal-header">
          <span>New query</span>
          <button
            className="btn"
            style={{ padding: "2px 8px", fontSize: 11 }}
            onClick={onClose}
            aria-label="Close"
          >
            ✕
          </button>
        </div>
        <form onSubmit={submit}>
          <div className="modal-body">
            <div className="field">
              <label className="field-label" htmlFor="problem">
                Problem statement
              </label>
              <textarea
                id="problem"
                className="input"
                placeholder="Describe the problem concisely. Be specific about what you need."
                value={problem}
                onChange={(e) => setProblem(e.target.value)}
                rows={5}
                required
              />
              <span
                style={{
                  fontSize: 11,
                  color: problem.length < 10 ? "var(--text-3)" : "var(--green)",
                  textAlign: "right",
                }}
              >
                {problem.length} / 10 min chars
              </span>
            </div>

            <div className="field">
              <span className="field-label">Capabilities required</span>
              <div className="cap-filters">
                {CAPABILITIES.map((c) => (
                  <button
                    key={c}
                    type="button"
                    className={`tag${caps.includes(c) ? " selected" : ""}`}
                    onClick={() => toggleCap(c)}
                    aria-pressed={caps.includes(c)}
                  >
                    {c}
                  </button>
                ))}
              </div>
            </div>

            <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 12 }}>
              <div className="field">
                <label className="field-label" htmlFor="bounty">
                  Bounty (MON)
                </label>
                <input
                  id="bounty"
                  type="number"
                  className="input"
                  value={bounty}
                  onChange={(e) => setBounty(e.target.value)}
                  min="0"
                  step="0.001"
                />
              </div>
              <div className="field">
                <label className="field-label" htmlFor="deadline">
                  Deadline
                </label>
                <select
                  id="deadline"
                  className="input"
                  value={deadline}
                  onChange={(e) => setDeadline(Number(e.target.value))}
                >
                  {[5, 10, 15, 30].map((m) => (
                    <option key={m} value={m}>
                      {m} minutes
                    </option>
                  ))}
                </select>
              </div>
            </div>

            {error && (
              <div
                role="alert"
                style={{
                  padding: "8px 12px",
                  background: "var(--red-dim)",
                  border: "1px solid var(--red)",
                  borderRadius: "var(--radius)",
                  fontSize: 12,
                  color: "var(--red)",
                }}
              >
                {error}
              </div>
            )}
          </div>

          <div className="modal-footer">
            <button type="button" className="btn" onClick={onClose} disabled={loading}>
              Cancel
            </button>
            <button
              type="submit"
              className="btn btn-primary"
              disabled={loading || !canSubmit}
            >
              {loading ? "Submitting…" : "Submit query"}
            </button>
          </div>
        </form>
      </div>
    </div>
  );
}

/* ─── Main page ────────────────────────────────────────────────────── */
export default function ExplorePage() {
  const [queries, setQueries] = useState<Query[]>([]);
  const [listLoading, setListLoading] = useState(true);
  const [listError, setListError] = useState<string | null>(null);
  const [filter, setFilter] = useState("all");
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [detail, setDetail] = useState<Query | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [showNew, setShowNew] = useState(false);

  const fetchQueries = useCallback(async () => {
    try {
      const data = await api.getQueries({
        status: filter === "all" ? undefined : filter,
        limit: 50,
      });
      setQueries(data);
      setListError(null);
    } catch {
      setListError("Could not reach orchestrator. Is it running?");
    } finally {
      setListLoading(false);
    }
  }, [filter]);

  useEffect(() => {
    setListLoading(true);
    fetchQueries();
  }, [fetchQueries]);

  useEffect(() => {
    const id = setInterval(fetchQueries, 5000);
    return () => clearInterval(id);
  }, [fetchQueries]);

  useEffect(() => {
    if (!selectedId) { setDetail(null); return; }
    setDetailLoading(true);
    api.getQuery(selectedId)
      .then(setDetail)
      .catch(() => setDetail(null))
      .finally(() => setDetailLoading(false));
  }, [selectedId]);

  useEffect(() => {
    if (!selectedId || !detail) return;
    if (["SETTLED", "FAILED"].includes(detail.status)) return;
    const id = setInterval(() => {
      api.getQuery(selectedId).then(setDetail).catch(() => {});
    }, 4000);
    return () => clearInterval(id);
  }, [selectedId, detail?.status]);

  function handleCreated(id: string) {
    setShowNew(false);
    fetchQueries();
    setSelectedId(id);
  }

  function statusDot(status: string): string {
    const s = status.toUpperCase();
    if (s === "SETTLED") return "var(--green)";
    if (s === "FAILED") return "var(--red)";
    if (s === "ESCALATING" || s === "SCORING") return "var(--amber)";
    if (s === "COLLECTING" || s === "ROUTING") return "var(--blue)";
    return "var(--text-3)";
  }

  return (
    <>
      <div className="explore-layout">
        {/* ── List panel ── */}
        <div className="explore-list-panel">
          <div className="qlist-toolbar">
            <div style={{ display: "flex", gap: 2, flex: 1, flexWrap: "wrap" }}>
              {FILTERS.map((f) => (
                <button
                  key={f}
                  className={`filter-tab${filter === f ? " active" : ""}`}
                  onClick={() => {
                    setFilter(f);
                    setListLoading(true);
                  }}
                  aria-pressed={filter === f}
                >
                  {f === "all" ? "All" : f.charAt(0).toUpperCase() + f.slice(1)}
                </button>
              ))}
            </div>
            <button
              className="btn btn-primary"
              onClick={() => setShowNew(true)}
              style={{ flexShrink: 0 }}
            >
              + New
            </button>
          </div>

          <div
            style={{
              padding: "5px 12px",
              fontSize: 11,
              color: "var(--text-3)",
              borderBottom: "1px solid var(--border)",
              background: "var(--bg-subtle)",
            }}
          >
            {listLoading
              ? "Loading…"
              : `${queries.length} quer${queries.length !== 1 ? "ies" : "y"}`}
          </div>

          <div className="qlist-body" role="list">
            {listLoading ? (
              Array.from({ length: 6 }).map((_, i) => (
                <div
                  key={i}
                  style={{ padding: "10px 12px", borderBottom: "1px solid var(--border)" }}
                >
                  <div className="skeleton" style={{ height: 10, width: "40%", marginBottom: 8 }} />
                  <div className="skeleton" style={{ height: 12, width: "90%", marginBottom: 4 }} />
                  <div className="skeleton" style={{ height: 12, width: "65%" }} />
                </div>
              ))
            ) : listError ? (
              <div className="empty-state">
                <h3>Cannot connect</h3>
                <p>{listError}</p>
                <button className="btn" style={{ marginTop: 12 }} onClick={fetchQueries}>
                  Retry
                </button>
              </div>
            ) : queries.length === 0 ? (
              <div className="empty-state">
                <h3>No queries</h3>
                <p>
                  {filter === "all"
                    ? "Submit a query to get started."
                    : `No queries with status "${filter}".`}
                </p>
              </div>
            ) : (
              queries.map((q) => (
                <div
                  key={q.id}
                  role="listitem"
                  className={`qlist-item${selectedId === q.id ? " selected" : ""}`}
                  onClick={() => setSelectedId((p) => (p === q.id ? null : q.id))}
                  onKeyDown={(e) =>
                    e.key === "Enter" &&
                    setSelectedId((p) => (p === q.id ? null : q.id))
                  }
                  tabIndex={0}
                  aria-selected={selectedId === q.id}
                >
                  <div className="qlist-item-bar">
                    <span
                      style={{
                        display: "inline-block",
                        width: 5,
                        height: 5,
                        borderRadius: "50%",
                        background: statusDot(q.status),
                        flexShrink: 0,
                      }}
                    />
                    <StatusBadge status={q.status} />
                    <span>{shortId(q.id)}</span>
                    <span>·</span>
                    <span>rd {q.round}</span>
                    <span>·</span>
                    <span>{q.response_count ?? 0} resp</span>
                    <span style={{ marginLeft: "auto" }}>{fmtTime(q.created_at)}</span>
                  </div>
                  <div className="qlist-item-problem">{q.problem}</div>
                  {q.winner_address && (
                    <div className="qlist-item-winner">
                      winner:{" "}
                      <span className="mono">{shortAddr(q.winner_address)}</span>
                    </div>
                  )}
                </div>
              ))
            )}
          </div>
        </div>

        {/* ── Detail panel ── */}
        <div className="explore-detail-panel">
          <QueryDetail query={detail} loading={detailLoading} />
        </div>
      </div>

      {showNew && (
        <NewQueryModal onClose={() => setShowNew(false)} onCreated={handleCreated} />
      )}
    </>
  );
}
