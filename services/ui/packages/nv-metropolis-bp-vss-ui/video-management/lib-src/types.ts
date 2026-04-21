// SPDX-License-Identifier: MIT
export interface StreamMetadata {
  bitrate: string;
  codec: string;
  framerate: string;
  govlength: string;
  resolution: string;
}

export interface StreamInfo {
  isMain: boolean;
  metadata: StreamMetadata;
  name: string;
  streamId: string;
  url: string;
  vodUrl: string;
  sensorId: string;
}

export type StreamsApiResponse = Array<Record<string, Omit<StreamInfo, 'sensorId'>[]>>;

export interface TimelineInfo {
  endTime: string;
  sizeInMegabytes: number;
  startTime: string;
}

export interface StreamStorageInfo {
  sizeInMegabytes: number;
  state: string;
  timelines: TimelineInfo[];
}

export interface TotalStorageInfo {
  remainingStorageDays: number;
  sizeInMegabytes: number;
  totalAvailableStorageSize: number;
  totalDiskCapacity: number;
}

export interface StorageSizeResponse {
  [streamId: string]: StreamStorageInfo | TotalStorageInfo;
  total: TotalStorageInfo;
}

export interface FileUploadResponse {
  bytes: number;
  chunkCount: string;
  chunkIdentifier: string;
  created_at: string;
  filePath: string;
  filename: string;
  id: string;
  sensorId: string;
}

export interface FileUploadError {
  error_code: string;
  error_message: string;
}

export interface UploadProgress {
  id: string;
  fileName: string;
  progress: number;
  status: 'pending' | 'uploading' | 'success' | 'error' | 'cancelled';
  error?: string;
}

export interface VideoManagementSidebarControlHandlers {
  controlsComponent: React.ReactNode;
}

export interface VideoManagementData {
  systemStatus: string;
  vstApiUrl?: string | null;
  agentApiUrl?: string | null;
  chatUploadFileConfigTemplateJson?: string | null;
  enableAddRtspButton?: boolean;
  enableVideoUpload?: boolean;
}

export interface VideoManagementComponentProps {
  theme?: 'light' | 'dark';
  onThemeChange?: (theme: 'light' | 'dark') => void;
  isActive?: boolean;
  serverRenderTime?: string;
  videoManagementData?: VideoManagementData;
  renderControlsInLeftSidebar?: boolean;
  onControlsReady?: (handlers: VideoManagementSidebarControlHandlers) => void;
}

