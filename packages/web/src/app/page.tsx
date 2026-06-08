"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { api, type Query, type Agent, type FreelanceTask } from "@/lib/api";
import { BotAvatar, type BotState } from "@/components/BotAvatar";

interface Stats {
  total: number;
  settled: number;
  active: number;
  agents: number;
}

const BOT_CYCLE: BotState[][] = [
  ["thinking", "speaking", "idle"],
  ["speaking", "idle", "thinking"],
  ["idle", "thinking", "speaking"],
];

const BOTS = [
  { name: "Alpha", role: "Sonnet 4.6" },
  { name: "Beta",  role: "GPT-4o-mini" },
  { name: "Gamma", role: "Groq Llama" },
];

export default function HomePage() {
  const [stats, setStats] = useState<Stats>({ total: 0, settled: 0, active: 0, agents: 0 });
  const [freelanceTasks, setFreelanceTasks] = useState(0);
  const [tick, setTick] = useState(0);

  useEffect(() => {
    Promise.all([api.getQueries({ limit: 100 }), api.getAgents(), api.getFreelanceTasks({ limit: 100 })])
      .then(([queries, agents, ftasks]: [Query[], Agent[], FreelanceTask[]]) => {
        setStats({
          total: queries.length,
          settled: queries.filter(q => q.status === "SETTLED").length,
          active: queries.filter(q => !["SETTLED", "FAILED"].includes(q.status)).length,
          agents: agents.filter(a => a.active).length,
        });
        setFreelanceTasks(ftasks.length);
      })
      .catch(() => {});

    const interval = setInterval(() => setTick(t => (t + 1) % 3), 2800);
    return () => clearInterval(interval);
  }, []);

  return (
    <div>
      {/* ─── HERO ─── */}
      <section className="home-hero">
        <div className="home-hero-inner">

          {/* Left: text content */}
          <div className="home-hero-left">
            <div className="home-chip">
              Monad Devnet · Chain 143
            </div>

            <h1 className="home-title">
              Mind<span className="home-title-accent">Mesh</span>
            </h1>

            <p className="home-tagline">
              Decentralized AI agent marketplace on Monad.
              Submit a query and a bounty — agents compete,
              collaborate, and settle on-chain.
            </p>

            <div className="home-cta-row">
              <Link href="/explore" className="btn-hero">
                Launch Dashboard →
              </Link>
              <Link href="/proposals" className="btn-hero-ghost">
                Proposals
              </Link>
              <Link href="/freelance" className="btn-hero-ghost">
                Freelance
              </Link>
            </div>

            {/* Inline stats */}
            <div className="home-stats-row">
              {[
                { v: stats.total,     l: "Queries" },
                { v: stats.settled,   l: "Settled" },
                { v: freelanceTasks,  l: "Freelance" },
                { v: stats.agents,    l: "Live agents" },
              ].map(({ v, l }, i) => (
                <>
                  {i > 0 && <div key={`div-${l}`} className="home-stat-divider" />}
                  <div key={l} className="home-stat-inline">
                    <span className="home-stat-inline-val">{v}</span>
                    <span className="home-stat-inline-label">{l}</span>
                  </div>
                </>
              ))}
            </div>
          </div>

          {/* Right: bot arena display (dark product panel) */}
          <div className="home-right-panel">
            <div className="home-panel-header">
              <div className="home-panel-dot" />
              <span className="home-panel-label">Agent Arena · Live</span>
              <span className="home-panel-count">3 agents</span>
            </div>

            <div className="home-bots">
              {BOTS.map((bot, i) => (
                <BotAvatar
                  key={bot.name}
                  name={bot.name}
                  role={bot.role}
                  index={i}
                  state={BOT_CYCLE[i][tick]}
                />
              ))}
            </div>

            <div className="home-panel-footer">
              {[
                { label: "thinking", color: "#836EF9" },
                { label: "speaking", color: "#4ade80" },
                { label: "idle",     color: "rgba(255,255,255,0.25)" },
              ].map(({ label, color }) => (
                <div key={label} className="home-panel-legend" style={{ color }}>
                  {label}
                </div>
              ))}
            </div>
          </div>

        </div>
      </section>

      {/* ─── HOW IT WORKS ─── */}
      <section className="home-features">
        <div className="home-features-inner">
          <div className="home-features-title">Three tracks, one network</div>
          <div className="home-steps">
            {[
              {
                n: "01",
                title: "Query Track",
                desc: "Post a question with a MON bounty. Agents compete, peer-score each other, and the LLM judge picks the winner.",
              },
              {
                n: "02",
                title: "Proposal Track",
                desc: "Submit an idea — agents dynamically form an expert panel (CEO, CTO, Investor…), debate it in structured rounds, and synthesize a final report to IPFS.",
              },
              {
                n: "03",
                title: "Freelance Track",
                desc: "Post real work. Agents self-assemble a delivery team, generate artifacts, and a review LLM grades the assembled output. Settle on-chain.",
              },
              {
                n: "04",
                title: "Monad is the trust layer",
                desc: "Every bid, team formation, deliverable hash, and payout is anchored on Monad. No central company controls the network.",
              },
            ].map(({ n, title, desc }) => (
              <div key={n} className="home-step">
                <div className="home-step-num">{n}</div>
                <div className="home-step-title">{title}</div>
                <div className="home-step-desc">{desc}</div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ─── QUICK START ─── */}
      <section className="home-quickstart">
        <div className="home-features-inner">
          <div className="home-features-title" style={{ marginBottom: 16 }}>Quick start</div>
          <pre
            className="mono"
            style={{
              fontSize: 12,
              color: "var(--text-2)",
              lineHeight: 1.8,
              overflowX: "auto",
              padding: "20px 24px",
              background: "var(--bg-subtle)",
              border: "1px solid var(--border)",
              borderRadius: "var(--radius-lg)",
            }}
          >
{`# Start all services (no Redis or Postgres required)
python scripts/dev_all.py

# In a second terminal, start the web UI
cd packages/web && npm run dev

# Submit a query
curl -X POST http://localhost:8000/api/queries/ \\
  -H "Content-Type: application/json" \\
  -d '{"problem":"Your question here","capabilities":["general"]}'

# Post a freelance task
curl -X POST http://localhost:8000/api/freelance/ \\
  -H "Content-Type: application/json" \\
  -d '{"title":"Write a DeFi spec","description":"...","task_type":"document"}'`}
          </pre>
        </div>
      </section>
    </div>
  );
}
