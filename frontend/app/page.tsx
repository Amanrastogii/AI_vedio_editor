"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import AnimatedBackground from "@/components/ui/AnimatedBackground";
import AppHeader from "@/components/ui/AppHeader";
import SpinningDisc from "@/components/ui/SpinningDisc";
import { Project, StyleProfile, createProject, deleteProject, getToken, listProjects, listStyles } from "@/lib/api";
import { FORMAT_META } from "@/lib/agents";

const ALL_FORMATS = ["youtube", "shorts", "reels", "tiktok", "linkedin"];

const FORMAT_ICON: Record<string, string> = {
  youtube: "▶️",
  shorts: "⚡",
  reels: "📸",
  tiktok: "🎵",
  linkedin: "💼",
};

const STATUS_STYLE: Record<string, { pill: string; dot: string; grad: string }> = {
  created: { pill: "bg-slate-500/15 text-slate-300", dot: "bg-slate-400", grad: "from-slate-500/40 to-slate-700/40" },
  uploading: { pill: "bg-sky-500/15 text-sky-300", dot: "bg-sky-400", grad: "from-sky-500/50 to-indigo-600/40" },
  processing: {
    pill: "bg-amber-500/15 text-amber-300",
    dot: "bg-amber-400 animate-pulse",
    grad: "from-amber-500/50 to-accent4/40",
  },
  completed: {
    pill: "bg-emerald-500/15 text-emerald-300",
    dot: "bg-emerald-400",
    grad: "from-accent/60 to-accent2/40",
  },
  failed: { pill: "bg-red-500/15 text-red-300", dot: "bg-red-400", grad: "from-red-500/50 to-rose-800/40" },
  cancelled: { pill: "bg-slate-500/15 text-slate-400", dot: "bg-slate-500", grad: "from-slate-600/40 to-slate-800/40" },
};

const STEPS = [
  { icon: "🎓", title: "Teach your style", desc: "Show it past edits + raw clips" },
  { icon: "⬆️", title: "Upload clips", desc: "Drop in your raw footage" },
  { icon: "🤖", title: "AI edits", desc: "Cuts it the way you would" },
  { icon: "🎛", title: "Fine-tune & export", desc: "Split, trim, music, text — every format" },
];

