import type { Metadata } from "next";
import "./globals.css";

export const metadata: Metadata = {
  title: "Archive Video Analysis",
  description: "Search and summarize archived videos with citations",
};

export default function RootLayout({ children }: { children: React.ReactNode }) {
  return (
    <html lang="en">
      <body>
        <header className="topbar">
          <a href="/" className="brand">AVA</a>
          <nav className="topnav">
            <a href="/library">Library</a>
            <a href="/upload">Upload</a>
          </nav>
        </header>
        {children}
      </body>
    </html>
  );
}
