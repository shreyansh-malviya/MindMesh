"use client";

export type BotState = "idle" | "thinking" | "speaking" | "done" | "waiting";

const AGENT_COLORS = [
  "#836EF9", // monad purple
  "#22d3ee", // cyan
  "#4ade80", // green
  "#fb923c", // orange
  "#f472b6", // pink
  "#60a5fa", // blue
];

const AGENT_ACCENTS = [
  "#6d28d9",
  "#0891b2",
  "#15803d",
  "#c2410c",
  "#be185d",
  "#1d4ed8",
];

interface BotAvatarProps {
  name: string;
  role?: string;
  index: number;
  state: BotState;
  address?: string;
}

export function BotAvatar({ name, role, index, state }: BotAvatarProps) {
  const color = AGENT_COLORS[index % AGENT_COLORS.length];
  const accent = AGENT_ACCENTS[index % AGENT_ACCENTS.length];
  const isThinking = state === "thinking";
  const isSpeaking = state === "speaking";
  const isDone = state === "done";
  const isActive = isThinking || isSpeaking;

  return (
    <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 6, opacity: isDone ? 0.5 : 1, transition: "opacity 0.4s" }}>
      {/* Antenna */}
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 0 }}>
        <div style={{
          width: 8, height: 8, borderRadius: "50%",
          background: isActive ? color : "var(--border-strong)",
          boxShadow: isActive ? `0 0 8px ${color}` : "none",
          transition: "all 0.3s",
          animation: isThinking ? "antennaPulse 0.8s infinite" : isSpeaking ? "antennaPulse 0.4s infinite" : "none",
        }} />
        <div style={{
          width: 2, height: 14,
          background: isActive ? color : "var(--border-strong)",
          transition: "background 0.3s",
        }} />
      </div>

      {/* Head */}
      <div style={{
        position: "relative",
        width: 72, height: 64,
        borderRadius: 10,
        background: "linear-gradient(160deg, #1e1e2e 60%, #2a2a3e)",
        border: `2px solid ${isActive ? color : "var(--border-strong)"}`,
        boxShadow: isActive ? `0 0 18px ${color}55, inset 0 0 10px ${color}11` : "0 2px 8px #0004",
        transition: "border-color 0.3s, box-shadow 0.3s",
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        justifyContent: "center",
        gap: 8,
        overflow: "hidden",
      }}>
        {/* Visor strip at top */}
        <div style={{
          position: "absolute", top: 0, left: 0, right: 0, height: 4,
          background: `linear-gradient(90deg, transparent, ${color}66, transparent)`,
          opacity: isActive ? 1 : 0.3,
          transition: "opacity 0.3s",
        }} />

        {/* Eyes */}
        <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <Eye color={color} state={state} delay={0} />
          <Eye color={color} state={state} delay={0.08} />
        </div>

        {/* Mouth */}
        <Mouth color={color} accent={accent} state={state} />

        {/* Scan line */}
        {isActive && (
          <div style={{
            position: "absolute", left: 0, right: 0, height: 1,
            background: `${color}44`,
            animation: "scanLine 1.6s linear infinite",
          }} />
        )}
      </div>

      {/* Shoulders */}
      <div style={{
        display: "flex", gap: 4, alignItems: "flex-start",
      }}>
        <div style={{
          width: 10, height: 12, borderRadius: "4px 4px 0 0",
          background: "linear-gradient(180deg, #2a2a3e, #1a1a2e)",
          border: `1px solid ${isActive ? color + "66" : "var(--border)"}`,
          transition: "border-color 0.3s",
        }} />
        <div style={{
          width: 52, height: 16, borderRadius: "6px 6px 0 0",
          background: "linear-gradient(180deg, #252535, #1a1a2e)",
          border: `1px solid ${isActive ? color + "66" : "var(--border)"}`,
          display: "flex", alignItems: "center", justifyContent: "center",
          transition: "border-color 0.3s",
        }}>
          <div style={{
            width: 20, height: 4, borderRadius: 2,
            background: isActive ? color : "var(--border-strong)",
            opacity: isActive ? 0.9 : 0.3,
            transition: "all 0.3s",
            animation: isSpeaking ? "speakerPulse 0.5s infinite alternate" : "none",
          }} />
        </div>
        <div style={{
          width: 10, height: 12, borderRadius: "4px 4px 0 0",
          background: "linear-gradient(180deg, #2a2a3e, #1a1a2e)",
          border: `1px solid ${isActive ? color + "66" : "var(--border)"}`,
          transition: "border-color 0.3s",
        }} />
      </div>

      {/* Name tag */}
      <div style={{ textAlign: "center", marginTop: 2 }}>
        <div style={{
          fontSize: 11, fontWeight: 700, letterSpacing: "0.04em",
          color: isActive ? color : "var(--text-1)",
          transition: "color 0.3s",
          fontFamily: "var(--mono)",
        }}>
          {name.toUpperCase()}
        </div>
        {role && (
          <div style={{
            fontSize: 9, color: "var(--text-3)", marginTop: 1,
            letterSpacing: "0.06em", textTransform: "uppercase",
            maxWidth: 80, textAlign: "center", lineHeight: 1.3,
          }}>
            {role}
          </div>
        )}
        {/* Status pill */}
        <div style={{
          marginTop: 4, display: "inline-flex", alignItems: "center", gap: 4,
          padding: "2px 8px", borderRadius: 99,
          background: isActive ? `${color}22` : "var(--bg-subtle)",
          border: `1px solid ${isActive ? color + "55" : "var(--border)"}`,
          transition: "all 0.3s",
        }}>
          <div style={{
            width: 5, height: 5, borderRadius: "50%",
            background: isDone ? "var(--green)" : isActive ? color : "var(--border-strong)",
            animation: isActive ? "pulse 1s infinite" : "none",
            transition: "background 0.3s",
          }} />
          <span style={{
            fontSize: 9, fontFamily: "var(--mono)", fontWeight: 600,
            color: isDone ? "var(--green)" : isActive ? color : "var(--text-3)",
            transition: "color 0.3s",
          }}>
            {isDone ? "done" : isThinking ? "thinking" : isSpeaking ? "speaking" : "idle"}
          </span>
        </div>
      </div>
    </div>
  );
}

