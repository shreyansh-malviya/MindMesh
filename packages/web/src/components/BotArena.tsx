"use client";

import { BotAvatar, type BotState } from "./BotAvatar";
import type { Proposal, DiscussionMessage, ProposalRole } from "@/lib/api";

interface BotArenaProps {
  proposal: Proposal;
}

interface AgentInfo {
  name: string;
  role: string;
  address: string;
  state: BotState;
}

function deriveAgents(proposal: Proposal): AgentInfo[] {
  const { roles, bids, messages, status } = proposal;
  const lastMsg: DiscussionMessage | undefined = messages[messages.length - 1];
  const activeAddr = lastMsg?.agent_address;

  // If team is formed, show assigned agents
  if (roles.some((r: ProposalRole) => r.agent_address)) {
    return roles
      .filter((r: ProposalRole) => r.agent_address)
      .map((r: ProposalRole) => {
        const isActive = r.agent_address === activeAddr;
        let state: BotState = "idle";
        if (status === "SETTLED" || status === "FAILED") state = "done";
        else if (isActive && status === "DISCUSSING") state = "speaking";
        else if (status === "SYNTHESIZING") state = isActive ? "thinking" : "idle";
        return {
          name: r.agent_name ?? r.agent_address?.slice(0, 6) ?? "Agent",
          role: r.role_name,
          address: r.agent_address!,
          state,
        };
      });
  }

  // During bidding — show unique bidders
  if (bids.length > 0) {
    const seen = new Set<string>();
    const agents: AgentInfo[] = [];
    bids.forEach((b) => {
      if (!seen.has(b.agent_address)) {
        seen.add(b.agent_address);
        agents.push({
          name: b.agent_name ?? b.agent_address.slice(0, 6),
          role: b.role_name,
          address: b.agent_address,
          state: status === "BIDDING" ? "thinking" : "idle",
        });
      }
    });
    return agents.slice(0, 6);
  }

  // Early stages — show placeholder agents
  const placeholders = ["Alpha", "Beta", "Gamma"];
  return placeholders.map((n) => ({
    name: n,
    role: "Agent",
    address: "",
    state: status === "ROLE_DISCOVERY" ? "thinking" : "idle",
  }));
}

const STATUS_LABEL: Record<string, string> = {
  CREATED: "Initializing…",
  ROLE_DISCOVERY: "Discovering roles…",
  BIDDING: "Agents are bidding…",
  TEAM_FORMED: "Team assembled",
  DISCUSSING: "Multi-agent discussion…",
  SYNTHESIZING: "Synthesizing report…",
  SETTLED: "Completed",
  FAILED: "Failed",
};

export function BotArena({ proposal }: BotArenaProps) {
  const agents = deriveAgents(proposal);
  const isTerminal = ["SETTLED", "FAILED"].includes(proposal.status);
  const statusLabel = STATUS_LABEL[proposal.status] ?? proposal.status;

  return (
    <div style={{
      width: "100%",
      height: "100%",
      minHeight: 420,
      display: "flex",
      flexDirection: "column",
      background: "linear-gradient(160deg, #0d0d1a 0%, #111128 60%, #0d0d1a 100%)",
      border: "1px solid var(--border)",
      borderRadius: "var(--radius)",
      overflow: "hidden",
      position: "relative",
    }}>
      {/* Grid background */}
      <div style={{
        position: "absolute", inset: 0, pointerEvents: "none",
        backgroundImage: "linear-gradient(#ffffff06 1px, transparent 1px), linear-gradient(90deg, #ffffff06 1px, transparent 1px)",
        backgroundSize: "28px 28px",
      }} />

      {/* Header */}
      <div style={{
        padding: "10px 16px",
        borderBottom: "1px solid #ffffff11",
        display: "flex", alignItems: "center", gap: 10,
        background: "#ffffff05",
        position: "relative",
      }}>
        <div style={{
          width: 8, height: 8, borderRadius: "50%",
          background: isTerminal ? (proposal.status === "SETTLED" ? "var(--green)" : "var(--red)") : "#836EF9",
          animation: isTerminal ? "none" : "pulse 1.4s infinite",
          boxShadow: isTerminal ? "none" : "0 0 8px #836EF9",
        }} />
        <span style={{ fontSize: 11, fontWeight: 600, color: "#ffffff88", letterSpacing: "0.08em", textTransform: "uppercase", fontFamily: "var(--mono)" }}>
          Agent Arena
        </span>
        <span style={{ fontSize: 10, color: "#ffffff44", marginLeft: "auto", fontFamily: "var(--mono)" }}>
          {agents.length} agent{agents.length !== 1 ? "s" : ""}
        </span>
      </div>

      {/* Status banner */}
      <div style={{
        padding: "8px 16px",
        borderBottom: "1px solid #ffffff08",
        display: "flex", alignItems: "center", gap: 8,
      }}>
        <div style={{
          fontSize: 12, color: isTerminal
            ? (proposal.status === "SETTLED" ? "var(--green)" : "var(--red)")
            : "#836EF9",
          fontWeight: 600, fontFamily: "var(--mono)",
          animation: isTerminal ? "none" : "textGlow 2s ease-in-out infinite alternate",
        }}>
          {statusLabel}
        </div>
      </div>

      {/* Agent grid */}
      <div style={{
        flex: 1,
        display: "grid",
        gridTemplateColumns: agents.length <= 3 ? `repeat(${agents.length}, 1fr)` : "repeat(3, 1fr)",
        gap: "24px 16px",
        padding: "28px 20px",
        alignItems: "start",
        justifyItems: "center",
        position: "relative",
      }}>
        {agents.map((agent, i) => (
          <BotAvatar
            key={agent.address || agent.name}
            name={agent.name}
            role={agent.role}
            index={i}
            state={agent.state}
            address={agent.address}
          />
        ))}
      </div>

      {/* Floor reflection line */}
      <div style={{
        height: 1,
        background: "linear-gradient(90deg, transparent, #836EF944, transparent)",
        margin: "0 20px",
      }} />

      {/* Footer */}
      <div style={{
        padding: "8px 16px",
        display: "flex", gap: 16, alignItems: "center",
        position: "relative",
      }}>
        {[
          { label: "thinking", color: "#836EF9" },
          { label: "speaking", color: "#4ade80" },
          { label: "idle", color: "#ffffff44" },
        ].map(({ label, color }) => (
          <div key={label} style={{ display: "flex", alignItems: "center", gap: 4 }}>
            <div style={{ width: 5, height: 5, borderRadius: "50%", background: color }} />
            <span style={{ fontSize: 9, color: "#ffffff44", fontFamily: "var(--mono)", textTransform: "uppercase", letterSpacing: "0.06em" }}>
              {label}
            </span>
          </div>
        ))}

        {proposal.chain_proposal_id != null && (
          <span style={{ marginLeft: "auto", fontSize: 9, color: "#ffffff33", fontFamily: "var(--mono)" }}>
            escrow #{proposal.chain_proposal_id}
          </span>
        )}
      </div>
    </div>
  );
}
