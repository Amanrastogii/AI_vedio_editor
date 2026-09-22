"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter, useParams } from "next/navigation";
import PipelineView from "@/components/PipelineView";
import Editor from "@/components/editor/Editor";
import AnimatedBackground from "@/components/ui/AnimatedBackground";
import AppHeader from "@/components/ui/AppHeader";
import SpinningDisc from "@/components/ui/SpinningDisc";
import { FORMAT_META } from "@/lib/agents";
import {
  Clip,
  Output,
  Project,
  StyleProfile,
  getProject,
  getToken,
  ingestUploaded,
  listClips,
  listOutputs,
  listStyles,
  startProcessing,
  updateProject,
  uploadClip,
} from "@/lib/api";

type Tab = "upload" | "dashboard" | "edit" | "outputs";
const TABS: { key: Tab; label: string; icon: string }[] = [
  { key: "upload", label: "Upload", icon: "⬆️" },
  { key: "dashboard", label: "Dashboard", icon: "📊" },
  { key: "edit", label: "Edit", icon: "🎛" },
  { key: "outputs", label: "Outputs", icon: "🎬" },
];

export default function ProjectPage() {
  const router = useRouter();
  const params = useParams();
  const id = params.id as string;

  const [project, setProject] = useState<Project | null>(null);
  const [clips, setClips] = useState<Clip[]>([]);
  const [outputs, setOutputs] = useState<Output[]>([]);
  const [tab, setTab] = useState<Tab>("upload");
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [processing, setProcessing] = useState(false);
  const [preparing, setPreparing] = useState(false);
  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [error, setError] = useState("");
  const fileRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    listStyles().then((all) => setStyles(all.filter((s) => s.status === "ready"))).catch(() => {});
  }, []);

  async function chooseStyle(profileId: string) {
    if (!project) return;
    const target_style = { ...(project.target_style || {}) };
    if (profileId) target_style.style_profile_id = profileId;
    else delete target_style.style_profile_id;
    try {
      setProject(await updateProject(id, { target_style }));
    } catch (e: any) {
      setError(e.message || "Couldn't update the style");
    }
  }

  async function handleManual() {
    setPreparing(true);
    setError("");
    try {
      await ingestUploaded(id);
      setTab("edit");
    } catch (e: any) {
      setError(e.message || "Couldn't prepare clips");
    } finally {
      setPreparing(false);
    }
  }

  async function load() {
    try {
      const [p, c] = await Promise.all([getProject(id), listClips(id)]);
      setProject(p);
      setClips(c);
      listOutputs(id).then(setOutputs).catch(() => {});
      if (p.status === "processing") setTab("dashboard");
      else if (p.status === "completed") setTab("outputs");
    } catch (e: any) {
      if (/401|token/i.test(String(e.message))) router.push("/login");
      else setError(e.message);
    }
  }

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function handleFiles(files: FileList | null) {
    if (!files || files.length === 0) return;
    setError("");
    setUploading(true);
    try {
      for (const f of Array.from(files)) {
        await uploadClip(id, f);
      }
      setClips(await listClips(id));
    } catch (e: any) {
      setError(e.message || "Upload failed");
    } finally {
      setUploading(false);
    }
  }

  async function handleProcess() {
    setProcessing(true);
    setError("");
    try {
      await startProcessing(id);
      const p = await getProject(id);
      setProject(p);
      setTab("dashboard");
    } catch (e: any) {
      setError(e.message || "Failed to start processing");
    } finally {
      setProcessing(false);
    }
  }

  async function onPipelineComplete() {
    const [p, o] = await Promise.all([getProject(id), listOutputs(id)]);
    setProject(p);
    setOutputs(o);
  }

  if (!project) {
    return (
      <main className="relative flex min-h-screen flex-col items-center justify-center gap-6 text-slate-400">
        <AnimatedBackground />
        {error ? (
          <span className="text-red-400">{error}</span>
        ) : (
          <>
            <SpinningDisc size={96} fast />
            <span className="animate-pulse text-sm">Loading project…</span>
          </>
        )}
      </main>
    );
  }

  const canUpload = ["created", "uploading"].includes(project.status);

  return (
    <main className="relative min-h-screen">
      <AnimatedBackground />
      <AppHeader title={project.title} subtitle={project.status} backHref="/" />

      <div className={`mx-auto px-6 py-8 ${tab === "edit" ? "max-w-[1600px]" : "max-w-6xl"}`}>
        {/* Tabs */}
        <div className="glass sticky top-[68px] z-10 mx-auto mb-6 grid max-w-6xl animate-fade-up grid-cols-4 gap-1 rounded-2xl p-1.5">
          {TABS.map((t) => {
            const active = tab === t.key;
            return (
              <button
                key={t.key}
                onClick={() => {
                  setTab(t.key);
                  if (t.key === "outputs") listOutputs(id).then(setOutputs);
                }}
                className={`relative flex items-center justify-center gap-2 rounded-xl px-3 py-2.5 text-sm font-medium transition-all duration-300 ${
                  active
                    ? "bg-gradient-to-r from-accent to-accent4 text-white shadow-[0_8px_24px_-8px_rgba(124,131,245,0.7)]"
                    : "text-slate-400 hover:bg-white/5 hover:text-white"
                }`}
              >
                <span className={`transition-transform duration-300 ${active ? "scale-110" : ""}`}>{t.icon}</span>
                <span className="hidden sm:inline">{t.label}</span>
                {t.key === "upload" && clips.length > 0 && (
                  <span className="rounded-full bg-black/30 px-1.5 text-[10px]">{clips.length}</span>
                )}
              </button>
            );
          })}
        </div>

        {error && (
          <div className="mb-4 animate-fade-in rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
            {error}
          </div>
        )}

        <div key={tab} className="animate-fade-up">
        {/* Upload tab */}
        {tab === "upload" && (
          <div>
            <div
              onClick={() => canUpload && fileRef.current?.click()}
              onDragOver={(e) => {
                e.preventDefault();
                setDragOver(true);
              }}
              onDragLeave={() => setDragOver(false)}
              onDrop={(e) => {
                e.preventDefault();
                setDragOver(false);
                if (canUpload) handleFiles(e.dataTransfer.files);
              }}
              className={`glass group relative flex min-h-[240px] flex-col items-center justify-center overflow-hidden rounded-3xl border-2 border-dashed p-10 text-center transition-all duration-300 ${
                canUpload ? "cursor-pointer" : "cursor-not-allowed opacity-70"
              } ${dragOver ? "scale-[1.01] border-accent bg-accent/10" : "border-white/10 hover:border-accent/60"}`}
            >
              <input
                ref={fileRef}
                type="file"
                accept="video/*"
                multiple
                hidden
                onChange={(e) => handleFiles(e.target.files)}
              />
              <div className="absolute inset-0 bg-gradient-to-br from-accent/5 via-transparent to-accent4/5 opacity-0 transition-opacity duration-300 group-hover:opacity-100" />
              <div className="relative mb-4">
                {uploading ? (
                  <SpinningDisc size={72} fast glow={false} />
                ) : (
                  <div className="flex h-16 w-16 animate-float items-center justify-center rounded-2xl bg-gradient-to-br from-accent to-accent4 text-3xl shadow-[0_12px_32px_-8px_rgba(124,131,245,0.7)]">
                    ⬆️
                  </div>
                )}
              </div>
              <p className="relative text-lg font-bold">
                {uploading
                  ? "Uploading your clips…"
                  : !canUpload
                  ? "Uploads are closed for this project"
                  : dragOver
                  ? "Drop to upload"
                  : "Drag & drop your video clips"}
              </p>
              <p className="relative mt-1 text-sm text-slate-400">
                {canUpload ? "or click to browse · MP4, MOV, WebM · multiple files" : `Project is ${project.status}.`}
              </p>
            </div>

            {clips.length > 0 && (
              <div className="mt-8">
                <h3 className="mb-3 text-sm font-bold uppercase tracking-wider text-slate-300">
                  Uploaded clips <span className="text-slate-500">· {clips.length}</span>
                </h3>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {clips.map((c, i) => (
                    <div
                      key={c.id}
                      className="glass card-hover animate-fade-up overflow-hidden rounded-2xl"
                      style={{ animationDelay: `${i * 60}ms` }}
                    >
                      <div className="relative aspect-video bg-gradient-to-br from-accent/30 to-accent4/20">
                        {c.thumbnail_url ? (
                          // eslint-disable-next-line @next/next/no-img-element
                          <img src={c.thumbnail_url} alt="" className="h-full w-full object-cover" />
                        ) : (
                          <div className="flex h-full items-center justify-center text-3xl opacity-60">🎬</div>
                        )}
                        <span className="absolute left-2 top-2 rounded-full bg-black/50 px-2 py-0.5 text-[10px] backdrop-blur">
                          #{c.upload_order}
                        </span>
                        {c.duration_ms && (
                          <span className="absolute bottom-2 right-2 rounded bg-black/60 px-1.5 py-0.5 text-[10px] backdrop-blur">
                            {Math.round(c.duration_ms / 1000)}s
                          </span>
                        )}
                      </div>
                      <div className="flex items-center justify-between px-3 py-2.5">
                        <div className="min-w-0">
                          <p className="truncate text-sm font-medium">{c.original_filename}</p>
                          <p className="text-[11px] text-slate-500">
                            {c.file_size_bytes ? `${(c.file_size_bytes / 1e6).toFixed(1)} MB` : "—"}
                          </p>
                        </div>
                        <span className="flex items-center gap-1 text-[11px] text-emerald-400">
                          <span className="h-1.5 w-1.5 rounded-full bg-emerald-400" /> ready
                        </span>
                      </div>
                    </div>
                  ))}
                </div>

                {canUpload && (
                  <>
                    <div className="glass mt-8 flex flex-col gap-3 rounded-2xl p-4 sm:flex-row sm:items-center">
                      <div className="flex-1">
                        <p className="text-sm font-semibold">🎓 Edit in a learned style</p>
                        <p className="text-xs text-slate-400">
                          The AI cuts this footage the way that editor would — pacing, what they keep, look & sound.{" "}
                          <a href="/styles" className="text-accent hover:underline">Train a style →</a>
                        </p>
                      </div>
                      <select
                        value={project.target_style?.style_profile_id || ""}
                        onChange={(e) => chooseStyle(e.target.value)}
                        className="input-field !w-auto !py-2"
                        aria-label="Editing style"
                      >
                        <option value="">Default AI style</option>
                        {styles.map((s) => (
                          <option key={s.id} value={s.id}>{s.name}</option>
                        ))}
                      </select>
                    </div>
                    <div className="mt-4 grid gap-3 sm:grid-cols-[1fr_auto]">
                      <button onClick={handleProcess} disabled={processing || preparing} className="btn-primary w-full py-4 text-base">
                        {processing ? (
                          <>
                            <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                            Starting the pipeline…
                          </>
                        ) : (
                          "🚀 Start AI editing — run the 11-agent pipeline"
                        )}
                      </button>
                      <button onClick={handleManual} disabled={processing || preparing} className="btn-ghost justify-center !px-5 !py-4 !text-sm">
                        {preparing ? "Preparing clips…" : "🎛 Edit manually (skip AI)"}
                      </button>
                    </div>
                  </>
                )}
              </div>
            )}
          </div>
        )}

        {/* Dashboard tab */}
        {tab === "dashboard" && (
          <PipelineView
            projectId={id}
            initialStatus={project.status}
            onComplete={onPipelineComplete}
          />
        )}

        {/* Edit tab — manual video editor: player, timeline, AI chat */}
        {tab === "edit" && (
          <Editor
            projectId={id}
            outputs={outputs}
            onRendered={() => listOutputs(id).then(setOutputs)}
          />
        )}

        {/* Outputs tab */}
        {tab === "outputs" && (
          <div>
            {outputs.length === 0 ? (
              <div className="glass flex flex-col items-center rounded-3xl py-16 text-center">
                <div className="animate-float">
                  <SpinningDisc size={100} />
                </div>
                <p className="mt-8 font-bold">No videos yet</p>
                <p className="mt-1 text-sm text-slate-400">Upload clips and run the pipeline to get your first cut.</p>
              </div>
            ) : (
              <div className="grid grid-cols-1 gap-6 md:grid-cols-2">
                {outputs.map((o, i) => {
                  const vertical = (o.height ?? 0) > (o.width ?? 0);
                  return (
                    <div
                      key={o.id}
                      className="glass card-hover animate-fade-up overflow-hidden rounded-3xl"
                      style={{ animationDelay: `${i * 80}ms` }}
                    >
                      <div className="flex items-center justify-between px-5 pt-4">
                        <div>
                          <h3 className="text-lg font-bold">{FORMAT_META[o.format]?.label || o.format}</h3>
                          <p className="text-xs text-slate-400">
                            {o.aspect_ratio} · {o.width}×{o.height}
                            {o.duration_ms ? ` · ${(o.duration_ms / 1000).toFixed(0)}s` : ""}
                            {o.file_size_bytes ? ` · ${(o.file_size_bytes / 1e6).toFixed(1)} MB` : ""}
                          </p>
                        </div>
                        {o.quality_score != null && <QualityRing score={o.quality_score} />}
                      </div>
                      <div className="p-5">
                        <div className={`mx-auto overflow-hidden rounded-2xl bg-black ${vertical ? "max-w-[240px]" : ""}`}>
                          {o.download_url ? (
                            <video
                              src={o.download_url}
                              controls
                              className={`w-full bg-black ${vertical ? "aspect-[9/16]" : "aspect-video"}`}
                            />
                          ) : o.render_metadata?.error ? (
                            <div className="flex aspect-video w-full flex-col items-center justify-center gap-1 bg-red-500/10 text-center text-xs text-red-300">
                              <span>⚠ Render failed</span>
                              <span className="text-[10px] text-red-400">{o.render_metadata.error}</span>
                            </div>
                          ) : (
                            <div className="flex aspect-video w-full items-center justify-center gap-3 text-xs text-slate-400">
                              <SpinningDisc size={40} fast glow={false} /> rendering…
                            </div>
                          )}
                        </div>
                        {o.download_url && (
                          <a href={o.download_url} download className="btn-primary mt-4 w-full">
                            ⬇ Download
                          </a>
                        )}
                      </div>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        )}
        </div>
      </div>
    </main>
  );
}

function QualityRing({ score }: { score: number }) {
  const r = 18;
  const c = 2 * Math.PI * r;
  const pct = Math.max(0, Math.min(100, score)) / 100;
  const color = score >= 80 ? "#56cfb2" : score >= 70 ? "#f5a623" : "#e96b8c";
  return (
    <div className="relative flex h-12 w-12 items-center justify-center" title="Heuristic quality score">
      <svg className="absolute inset-0 -rotate-90" viewBox="0 0 44 44">
        <circle cx="22" cy="22" r={r} fill="none" stroke="rgba(255,255,255,0.08)" strokeWidth="4" />
        <circle
          cx="22"
          cy="22"
          r={r}
          fill="none"
          stroke={color}
          strokeWidth="4"
          strokeLinecap="round"
          strokeDasharray={c}
          strokeDashoffset={c * (1 - pct)}
          style={{ transition: "stroke-dashoffset 1s ease" }}
        />
      </svg>
      <span className="text-[11px] font-bold">{Math.round(score)}</span>
    </div>
  );
}
