import { MetadataRoute } from "next";

export default function robots(): MetadataRoute.Robots {
  return {
    rules: { userAgent: "*", allow: "/", disallow: "/analyze" },
    sitemap: "https://readingdna.vercel.app/sitemap.xml",
  };
}
