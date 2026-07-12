export default function Wordmark({ size = "text-4xl" }: { size?: string }) {
  return (
    <h1 className={`${size} tracking-tight leading-none`}>
      <span className="font-light" style={{ color: "var(--text-1)" }}>Reading</span>
      <span
        style={{
          fontFamily: "var(--font-dm-serif)",
          fontStyle: "italic",
          backgroundImage: "linear-gradient(105deg, var(--sage) 0%, var(--sage-dark) 45%, var(--teal) 100%)",
          WebkitBackgroundClip: "text",
          backgroundClip: "text",
          color: "transparent",
        }}
      >
        DNA
      </span>
    </h1>
  );
}
