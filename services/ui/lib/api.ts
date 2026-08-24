import type {
  ChatResponse,
  VideoCreated,
  VideoMetadata,
} from "./types";

const API_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

function authHeaders(): HeadersInit {
  if (typeof window === "undefined") return {};
  const token = window.localStorage.getItem("ava_id_token");
  return token ? { Authorization: `Bearer ${token}` } : {};
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_URL}${path}`, {
    ...init,
    headers: { "Content-Type": "application/json", ...authHeaders(), ...init?.headers },
  });
  if (!response.ok) {
    const detail = await response.text();
    throw new Error(`API ${response.status}: ${detail}`);
  }
  if (response.status === 204) return undefined as T;
  return response.json() as Promise<T>;
}

export const api = {
  createVideo: (filename: string, contentType: string) =>
    request<VideoCreated>(
      `/videos?filename=${encodeURIComponent(filename)}&content_type=${encodeURIComponent(contentType)}`,
      { method: "POST" },
    ),

  uploadFile: async (uploadUrl: string, file: File) => {
    const response = await fetch(uploadUrl, {
      method: "PUT",
      body: file,
      headers: { "Content-Type": file.type || "application/octet-stream" },
    });
    if (!response.ok) throw new Error(`Upload failed: ${response.status}`);
  },

  listVideos: () => request<{ videos: VideoMetadata[] }>("/videos"),

  getVideo: (videoId: string) => request<VideoMetadata>(`/videos/${videoId}`),

  deleteVideo: (videoId: string) => request<void>(`/videos/${videoId}`, { method: "DELETE" }),

  getStreamUrl: (videoId: string) =>
    request<{ url: string }>(`/videos/${videoId}/stream-url`),

  chat: (question: string, videoIds: string[]) =>
    request<ChatResponse>("/agent/chat", {
      method: "POST",
      body: JSON.stringify({ question, video_ids: videoIds }),
    }),

  chatWithVideo: (videoId: string, question: string) =>
    request<ChatResponse>(`/videos/${videoId}/chat`, {
      method: "POST",
      body: JSON.stringify({ question }),
    }),
};
