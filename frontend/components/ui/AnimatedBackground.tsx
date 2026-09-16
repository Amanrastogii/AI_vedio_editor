export default function AnimatedBackground() {
  return (
    <div aria-hidden className="pointer-events-none fixed inset-0 -z-10 overflow-hidden bg-bg">
      <div className="grid-pattern absolute inset-0" />
      <div className="absolute -left-40 -top-40 h-[520px] w-[520px] animate-aurora rounded-full bg-accent/25 blur-[120px]" />
      <div
        className="absolute -right-32 top-1/3 h-[460px] w-[460px] animate-aurora rounded-full bg-accent4/20 blur-[120px]"
        style={{ animationDelay: "-6s" }}
      />
      <div
        className="absolute -bottom-40 left-1/3 h-[420px] w-[420px] animate-aurora rounded-full bg-accent2/15 blur-[120px]"
        style={{ animationDelay: "-12s" }}
      />
      <div className="absolute inset-0 bg-gradient-to-b from-transparent via-transparent to-bg/80" />
    </div>
  );
}
