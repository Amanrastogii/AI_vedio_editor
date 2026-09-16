interface Props {
  size?: number;
  fast?: boolean;
  glow?: boolean;
}

/** Animated CD / vinyl-style disc used as the app's brand mark. */
export default function SpinningDisc({ size = 220, fast = false, glow = true }: Props) {
  const label = size * 0.34;
  const hole = size * 0.07;

  return (
    <div className="relative" style={{ width: size, height: size }}>
      {glow && (
        <>
          <div className="absolute inset-0 animate-ring-pulse rounded-full border border-accent/40" />
          <div
            className="absolute inset-0 animate-ring-pulse rounded-full border border-accent4/30"
            style={{ animationDelay: "1.2s" }}
          />
          <div className="absolute inset-[-12%] rounded-full bg-gradient-to-br from-accent/30 to-accent4/30 blur-3xl" />
        </>
      )}

      <div
        className={`absolute inset-0 rounded-full ${fast ? "animate-spin-fast" : "animate-spin-slow"}`}
        style={{
          background:
            "conic-gradient(from 0deg, #1a1d2b, #7c83f5 12%, #56cfb2 22%, #1a1d2b 30%, #e96b8c 45%, #f5a623 55%, #1a1d2b 65%, #7c83f5 82%, #1a1d2b)",
          boxShadow: "0 20px 60px -10px rgba(0,0,0,0.8), inset 0 0 0 1px rgba(255,255,255,0.08)",
        }}
      >
        {/* grooves */}
        <div
          className="absolute inset-[4%] rounded-full opacity-70"
          style={{
            background:
              "repeating-radial-gradient(circle, rgba(0,0,0,0.35) 0px, rgba(0,0,0,0.35) 1px, transparent 2px, transparent 5px)",
          }}
        />
        {/* reflective sheen */}
        <div
          className="absolute inset-0 rounded-full"
          style={{
            background:
              "linear-gradient(135deg, rgba(255,255,255,0.35) 0%, transparent 30%, transparent 70%, rgba(255,255,255,0.12) 100%)",
          }}
        />
        {/* center label */}
        <div
          className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2 items-center justify-center rounded-full bg-gradient-to-br from-accent to-accent4"
          style={{ width: label, height: label, boxShadow: "inset 0 0 0 2px rgba(255,255,255,0.15)" }}
        >
          {size >= 80 && (
            <span
              className="absolute font-black tracking-widest text-white/90"
              style={{ fontSize: label * 0.14, top: label * 0.14 }}
            >
              AI
            </span>
          )}
          <div
            className="absolute rounded-full bg-bg"
            style={{ width: hole, height: hole, boxShadow: "inset 0 0 0 1px rgba(255,255,255,0.2)" }}
          />
        </div>
      </div>
    </div>
  );
}