export default function Home() {
  const router = useRouter();
  const [projects, setProjects] = useState<Project[]>([]);
  const [loading, setLoading] = useState(true);
  const [showCreate, setShowCreate] = useState(false);
  const [title, setTitle] = useState("");
  const [formats, setFormats] = useState<string[]>(["youtube", "shorts"]);
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");
  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [styleId, setStyleId] = useState("");

  async function refresh() {
    try {
      setProjects(await listProjects());
    } catch (e: any) {
      if (/401|token/i.test(String(e.message))) router.push("/login");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    refresh();
    listStyles().then((all) => setStyles(all.filter((s) => s.status === "ready"))).catch(() => {});
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setCreating(true);
    try {
      const p = await createProject(title, formats, styleId || undefined);
      router.push(`/projects/${p.id}`);
    } catch (e: any) {
      if (/401|token/i.test(String(e.message))) router.push("/login");
      setError(e.message || "Failed to create project");
      setCreating(false);
    }
  }

  function toggleFormat(f: string) {
    setFormats((prev) => (prev.includes(f) ? prev.filter((x) => x !== f) : [...prev, f]));
  }

  async function handleDelete(id: string, e: React.MouseEvent) {
    e.preventDefault();
    e.stopPropagation();
    if (!confirm("Delete this project?")) return;
    await deleteProject(id);
    refresh();
  }

  const stats = [
    { label: "Projects", value: projects.length, icon: "📁", color: "from-accent/30 to-accent/5" },
    {
      label: "Completed",
      value: projects.filter((p) => p.status === "completed").length,
      icon: "✅",
      color: "from-accent2/30 to-accent2/5",
    },
    {
      label: "In progress",
      value: projects.filter((p) => ["processing", "uploading"].includes(p.status)).length,
      icon: "⚙️",
      color: "from-accent3/30 to-accent3/5",
    },
    {
      label: "Formats queued",
      value: projects.reduce((n, p) => n + (p.output_formats?.length || 0), 0),
      icon: "🎬",
      color: "from-accent4/30 to-accent4/5",
    },
  ];

  return (
    <main className="relative min-h-screen">
      <AnimatedBackground />
      <AppHeader />

      <div className="mx-auto max-w-6xl px-6 py-10">
        {/* Hero */}
        <section className="glass relative mb-8 animate-fade-up overflow-hidden rounded-3xl p-8">
          <div className="relative z-10 max-w-xl">
            <p className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-accent">Your studio</p>
            <h2 className="text-3xl font-extrabold leading-tight tracking-tight sm:text-4xl">
              What are we <span className="text-gradient">creating</span> today?
            </h2>
            <p className="mt-2 text-sm text-slate-400">
              Start a project, drop in your clips, and let the pipeline build a first cut you can polish.
            </p>
            <button onClick={() => setShowCreate((v) => !v)} className="btn-primary mt-6">
              {showCreate ? "✕ Close" : "＋ New project"}
            </button>
          </div>
          <div className="pointer-events-none absolute -right-10 top-1/2 hidden -translate-y-1/2 md:block">
            <div className="animate-float">
              <SpinningDisc size={260} />
            </div>
          </div>
        </section>

        {/* Create form */}
        {showCreate && (
          <form onSubmit={handleCreate} className="glass mb-8 animate-scale-in rounded-3xl p-6">
            <div className="grid gap-6 md:grid-cols-[1fr_auto]">
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Project title</label>
                <input
                  required
                  autoFocus
                  value={title}
                  onChange={(e) => setTitle(e.target.value)}
                  className="input-field"
                  placeholder="My summer trip highlights"
                />
                <label className="mb-2 mt-5 block text-xs font-medium text-slate-400">
                  Where will it be posted?
                </label>
                <div className="flex flex-wrap gap-2">
                  {ALL_FORMATS.map((f) => {
                    const on = formats.includes(f);
                    return (
                      <button
                        type="button"
                        key={f}
                        onClick={() => toggleFormat(f)}
                        className={`flex items-center gap-2 rounded-xl border px-3 py-2 text-xs font-medium transition-all duration-200 ${
                          on
                            ? "scale-[1.02] border-accent bg-accent/20 text-white shadow-[0_0_20px_-6px_rgba(124,131,245,0.7)]"
                            : "border-white/10 bg-white/5 text-slate-400 hover:border-white/20 hover:text-slate-200"
                        }`}
                      >
                        <span>{FORMAT_ICON[f]}</span>
                        {FORMAT_META[f].label}
                        <span className="text-slate-500">{FORMAT_META[f].ratio}</span>
                      </button>
                    );
                  })}
                </div>
                {styles.length > 0 && (
                  <>
                    <label className="mb-1.5 mt-5 block text-xs font-medium text-slate-400">
                      Edit in the style of (optional)
                    </label>
                    <select value={styleId} onChange={(e) => setStyleId(e.target.value)} className="input-field !w-auto">
                      <option value="">Default AI style</option>
                      {styles.map((s) => (
                        <option key={s.id} value={s.id}>🎓 {s.name}</option>
                      ))}
                    </select>
                  </>
                )}
              </div>
              <div className="flex items-end">
                <button
                  type="submit"
                  disabled={creating || formats.length === 0 || !title.trim()}
                  className="btn-primary w-full whitespace-nowrap md:w-auto"
                >
                  {creating ? "Creating…" : "Create & upload clips →"}
                </button>
              </div>
            </div>
            {error && <p className="mt-3 text-xs text-red-400">{error}</p>}
          </form>
        )}

        {/* Stats */}
        <section className="mb-10 grid grid-cols-2 gap-4 lg:grid-cols-4">
          {stats.map((s, i) => (
            <div
              key={s.label}
              className={`glass relative animate-fade-up overflow-hidden rounded-2xl p-5`}
              style={{ animationDelay: `${100 + i * 70}ms` }}
            >
              <div className={`absolute inset-0 bg-gradient-to-br ${s.color} opacity-60`} />
              <div className="relative">
                <div className="text-xl">{s.icon}</div>
                <p className="mt-2 text-3xl font-extrabold">{loading ? "–" : s.value}</p>
                <p className="text-xs text-slate-400">{s.label}</p>
              </div>
            </div>
          ))}
        </section>

        {/* Projects */}
        <div className="mb-4 flex items-end justify-between">
          <div>
            <h3 className="text-lg font-bold">Recent projects</h3>
            <p className="text-xs text-slate-500">Click a project to upload, watch the AI work, or edit.</p>
          </div>
        </div>

        {loading ? (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <div
                key={i}
                className="h-56 animate-shimmer rounded-2xl border border-white/5 bg-[linear-gradient(90deg,rgba(255,255,255,0.03),rgba(255,255,255,0.08),rgba(255,255,255,0.03))] bg-[length:200%_100%]"
              />
            ))}
          </div>
        ) : projects.length === 0 ? (
          <EmptyState onCreate={() => setShowCreate(true)} />
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {projects.map((p, i) => {
              const st = STATUS_STYLE[p.status] || STATUS_STYLE.created;
              return (
                <Link
                  key={p.id}
                  href={`/projects/${p.id}`}
                  className="glass card-hover group animate-fade-up overflow-hidden rounded-2xl"
                  style={{ animationDelay: `${150 + i * 60}ms` }}
                >
                  <div className={`relative h-28 overflow-hidden bg-gradient-to-br ${st.grad}`}>
                    <FilmStrip />
                    <div className="absolute inset-0 flex items-center justify-center">
                      <div className="flex h-12 w-12 items-center justify-center rounded-full bg-black/40 text-lg backdrop-blur transition-transform duration-300 group-hover:scale-110">
                        {p.status === "completed" ? "▶" : p.status === "processing" ? "⚙️" : "🎬"}
                      </div>
                    </div>
                    <button
                      onClick={(e) => handleDelete(p.id, e)}
                      className="absolute right-2 top-2 flex h-7 w-7 items-center justify-center rounded-full bg-black/40 text-xs text-slate-300 opacity-0 backdrop-blur transition hover:bg-red-500/80 hover:text-white group-hover:opacity-100"
                      title="Delete"
                    >
                      ✕
                    </button>
                  </div>
                  <div className="p-4">
                    <div className="flex items-start justify-between gap-2">
                      <h4 className="truncate font-semibold transition-colors group-hover:text-accent">{p.title}</h4>
                      <span
                        className={`flex shrink-0 items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[11px] font-medium capitalize ${st.pill}`}
                      >
                        <span className={`h-1.5 w-1.5 rounded-full ${st.dot}`} />
                        {p.status}
                      </span>
                    </div>
                    <div className="mt-3 flex flex-wrap gap-1.5">
                      {(p.output_formats || []).map((f) => (
                        <span key={f} className="rounded-md bg-white/5 px-2 py-0.5 text-[10px] text-slate-400">
                          {FORMAT_ICON[f]} {FORMAT_META[f]?.label || f}
                        </span>
                      ))}
                    </div>
                    <p className="mt-3 text-[11px] text-slate-500">
                      {new Date(p.created_at).toLocaleDateString(undefined, {
                        month: "short",
                        day: "numeric",
                        year: "numeric",
                      })}
                    </p>
                  </div>
                </Link>
              );
            })}
          </div>
        )}

        {/* How it works */}
        <section className="mt-14">
          <h3 className="mb-4 text-lg font-bold">How it works</h3>
          <div className="grid grid-cols-2 gap-4 lg:grid-cols-4">
            {STEPS.map((s, i) => (
              <div key={s.title} className="glass relative rounded-2xl p-5">
                <span className="absolute right-4 top-3 text-4xl font-black text-white/5">{i + 1}</span>
                <div className="text-2xl">{s.icon}</div>
                <p className="mt-2 text-sm font-semibold">{s.title}</p>
                <p className="text-xs text-slate-400">{s.desc}</p>
              </div>
            ))}
          </div>
        </section>
      </div>
    </main>
  );
}

function FilmStrip() {
  const holes = Array.from({ length: 24 });
  return (
    <div className="absolute inset-x-0 top-0 overflow-hidden opacity-40">
      <div className="flex w-[200%] animate-film-scroll gap-3 px-2 py-1.5">
        {holes.map((_, i) => (
          <span key={i} className="h-2 w-3 shrink-0 rounded-sm bg-black/60" />
        ))}
      </div>
    </div>
  );
}

function EmptyState({ onCreate }: { onCreate: () => void }) {
  return (
    <div className="glass flex animate-fade-up flex-col items-center rounded-3xl px-6 py-16 text-center">
      <div className="animate-float">
        <SpinningDisc size={120} />
      </div>
      <p className="mt-8 text-lg font-bold">Your studio is empty</p>
      <p className="mt-1 max-w-sm text-sm text-slate-400">
        Create your first project and upload a few clips — the AI will build a first cut for you.
      </p>
      <button onClick={onCreate} className="btn-primary mt-6">
        ＋ Create first project
      </button>
    </div>
  );
}
