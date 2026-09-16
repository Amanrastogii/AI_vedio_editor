"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { AGENTS } from "@/lib/agents";
import { getPipeline, openPipelineSocket } from "@/lib/api";
import { useWebSocket } from "@/hooks/useWebSocket";

type AgentState = {
  status: "pending" | "running" | "completed" | "failed";
  pct: number;
  message: string;
  summary?: string;
  aiMode?: string | null;
};

interface Props {
  projectId: string;
  initialStatus: string;
  onComplete?: () => void;
}

const emptyState = (): Record<string, AgentState> =>
  Object.fromEntries(AGENTS.map((a) => [a.key, { status: "pending", pct: 0, message: "" }])) as Record<
    string,
    AgentState
  >;

export default function PipelineView({ projectId, initialStatus, onComplete }: Props) {
  const [states, setStates] = useState<Record<string, AgentState>>(emptyState);
  const [pipelineStatus, setPipelineStatus] = useState(initialStatus);
  const [summary, setSummary] = useState<Record<string, any> | null>(null);
  const [startedAt, setStartedAt] = useState<number | null>(null);
  const [now, setNow] = useState(Date.now());

  function update(key: string, patch: Partial<AgentState>) {
    setStates((prev) => ({ ...prev, [key]: { ...prev[key], ...patch } }));
  }

  async function hydrate() {
    try {
      const p = await getPipeline(projectId);
      setPipelineStatus(p.project_status);
      setStates((prev) => {
        const next = { ...prev };
        for (const a of p.agents) {
          const key = a.agent;
          if (!next[key]) continue;
          const status = a.status as AgentState["status"];
          next[key] = {
            status,
            pct: a.progress_pct ?? (status === "completed" ? 100 : 0),
            message: status === "running" ? a.current_message || next[key].message : next[key].message,
            summary: next[key].summary,
            aiMode: a.result_metadata?.ai_mode ?? next[key].aiMode ?? null,
          };
        }
        return next;
      });
      if (!startedAt) {
        const first = p.agents.find((a) => a.started_at)?.started_at;
        if (first) setStartedAt(new Date(first).getTime());
      }
    } catch {
      /* ignore */
    }
  }

  // Hydrate from REST first (covers reload / already-finished runs), then
  // keep polling as a fallback in case the WebSocket is down.
  useEffect(() => {
    hydrate();
    if (initialStatus !== "processing") return;
    const interval = setInterval(hydrate, 4000);
    return () => clearInterval(interval);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId]);

  // Ticking clock for elapsed time.
  useEffect(() => {
    if (pipelineStatus !== "processing") return;
    const id = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(id);
  }, [pipelineStatus]);

  // Live updates via WebSocket, with reconnect/backoff.
  useWebSocket(
    initialStatus === "processing",
    () => openPipelineSocket(projectId),
    (data) => {
      if (data.event === "agent.started") {
        update(data.agent, { status: "running", pct: 0 });
        if (!startedAt) setStartedAt(Date.now());
      } else if (data.event === "agent.progress") {
        update(data.agent, {
          status: "running",
          pct: data.progress_pct ?? 0,
          message: data.message || "",
        });
      } else if (data.event === "agent.completed") {
        update(data.agent, { status: "completed", pct: 100, summary: data.summary });
      } else if (data.event === "pipeline.complete") {
        setPipelineStatus("completed");
        setSummary(data.summary);
        onComplete?.();
      } else if (data.event === "pipeline.failed") {
        setPipelineStatus("failed");
      }
    }
  );

  const doneCount = Object.values(states).filter((s) => s.status === "completed").length;
  const overallPct = useMemo(() => {
    const vals = Object.values(states).map((s) => (s.status === "completed" ? 100 : s.pct));
    return Math.round(vals.reduce((a, b) => a + b, 0) / vals.length);
  }, [states]);
  const runningAgent = AGENTS.find((a) => states[a.key]?.status === "running");
  const elapsed = startedAt ? Math.max(0, Math.floor((now - startedAt) / 1000)) : 0;
  const mm = String(Math.floor(elapsed / 60)).padStart(2, "0");
  const ss = String(elapsed % 60).padStart(2, "0");

  return (
    <div>
      <div className="glass mb-5 rounded-2xl p-5">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="flex items-center gap-3">
            <span
              className={`h-2.5 w-2.5 rounded-full ${
                pipelineStatus === "processing"
                  ? "bg-amber-400 animate-pulse"
                  : pipelineStatus === "completed"
                  ? "bg-emerald-400"
                  : pipelineStatus === "failed"
                  ? "bg-red-400"
                  : "bg-slate-500"
              }`}
            />
            <span className="text-sm font-medium capitalize">{pipelineStatus}</span>
            {pipelineStatus === "processing" && (
              <span className="text-xs text-slate-400">
                {runningAgent ? `Running: ${runningAgent.label}` : "Starting…"} · {mm}:{ss} elapsed
              </span>
            )}
          </div>
          <span className="text-sm text-slate-400">{doneCount}/11 agents complete · {overallPct}%</span>
        </div>
        <div className="relative h-2.5 w-full overflow-hidden rounded-full bg-white/5">
          <div
            className="relative h-full overflow-hidden rounded-full bg-gradient-to-r from-accent via-accent4 to-accent2 transition-all duration-700"
            style={{ width: `${overallPct}%` }}
          >
            {pipelineStatus === "processing" && (
              <div className="absolute inset-0 animate-shimmer bg-[linear-gradient(90deg,transparent,rgba(255,255,255,0.45),transparent)] bg-[length:200%_100%]" />
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
        {AGENTS.map((a, i) => {
          const st = states[a.key];
          return (
            <div
              key={a.key}
              className={`glass relative animate-fade-up overflow-hidden rounded-2xl p-4 transition-all duration-500 ${
                st.status === "running" ? "scale-[1.01]" : ""
              }`}
              style={{
                borderLeft: `3px solid ${a.color}`,
                opacity: st.status === "pending" ? 0.55 : 1,
                animationDelay: `${i * 40}ms`,
                boxShadow: st.status === "running" ? `0 0 32px -8px ${a.color}` : undefined,
              }}
            >
              <div className="flex items-center justify-between">
                <div className="flex items-center gap-2.5">
                  <span
                    className="flex h-7 w-7 items-center justify-center rounded-full text-xs font-bold"
                    style={{ background: `${a.color}33`, color: a.color }}
                  >
                    {a.num}
                  </span>
                  <span className="text-sm font-semibold">{a.label}</span>
                </div>
                <div className="flex items-center gap-1.5">
                  {st.aiMode && <AiModeBadge mode={st.aiMode} />}
                  <StatusPill status={st.status} />
                </div>
              </div>

              <p className="mt-2 min-h-[16px] text-xs text-slate-400">
                {st.message || st.summary || a.desc}
              </p>

              {(st.status === "running" || st.status === "completed") && (
                <div className="mt-2.5 h-1.5 w-full overflow-hidden rounded-full bg-surface2">
                  <div
                    className="h-full rounded-full transition-all duration-500"
                    style={{ width: `${st.pct}%`, background: a.color }}
                  />
                </div>
              )}
            </div>
          );
        })}
      </div>

      {summary && (
        <div className="mt-6 rounded-xl border border-emerald-500/30 bg-emerald-500/10 p-4">
          <p className="mb-2 text-sm font-semibold text-emerald-300">✓ Pipeline complete</p>
          <div className="flex flex-wrap gap-4 text-xs text-slate-300">
            <span>{summary.segments} segments</span>
            <span>{summary.words_transcribed} words</span>
            <span>{summary.emotional_peaks} emotional peaks</span>
            <span>{summary.story_beats} story beats</span>
            <span>{summary.formats} formats rendered</span>
          </div>
        </div>
      )}
    </div>
  );
}

function StatusPill({ status }: { status: AgentState["status"] }) {
  const map = {
    pending: { t: "Pending", c: "bg-slate-500/20 text-slate-400" },
    running: { t: "Running", c: "bg-amber-500/20 text-amber-300 animate-pulse" },
    completed: { t: "Done", c: "bg-emerald-500/20 text-emerald-300" },
    failed: { t: "Failed", c: "bg-red-500/20 text-red-300" },
  }[status];
  return <span className={`rounded-full px-2.5 py-0.5 text-[10px] font-medium ${map.c}`}>{map.t}</span>;
}

function AiModeBadge({ mode }: { mode: string }) {
  const label = mode === "placeholder" ? "placeholder AI" : mode === "local" ? "local AI" : "cloud AI";
  const c =
    mode === "placeholder"
      ? "bg-slate-500/20 text-slate-400"
      : mode === "local"
      ? "bg-sky-500/20 text-sky-300"
      : "bg-violet-500/20 text-violet-300";
  return <span className={`rounded-full px-2 py-0.5 text-[9px] font-medium ${c}`}>{label}</span>;
}
