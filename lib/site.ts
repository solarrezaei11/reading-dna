const fallback = "http://localhost:3000";

export function siteUrl(): URL {
  try {
    return new URL(process.env.NEXT_PUBLIC_SITE_URL ?? fallback);
  } catch {
    return new URL(fallback);
  }
}
