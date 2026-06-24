"use client";

import { useState, useRef, useEffect, useCallback } from "react";

async function fetchCoverByTitle(title: string, author?: string): Promise<string | null> {
  try {
    const params = new URLSearchParams({ title, limit: "1", fields: "cover_i" });
    if (author) params.set("author", author);
    const res = await fetch(`https://openlibrary.org/search.json?${params}`);
    const data = await res.json();
    const id = data.docs?.[0]?.cover_i;
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
  const [src, setSrc] = useState<string | null>(
    isbn ? `https://covers.openlibrary.org/b/isbn/${isbn}-M.jpg?default=false` : null
  );
  const searchedRef = useRef(false);

  // If no ISBN supplied, kick off a title+author search immediately
  useEffect(() => {
    if (!isbn && title && !searchedRef.current) {
      searchedRef.current = true;
      fetchCoverByTitle(title, author).then(url => url && setSrc(url));
    }
  }, [isbn, title, author]);

  const handleError = useCallback(() => {
    if (!searchedRef.current && title) {
      // ISBN was wrong — fall back to title search
      searchedRef.current = true;
      fetchCoverByTitle(title, author).then(url => setSrc(url));
    } else {
      setSrc(null);
    }
  }, [title, author]);

  if (!src) return null;

  return (
    <img
      src={src}
      alt={title}
      onError={handleError}
      className="rounded object-cover shrink-0"
      style={{ width: size, height: size * 1.5, boxShadow: "0 2px 8px rgba(0,0,0,0.12)" }}
    />
  );
}
