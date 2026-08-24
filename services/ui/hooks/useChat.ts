"use client";

import { useCallback, useState } from "react";
import { api } from "@/lib/api";
import type { Citation } from "@/lib/types";

interface ChatMessage {
  role: "user" | "assistant";
  text: string;
  citations?: Citation[];
}

export function useChat(videoId: string) {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [busy, setBusy] = useState(false);

  const send = useCallback(
    async (question: string) => {
      if (!question.trim() || busy) return;
      setMessages((prev) => [...prev, { role: "user", text: question }]);
      setBusy(true);
      try {
        const response = await api.chatWithVideo(videoId, question);
        setMessages((prev) => [
          ...prev,
          { role: "assistant", text: response.answer, citations: response.citations },
        ]);
      } catch (err) {
        setMessages((prev) => [
          ...prev,
          {
            role: "assistant",
            text: err instanceof Error ? err.message : "Something went wrong.",
          },
        ]);
      } finally {
        setBusy(false);
      }
    },
    [busy, videoId],
  );

  return { messages, send, busy };
}