function Eye({ color, state, delay }: { color: string; state: BotState; delay: number }) {
  const isThinking = state === "thinking";
  const isSpeaking = state === "speaking";

  return (
    <div style={{
      width: 16, height: 16,
      borderRadius: 3,
      background: "#0d0d1a",
      border: `1.5px solid ${(isThinking || isSpeaking) ? color + "99" : "#333"}`,
      display: "flex", alignItems: "center", justifyContent: "center",
      overflow: "hidden",
      transition: "border-color 0.3s",
    }}>
      {/* Pupil */}
      <div style={{
        width: 7, height: 7,
        borderRadius: "50%",
        background: (isThinking || isSpeaking) ? color : "#444",
        boxShadow: (isThinking || isSpeaking) ? `0 0 6px ${color}` : "none",
        animation: isThinking
          ? `eyeMove 0.7s ${delay}s ease-in-out infinite`
          : isSpeaking
          ? `eyePulse 0.5s ${delay}s ease-in-out infinite alternate`
          : `eyeBlink 3s ${delay * 5}s ease-in-out infinite`,
        transition: "background 0.3s, box-shadow 0.3s",
      }} />
    </div>
  );
}

function Mouth({ color, accent, state }: { color: string; accent: string; state: BotState }) {
  const isSpeaking = state === "speaking";
  const isThinking = state === "thinking";

  if (isSpeaking) {
    return (
      <div style={{
        width: 28, height: 10, borderRadius: 4,
        background: `linear-gradient(90deg, ${accent}, ${color})`,
        animation: "mouthSpeak 0.25s ease-in-out infinite alternate",
        boxShadow: `0 0 8px ${color}88`,
      }} />
    );
  }

  if (isThinking) {
    return (
      <div style={{ display: "flex", gap: 3, alignItems: "center" }}>
        {[0, 1, 2].map((i) => (
          <div key={i} style={{
            width: 4, height: 4, borderRadius: "50%",
            background: color,
            animation: `thinkDot 0.9s ${i * 0.2}s ease-in-out infinite`,
            boxShadow: `0 0 4px ${color}`,
          }} />
        ))}
      </div>
    );
  }

  return (
    <div style={{
      width: 24, height: 3, borderRadius: 2,
      background: "#333",
    }} />
  );
}
