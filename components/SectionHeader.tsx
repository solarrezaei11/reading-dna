export default function SectionHeader({ num, title }: { num: string; title: string }) {
  return (
    <div className="flex items-center gap-3">
      <span
        className="text-[10px] tracking-[0.22em] uppercase font-medium shrink-0"
        style={{ color: "var(--sage-dark)", fontFamily: "var(--font-geist-mono)" }}
      >
        {num} — {title}
      </span>
      <div className="flex-1 h-px" style={{ background: "var(--border-mid)" }} />
    </div>
  );
}
