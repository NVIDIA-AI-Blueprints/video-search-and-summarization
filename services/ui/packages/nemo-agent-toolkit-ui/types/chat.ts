export interface QueryDataContext {
  id: string;
  label: string;
  /**
   * UI-only chip / grouping (e.g. tooltips). Not used by the backend — omitted from Chat `onSend`
   * `[Context:…]` payload, which forwards only `data` fields.
   *
   * Possible types for futuristic use could be:
   * - media/video
   * - media/image
   * - network-file
   */
  contextType: string;
  data: Record<string, unknown>;
}

/** Parent-provided renderable HTML snippet shown under an assistant response card. */
export type CallerInfo = string;

export interface Message {
  id?: string;
  role: Role;
  content: string;
  intermediateSteps?: any;
  humanInteractionMessages?: any;
  errorMessages?: any;
  timestamp?: number;
  parentId?: string;
  /** Parent app supplied metadata to render caller-info section on assistant responses. */
  callerInfo?: CallerInfo;
  hidden?: boolean; // If true, message will not be displayed in chat UI but will still be sent to API
}

export type Role = 'assistant' | 'user' | 'agent' | 'system';

// Dynamic custom agent params - can contain any key-value pairs
export type CustomAgentParams = Record<string, string | number | boolean>;

export interface ChatBody {
  chatCompletionURL?: string;
  messages?: Message[];
  additionalProps?: any;
  // Allow dynamic custom params at top level
  [key: string]: string | number | boolean | Message[] | any | undefined;
}

export interface Conversation {
  id: string;
  name: string;
  messages: Message[];
  folderId: string | null;
  isHomepageConversation?: boolean; // Flag to track homepage conversations before first message
  /** True while this conversation has an in-flight agent query (e.g. background processing). */
  isQueryInFlight?: boolean;
}

// WebSocket Message Types
export interface WebSocketMessageBase {
  id?: string;
  conversation_id?: string;
  parent_id?: string;
  timestamp?: string;
  status?: string;
}

export interface SystemResponseMessage extends WebSocketMessageBase {
  type: 'system_response_message';
  status: 'in_progress' | 'complete';
  content?: {
    text?: string;
  };
}

export interface SystemIntermediateMessage extends WebSocketMessageBase {
  type: 'system_intermediate_message';
  status?: string;
  content?: any;
  index?: number;
}

export interface SystemInteractionMessage extends WebSocketMessageBase {
  type: 'system_interaction_message';
  content?: {
    input_type?: string;
    oauth_url?: string;
    redirect_url?: string;
    text?: string;
  };
}

export interface ErrorMessage extends WebSocketMessageBase {
  type: 'error';
  content?: any;
}

export type WebSocketMessage =
  | SystemResponseMessage
  | SystemIntermediateMessage
  | SystemInteractionMessage
  | ErrorMessage;
