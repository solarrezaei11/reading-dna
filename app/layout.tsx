import type { Metadata } from "next";
import { Geist, Geist_Mono, DM_Serif_Display } from "next/font/google";
import "./globals.css";

const geistSans = Geist({ variable: "--font-geist-sans", subsets: ["latin"] });
const geistMono = Geist_Mono({ variable: "--font-geist-mono", subsets: ["latin"] });
const dmSerif = DM_Serif_Display({ weight: "400", variable: "--font-dm-serif", subsets: ["latin"] });

export const metadata: Metadata = {
  title: "ReadingDNA — Which AI knows you best as a reader?",
  description: "Import your Goodreads history. Two AI models compete to recommend books you'll love — and you see who knows your taste better. Get your Reading DNA profile.",
  keywords: ["goodreads", "book recommendations", "AI books", "reading profile", "book taste", "reading DNA", "book finder"],
  openGraph: {
    title: "ReadingDNA — Which AI knows you best as a reader?",
    description: "Import your Goodreads history. Two AI models compete to recommend books you'll love — see who wins.",
    type: "website",
    siteName: "ReadingDNA",
  },
  twitter: {
    card: "summary_large_image",
    title: "ReadingDNA — Which AI knows you best as a reader?",
    description: "Import your Goodreads history. Two AI models compete to recommend books you'll love — see who wins.",
  },
  robots: {
    index: true,
    follow: true,
    googleBot: { index: true, follow: true },
  },
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en" className={`${geistSans.variable} ${geistMono.variable} ${dmSerif.variable} h-full antialiased`}>
      <body className="min-h-full flex flex-col" style={{ background: "var(--bg)", color: "var(--text-1)" }}>
        {children}
      </body>
    </html>
  );
}
