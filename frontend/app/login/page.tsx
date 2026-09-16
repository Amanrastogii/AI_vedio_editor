"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import AnimatedBackground from "@/components/ui/AnimatedBackground";
import SpinningDisc from "@/components/ui/SpinningDisc";
import { login, register, setToken } from "@/lib/api";

const FEATURES = [
  { icon: "🎞", title: "Smart scene detection", desc: "Finds every shot and scores its quality" },
  { icon: "✂️", title: "Auto-edit timeline", desc: "Builds a story from your best moments" },
  { icon: "💬", title: "Edit by chatting", desc: "“Remove clip 2” — and it's done" },
  { icon: "📱", title: "Every format at once", desc: "YouTube, Shorts, Reels, TikTok, LinkedIn" },
];

export default function LoginPage() {
  const router = useRouter();
  const [mode, setMode] = useState<"login" | "register">("login");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [fullName, setFullName] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [welcome, setWelcome] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setError("");
    setLoading(true);
    try {
      const res =
        mode === "login" ? await login(email, password) : await register(email, password, fullName);
      setToken(res.access_token);
      setWelcome(true);
      setTimeout(() => router.push("/"), 1600);
    } catch (err: any) {
      setError(err.message || "Authentication failed");
      setLoading(false);
    }
  }

  if (welcome) {
    return <WelcomeSplash name={fullName || email.split("@")[0]} isNew={mode === "register"} />;
  }

  return (
    <main className="relative flex min-h-screen items-center justify-center px-4 py-10">
      <AnimatedBackground />

      <div className="grid w-full max-w-5xl grid-cols-1 items-center gap-12 lg:grid-cols-2">
        {/* Hero */}
        <div className="hidden flex-col items-center text-center lg:flex lg:items-start lg:text-left">
          <div className="mb-10 animate-float">
            <SpinningDisc size={240} />
          </div>
          <h1 className="animate-fade-up text-4xl font-extrabold leading-tight tracking-tight">
            Raw clips in.
            <br />
            <span className="text-gradient">Finished videos out.</span>
          </h1>
          <p className="mt-3 max-w-md animate-fade-up text-sm text-slate-400" style={{ animationDelay: "100ms" }}>
            An 11-agent pipeline that watches your footage, picks the best moments, and cuts it for every platform.
          </p>

          <div className="mt-8 grid w-full max-w-md grid-cols-2 gap-3">
            {FEATURES.map((f, i) => (
              <div
                key={f.title}
                className="glass animate-fade-up rounded-xl p-3 text-left"
                style={{ animationDelay: `${200 + i * 90}ms` }}
              >
                <div className="text-lg">{f.icon}</div>
                <p className="mt-1 text-xs font-semibold">{f.title}</p>
                <p className="text-[11px] text-slate-400">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>

        {/* Form */}
        <div className="mx-auto w-full max-w-sm animate-scale-in">
          <div className="mb-6 flex flex-col items-center text-center lg:hidden">
            <SpinningDisc size={110} />
            <h1 className="mt-5 text-2xl font-extrabold">
              AI <span className="text-gradient">Video Editor</span>
            </h1>
          </div>

          <div className="glass rounded-3xl p-7">
            {/* Mode switch */}
            <div className="relative mb-6 grid grid-cols-2 rounded-xl bg-black/30 p-1 text-sm">
              <div
                className="absolute bottom-1 left-1 top-1 w-[calc(50%-4px)] rounded-lg bg-gradient-to-r from-accent to-accent4 transition-transform duration-300"
                style={{ transform: mode === "login" ? "translateX(0)" : "translateX(100%)" }}
              />
              {(["login", "register"] as const).map((m) => (
                <button
                  key={m}
                  type="button"
                  onClick={() => {
                    setMode(m);
                    setError("");
                  }}
                  className={`relative z-10 rounded-lg py-2 font-semibold transition-colors ${
                    mode === m ? "text-white" : "text-slate-400 hover:text-white"
                  }`}
                >
                  {m === "login" ? "Sign in" : "Sign up"}
                </button>
              ))}
            </div>

            <h2 className="text-xl font-bold">{mode === "login" ? "Welcome back 👋" : "Create your studio ✨"}</h2>
            <p className="mb-5 text-sm text-slate-400">
              {mode === "login" ? "Pick up where you left off." : "It takes less than a minute."}
            </p>

            <form onSubmit={submit} className="space-y-4">
              {mode === "register" && (
                <div className="animate-fade-up">
                  <label className="mb-1.5 block text-xs font-medium text-slate-400">Full name</label>
                  <input
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                    className="input-field"
                    placeholder="Jane Doe"
                  />
                </div>
              )}
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">Email</label>
                <input
                  type="email"
                  required
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  className="input-field"
                  placeholder="you@example.com"
                />
              </div>
              <div>
                <label className="mb-1.5 block text-xs font-medium text-slate-400">
                  Password <span className="text-slate-500">(min 8 chars)</span>
                </label>
                <input
                  type="password"
                  required
                  minLength={8}
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="input-field"
                  placeholder="••••••••"
                />
              </div>

              {error && (
                <div className="animate-fade-in rounded-xl border border-red-500/30 bg-red-500/10 px-3 py-2 text-xs text-red-300">
                  {error}
                </div>
              )}

              <button type="submit" disabled={loading} className="btn-primary w-full py-3">
                {loading ? (
                  <>
                    <span className="h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
                    Please wait…
                  </>
                ) : mode === "login" ? (
                  "Sign in →"
                ) : (
                  "Create account →"
                )}
              </button>
            </form>
          </div>
        </div>
      </div>
    </main>
  );
}

function WelcomeSplash({ name, isNew }: { name: string; isNew: boolean }) {
  return (
    <main className="relative flex min-h-screen flex-col items-center justify-center px-4 text-center">
      <AnimatedBackground />
      <div className="animate-scale-in">
        <SpinningDisc size={180} fast />
      </div>
      <h1 className="mt-10 animate-fade-up text-3xl font-extrabold" style={{ animationDelay: "200ms" }}>
        {isNew ? "Welcome" : "Welcome back"}, <span className="text-gradient">{name}</span>
      </h1>
      <p className="mt-2 animate-fade-up text-sm text-slate-400" style={{ animationDelay: "350ms" }}>
        Warming up your studio…
      </p>
      <div className="mt-6 flex h-8 items-end gap-1 animate-fade-in" style={{ animationDelay: "450ms" }}>
        {Array.from({ length: 12 }).map((_, i) => (
          <span
            key={i}
            className="w-1.5 origin-bottom animate-equalizer rounded-full bg-gradient-to-t from-accent to-accent4"
            style={{ height: "100%", animationDelay: `${i * 80}ms` }}
          />
        ))}
      </div>
    </main>
  );
}
