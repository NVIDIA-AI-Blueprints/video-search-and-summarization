import { render, screen, fireEvent } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { Citation } from "@/components/chat/Citation";
import { StatusBadge } from "@/components/StatusBadge";
import { TranscriptViewer } from "@/components/transcript/TranscriptViewer";

describe("Citation", () => {
  it("renders the timecode and handles click", () => {
    const onClick = vi.fn();
    render(
      <Citation
        citation={{ video_id: "v1", start_ms: 65_000, end_ms: 70_000, quote: "hello" }}
        onClick={onClick}
      />,
    );
    expect(screen.getByRole("button")).toHaveTextContent("00:01:05");
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledOnce();
  });
});

describe("StatusBadge", () => {
  it("maps statuses to friendly labels", () => {
    render(<StatusBadge status="TRANSCRIBING" />);
    expect(screen.getByText("Transcribing")).toBeInTheDocument();
  });

  it("labels failures", () => {
    render(<StatusBadge status="FAILED" />);
    expect(screen.getByText("Failed")).toBeInTheDocument();
  });
});

describe("TranscriptViewer", () => {
  const segments = [
    { start_ms: 0, end_ms: 4000, text: "first line" },
    { start_ms: 5000, end_ms: 9000, text: "second line" },
  ];

  it("renders transcript rows and seeks on click", () => {
    const onSeek = vi.fn();
    render(<TranscriptViewer segments={segments} activeIndex={-1} onSeek={onSeek} />);

    expect(screen.getByText("first line")).toBeInTheDocument();
    fireEvent.click(screen.getByText("second line"));
    expect(onSeek).toHaveBeenCalledWith(5000);
  });

  it("shows empty state without segments", () => {
    render(<TranscriptViewer segments={[]} activeIndex={-1} />);
    expect(screen.getByText(/Transcript will appear/i)).toBeInTheDocument();
  });
});
