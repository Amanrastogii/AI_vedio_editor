"use client";

import { useEffect, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AnimatedBackground from "@/components/ui/AnimatedBackground";
import AppHeader from "@/components/ui/AppHeader";
import { STATUS_PILL } from "@/components/styles/StyleViz";
import { StyleProfile, createStyle, getToken, listStyles } from "@/lib/api";

export default function StylesPage() {
  const router = useRouter();
  const [styles, setStyles] = useState<StyleProfile[]>([]);
  const [loading, setLoading] = useState(true);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [error, setError] = useState("");

  useEffect(() => {
    if (!getToken()) {
      router.push("/login");
      return;
    }
    listStyles()
      .then(setStyles)
      .catch((e) => (/401|token/i.test(String(e.message)) ? router.push("/login") : setError(e.message)))
      .finally(() => setLoading(false));
  }, [router]);

  async function handleCreate(e: React.FormEvent) {
    e.preventDefault();
    setCreating(true);
    setError("");
    try {
      const p = await createStyle(name.trim(), description.trim() || undefined);
      router.push(`/styles/${p.id}`);
    } catch (err: any) {
      setError(err.message || "Failed to create");
      setCreating(false);
    }
  }

  return (
    <main className="relative min-h-screen">
      <AnimatedBackground />
      <AppHeader title="My editing styles" subtitle="teach the AI how you edit" backHref="/" />
      <div className="mx-auto max-w-6xl px-6 py-10">
        <section className="glass mb-8 animate-fade-up rounded-3xl p-8">
          <p className="mb-2 text-xs font-semibold uppercase tracking-[0.2em] text-accent">Style training</p>
          <h2 className="text-3xl font-extrabold tracking-tight">
            Teach the AI to edit <span className="text-gradient">like you</span>
          </h2>
          <p className="mt-2 max-w-2xl text-sm text-slate-400">
            Give it videos you already edited <b className="text-slate-200">plus the raw clips</b> they were cut from. It lines up every
            frame of your edit with the raw footage to learn what you keep, what you cut, how you trim, your pacing, transitions,
            color grade and sound. New footage then gets cut the same way.
          </p>
          <div className="mt-6 grid gap-3 sm:grid-cols-3">
            {[
              ["1", "Upload examples", "A finished edit + its raw clips. More examples → a sharper style."],
              ["2", "Train", "Runs on your machine in about a minute per example. No cloud, no GPU needed."],
              ["3", "Use it", "Pick the style when you create a project, or hit “Apply style” in the editor."],
            ].map(([n, t, d]) => (
              <div key={n} className="rounded-2xl border border-white/10 bg-white/[0.03] p-4">
                <p className="text-xs font-bold text-accent">STEP {n}</p>
                <p className="mt-1 text-sm font-semibold">{t}</p>
                <p className="text-xs text-slate-400">{d}</p>
              </div>
            ))}
          </div>
        </section>

        <form onSubmit={handleCreate} className="glass mb-8 grid gap-3 rounded-3xl p-6 md:grid-cols-[1fr_1.5fr_auto]">
          <input required value={name} onChange={(e) => setName(e.target.value)} className="input-field"
            placeholder="Style name — e.g. “My travel vlogs”" maxLength={255} />
          <input value={description} onChange={(e) => setDescription(e.target.value)} className="input-field"
            placeholder="Notes (optional)" maxLength={2000} />
          <button disabled={creating || !name.trim()} className="btn-primary whitespace-nowrap">
            {creating ? "Creating…" : "＋ New style"}
          </button>
          {error && <p className="text-xs text-red-400 md:col-span-3">{error}</p>}
        </form>

        {loading ? (
          <p className="text-sm text-slate-400">Loading…</p>
        ) : styles.length === 0 ? (
          <div className="glass rounded-3xl py-14 text-center text-sm text-slate-400">No styles yet — create your first one above.</div>
        ) : (
          <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
            {styles.map((s) => (
              <Link key={s.id} href={`/styles/${s.id}`} className="glass card-hover rounded-2xl p-5">
                <div className="flex items-start justify-between gap-2">
                  <h3 className="truncate font-semibold">🎓 {s.name}</h3>
                  <span className={`shrink-0 rounded-full px-2.5 py-0.5 text-[11px] font-medium capitalize ${STATUS_PILL[s.status]}`}>
                    {s.status}
                  </span>
                </div>
                <p className="mt-1 text-xs text-slate-500">{s.example_count} example(s)</p>
                <p className="mt-3 line-clamp-3 text-xs text-slate-400">
                  {s.summary?.split("\n")[0] || s.description || "Add an example (finished edit + raw clips) and train."}
                </p>
              </Link>
            ))}
          </div>
        )}
      </div>
    </main>
  );
}
