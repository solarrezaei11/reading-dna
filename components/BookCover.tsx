"use client";

import Image from "next/image";
import { useEffect, useState } from "react";

async function fetchCoverByTitle(title: string, author?: string): Promise<string | null> {
  try {
    const params = new URLSearchParams({ title, limit: "1", fields: "cover_i" });
    if (author) params.set("author", author);
    const res = await fetch(`https://openlibrary.org/search.json?${params}`);
    if (!res.ok) return null;
    const data: unknown = await res.json();
    const id = typeof data === "object" && data !== null &&
      Array.isArray((data as { docs?: unknown }).docs) &&
      typeof (data as { docs: Array<{ cover_i?: unknown }> }).docs[0]?.cover_i === "number"
      ? (data as { docs: Array<{ cover_i: number }> }).docs[0].cover_i
      : undefined;
    return id ? `https://covers.openlibrary.org/b/id/${id}-M.jpg` : null;
  } catch {
    return null;
  }
}

export function BookCover({
  isbn,
  title,
  author,
  size = 56,
}: {
  isbn?: string;
  title: string;
  author?: string;
  size?: number;
}) {
  const identity = JSON.stringify([isbn ?? "", title, author ?? ""]);
  const [fallback, setFallback] = useState<{ identity: string; src: string | null } | null>(null);
  const isbnCover = isbn ? `https://covers.openlibrary.org/b/isbn/${encodeURIComponent(isbn)}-M.jpg?default=false` : null;
  const src = fallback?.identity === identity ? fallback.src : isbnCover;

  useEffect(() => {
    let active = true;
    if (!isbn && title) {
      fetchCoverByTitle(title, author).then((url) => {
        if (active) setFallback({ identity, src: url });
      });
    }
    return () => { active = false; };
  }, [author, identity, isbn, title]);

  const handleError = () => {
    if (isbn && src?.includes("/isbn/")) {
      void fetchCoverByTitle(title, author).then((url) => setFallback({ identity, src: url }));
      return;
    }
    setFallback({ identity, src: null });
  };

  if (!src) return null;

  return (
    <Image
      src={src}
      alt={`Cover of ${title}`}
      width={size}
      height={Math.round(size * 1.5)}
      onError={handleError}
      className="rounded object-cover shrink-0"
      style={{ width: size, height: size * 1.5, boxShadow: "0 2px 8px rgba(0,0,0,0.12)" }}
    />
  );
}
