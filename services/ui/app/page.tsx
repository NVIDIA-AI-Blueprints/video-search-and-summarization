import Link from "next/link";

export default function Home() {
  return (
    <main className="page">
      <h1>Archive Video Search &amp; Summarization</h1>
      <p>Upload videos, get transcripts and visual analysis, ask questions with citations.</p>
      <div className="cta-row">
        <Link className="btn" href="/library">Go to library</Link>
        <Link className="btn secondary" href="/upload">Upload a video</Link>
      </div>
    </main>
  );
}
