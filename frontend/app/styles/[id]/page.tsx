"use client";

import { useCallback, useEffect, useState } from "react";
import { useParams, useRouter } from "next/navigation";
import AnimatedBackground from "@/components/ui/AnimatedBackground";
import AppHeader from "@/components/ui/AppHeader";
import SpinningDisc from "@/components/ui/SpinningDisc";
import { EditMap, FEATURE_LABELS, FeatureWeights, STATUS_PILL, StatTile, TransitionMix } from "@/components/styles/StyleViz";
import {
  StyleProfile, addStyleExample, deleteStyle, deleteStyleExample, getStyle, getToken, trainStyle,
} from "@/lib/api";

const BUSY = ["analyzing", "training"];

export default function StyleDetailPage() {
  const router = useRouter();
  const id = useParams().id as string;
  const [p, setP] = useState<StyleProfile | null>(null);
  const [error, setError] = useState("");
  const [edited, setEdited] = useState<File | null>(null);
  const [raws, setRaws] = useState<File[]>([]);
  const [title, setTitle] = useState("");
  const [uploadPct, setUploadPct] = useState<number | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  const load = useCallback(async () => {
    try {
      setP(await getStyle(id));
    } catch (e: any) {
      if (/401|token/i.test(String(e.message))) router.push("/login");
      else setError(e.message);
    }
  }, [id, router]);

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    load();
  }, [load, router]);

  // poll while analyzing / training
  useEffect(() => {
    if (!p || !BUSY.includes(p.status)) return;
    const h = setInterval(load, 2000);
    return () => clearInterval(h);
  }, [p, load]);

  async function upload(e: React.FormEvent) {
    e.preventDefault();
    if (!edited || !raws.length) return;
    setError("");
    setUploadPct(0);
    try {
      await addStyleExample(id, edited, raws, title.trim(), setUploadPct);
      setEdited(null);
      setRaws([]);
      setTitle("");
      (document.getElementById("edited-input") as HTMLInputElement | null)?.form?.reset();
      await load();
    } catch (err: any) {
      setError(err.message || "Upload failed");
    } finally {
      setUploadPct(null);
    }
  }

  async function train(reanalyze = false) {
    setError("");
    try {
      setP(await trainStyle(id, reanalyze));
    } catch (e: any) {
      setError(e.message);
    }
  }

  if (!p) {
    return (
      <main className="relative flex min-h-screen items-center justify-center text-slate-400">
        <AnimatedBackground />
        {error ? <span className="text-red-400">{error}</span> : <SpinningDisc size={80} fast />}
      </main>
    );
  }

  const busy = BUSY.includes(p.status);
  const style = p.style;
  const cv = p.metrics?.cv;

  return (
    <main className="relative min-h-screen">
      <AnimatedBackground />
      <AppHeader title={`🎓 ${p.name}`} subtitle={p.status} backHref="/styles" />
      <div className="mx-auto max-w-6xl space-y-6 px-6 py-8">
        {error && <div className="rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">{error}</div>}

        {/* Status + train */}
        <section className="glass flex flex-col gap-4 rounded-3xl p-6 md:flex-row md:items-center">
          <div className="flex-1">
            <div className="flex items-center gap-2">
              <span className={`rounded-full px-2.5 py-0.5 text-[11px] font-medium capitalize ${STATUS_PILL[p.status]}`}>{p.status}</span>
              {busy && <span className="text-xs text-slate-400">{p.metrics?.stage || "working"}…</span>}
              {p.trained_at && !busy && <span className="text-xs text-slate-500">trained {new Date(p.trained_at).toLocaleString()}</span>}
            </div>
            {p.description && <p className="mt-2 text-sm text-slate-400">{p.description}</p>}
            {p.status === "failed" && p.error_message && <p className="mt-2 text-xs text-red-300">{p.error_message}</p>}
          </div>
          <div className="flex flex-wrap gap-2">
            <button className="btn-primary" disabled={busy || !p.examples?.length} onClick={() => train(false)}>
              {busy ? (<><span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" /> Learning…</>)
                : p.status === "ready" ? "↻ Retrain" : "🧠 Train style"}
            </button>
            {p.status === "ready" && (
              <button className="btn-ghost" disabled={busy} onClick={() => train(true)} title="Re-run the full analysis on every example">
                Re-analyze all
              </button>
            )}
            {confirmDelete ? (
              <>
                <button className="btn-ghost !border-red-500/40 !text-red-300" onClick={async () => { await deleteStyle(id); router.push("/styles"); }}>
                  Confirm delete
                </button>
                <button className="btn-ghost" onClick={() => setConfirmDelete(false)}>Cancel</button>
              </>
            ) : (
              <button className="btn-ghost" disabled={busy} onClick={() => setConfirmDelete(true)}>Delete style</button>
            )}
          </div>
        </section>

        {/* Learned style */}
        {style && p.status === "ready" && (
          <section className="glass space-y-6 rounded-3xl p-6">
            <h2 className="text-lg font-bold">What the AI learned</h2>
            <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
              <StatTile label="Pacing" value={style.pacing?.median_shot_ms ? `${(style.pacing.median_shot_ms / 1000).toFixed(1)}s` : "—"}
                sub={style.pacing?.cuts_per_min ? `per shot · ${Math.round(style.pacing.cuts_per_min)} cuts/min` : "per shot"} />
              <StatTile label="Footage kept" value={style.duration?.ratio != null ? `${Math.round(style.duration.ratio * 100)}%` : "—"}
                sub={style.duration?.typical_ms ? `edits run ~${Math.round(style.duration.typical_ms / 1000)}s` : undefined} />
              <StatTile label="Held-out accuracy" value={cv?.balanced_accuracy != null ? `${Math.round(cv.balanced_accuracy * 100)}%` : "—"}
                sub={cv ? `vs ${Math.round(cv.majority_baseline * 100)}% guessing · ${cv.scheme}` : "needs more varied examples"} />
              <StatTile label="Matched to raw" value={style.alignment_coverage != null ? `${Math.round(style.alignment_coverage * 100)}%` : "—"}
                sub={`${p.metrics?.samples ?? 0} keep/cut decisions`} />
            </div>

            <div className="grid gap-6 lg:grid-cols-2">
              <div>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Transitions</h3>
                <TransitionMix mix={style.transitions || {}} />
                <h3 className="mb-2 mt-6 text-xs font-bold uppercase tracking-wider text-slate-400">Look & sound</h3>
                <ul className="space-y-1 text-sm text-slate-300">
                  <li>🎨 {describeColor(style.color)}</li>
                  <li>🔊 {({ original: "Keeps the original sound", mixed: "Mixes original sound with music / voice",
                    replaced: "Replaces the original sound with music / voice-over", silent: "Silent edits" } as Record<string, string>)[style.audio?.mode] || "Audio style not determined"}</li>
                  <li>📐 Exports {style.aspect === "9:16" ? "vertical 9:16" : "horizontal 16:9"}</li>
                  <li>🧭 {style.order?.chronological == null ? "Story order not determined" : style.order.chronological > 0.5 ? "Follows the footage in order"
                    : style.order.chronological < 0.1 ? "Rearranges footage freely" : "Mostly in order, some rearranging"}
                    {style.order?.hook_from_later ? " · opens with a hook" : ""}</li>
                </ul>
              </div>
              <div>
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">What makes you keep or cut a shot</h3>
                <FeatureWeights weights={p.metrics?.importance || []} labels={FEATURE_LABELS} />
              </div>
            </div>

            {p.summary && (
              <div className="rounded-2xl border border-white/10 bg-black/20 p-4">
                <h3 className="mb-2 text-xs font-bold uppercase tracking-wider text-slate-400">Style summary</h3>
                <p className="whitespace-pre-line text-sm leading-relaxed text-slate-300">{p.summary}</p>
              </div>
            )}
          </section>
        )}

        {/* Add example */}
        <section className="glass rounded-3xl p-6">
          <h2 className="text-lg font-bold">Add a training example</h2>
          <p className="mb-4 text-xs text-slate-400">
            One finished video you edited, plus the raw clips you cut it from. Only real pairs teach anything — the AI checks that
            the edit can be found inside the raw footage.
          </p>
          <form onSubmit={upload} className="grid gap-4 md:grid-cols-2">
            <label className="block text-xs text-slate-400">
              Finished edit (1 video)
              <input id="edited-input" type="file" accept="video/*" required disabled={uploadPct !== null}
                onChange={(e) => setEdited(e.target.files?.[0] || null)}
                className="mt-1 block w-full text-xs text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-accent/20 file:px-3 file:py-1.5 file:text-accent" />
            </label>
            <label className="block text-xs text-slate-400">
              Raw clips it was made from (select several)
              <input type="file" accept="video/*" multiple required disabled={uploadPct !== null}
                onChange={(e) => setRaws(Array.from(e.target.files || []))}
                className="mt-1 block w-full text-xs text-slate-300 file:mr-3 file:rounded-lg file:border-0 file:bg-accent/20 file:px-3 file:py-1.5 file:text-accent" />
            </label>
            <input value={title} onChange={(e) => setTitle(e.target.value)} placeholder="Title (optional)" className="input-field" maxLength={255} />
            <button className="btn-primary" disabled={!edited || !raws.length || uploadPct !== null || busy}>
              {uploadPct !== null ? `Uploading… ${uploadPct}%` : `⬆ Add example${raws.length ? ` (${raws.length} raw clips)` : ""}`}
            </button>
            {uploadPct !== null && (
              <div className="h-1.5 overflow-hidden rounded bg-white/10 md:col-span-2">
                <div className="h-full bg-gradient-to-r from-accent to-accent4 transition-all" style={{ width: `${uploadPct}%` }} />
              </div>
            )}
          </form>
        </section>

        {/* Examples */}
        <section className="space-y-4">
          <h2 className="text-lg font-bold">Examples <span className="text-slate-500">· {p.examples?.length ?? 0}</span></h2>
          {!p.examples?.length && <div className="glass rounded-3xl py-10 text-center text-sm text-slate-400">No examples yet.</div>}
          {p.examples?.map((ex) => {
            const a = ex.analysis;
            const rawNames = ex.assets.filter((x) => x.kind === "raw").map((x) => x.original_filename);
            return (
              <div key={ex.id} className="glass rounded-2xl p-5">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <p className="truncate font-semibold">{ex.title}</p>
                    <p className="text-[11px] text-slate-500">
                      edit: {ex.assets.find((x) => x.kind === "edited")?.original_filename} · {rawNames.length} raw clip(s)
                    </p>
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className={`rounded-full px-2 py-0.5 text-[10px] capitalize ${STATUS_PILL[ex.status] || STATUS_PILL.draft}`}>
                      {ex.status === "draft" ? "not analyzed" : ex.status}
                    </span>
                    <button className="text-xs text-slate-500 hover:text-red-300" disabled={busy} aria-label="Remove example"
                      onClick={async () => { await deleteStyleExample(id, ex.id).catch((e) => setError(e.message)); load(); }}>✕</button>
                  </div>
                </div>
                {ex.error_message && <p className="mt-2 text-xs text-red-300">{ex.error_message}</p>}
                {a && (
                  <div className="mt-4 space-y-3">
                    <div className="grid grid-cols-2 gap-2 text-[11px] text-slate-400 sm:grid-cols-4">
                      <span><b className="text-slate-200">{Math.round((a.coverage ?? 0) * 100)}%</b> matched to raw</span>
                      <span><b className="text-slate-200">{a.edited?.shot_count}</b> shots · {a.pacing?.cuts_per_min} cuts/min</span>
                      <span><b className="text-slate-200">{a.kept_windows}/{a.total_windows}</b> raw moments kept</span>
                      <span><b className="text-slate-200">{((a.edited?.duration_ms ?? 0) / 1000).toFixed(0)}s</b> from {((a.raw_total_ms ?? 0) / 1000).toFixed(0)}s raw</span>
                    </div>
                    {a.shots?.length > 0 && <EditMap shots={a.shots} rawNames={rawNames} durationMs={a.edited?.duration_ms ?? 1} />}
                    {(a.coverage ?? 0) < 0.3 && (
                      <p className="text-[11px] text-amber-300">
                        ⚠ Little of this edit was found in its raw clips — double-check you uploaded the right footage.
                      </p>
                    )}
                  </div>
                )}
              </div>
            );
          })}
        </section>
      </div>
    </main>
  );
}

function describeColor(c: Record<string, number> | null | undefined): string {
  if (!c) return "Color grade not measured";
  const bits: string[] = [];
  if (Math.abs(c.brightness ?? 0) > 0.015) bits.push(c.brightness > 0 ? "brighter" : "darker");
  if (Math.abs((c.contrast ?? 1) - 1) > 0.04) bits.push(`${c.contrast > 1 ? "+" : ""}${Math.round((c.contrast - 1) * 100)}% contrast`);
  if (Math.abs((c.saturation ?? 1) - 1) > 0.05) bits.push(`${c.saturation > 1 ? "+" : ""}${Math.round((c.saturation - 1) * 100)}% saturation`);
  if (Math.abs(c.warmth ?? 0) > 0.02) bits.push(c.warmth > 0 ? "warmer" : "cooler");
  return bits.length ? `Grades ${bits.join(", ")}` : "Keeps colors close to the original";
}
