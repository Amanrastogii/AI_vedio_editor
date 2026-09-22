"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import BackendStatus from "@/components/BackendStatus";
import SpinningDisc from "@/components/ui/SpinningDisc";
import { clearToken } from "@/lib/api";

interface Props {
  title?: string;
  subtitle?: string;
  backHref?: string;
}

export default function AppHeader({ title, subtitle, backHref }: Props) {
  const router = useRouter();

  function logout() {
    clearToken();
    router.push("/login");
  }

  return (
    <header className="sticky top-0 z-20 border-b border-white/5 bg-bg/60 backdrop-blur-xl">
      <div className="mx-auto flex max-w-6xl items-center justify-between gap-4 px-6 py-3">
        <div className="flex min-w-0 items-center gap-3">
          {backHref && (
            <Link href={backHref} className="btn-ghost !px-2.5" aria-label="Back">
              ←
            </Link>
          )}
          <Link href="/" className="flex items-center gap-3">
            <SpinningDisc size={36} glow={false} />
            {!title && (
              <div>
                <h1 className="text-base font-bold leading-tight">
                  AI <span className="text-gradient">Video Editor</span>
                </h1>
                <p className="text-[11px] text-slate-400">11-agent autonomous pipeline</p>
              </div>
            )}
          </Link>
          {title && (
            <div className="min-w-0">
              <h1 className="truncate text-base font-bold leading-tight">{title}</h1>
              {subtitle && <p className="text-[11px] capitalize text-slate-400">{subtitle}</p>}
            </div>
          )}
        </div>
        <div className="flex items-center gap-3">
          <div className="hidden md:block">
            <BackendStatus />
          </div>
          <Link href="/styles" className="btn-ghost" title="Teach the AI your editing style">
            🎓 <span className="hidden sm:inline">My styles</span>
          </Link>
          <button onClick={logout} className="btn-ghost">
            Logout
          </button>
        </div>
      </div>
    </header>
  );
}
