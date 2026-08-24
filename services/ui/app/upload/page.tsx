import { UploadForm } from "@/features/upload/UploadForm";

export default function UploadPage() {
  return (
    <main className="page">
      <h1>Upload a video</h1>
      <p className="muted">
        Videos are stored in S3 and processed asynchronously: transcription,
        visual analysis, chunking, and embedding.
      </p>
      <UploadForm />
    </main>
  );
}
