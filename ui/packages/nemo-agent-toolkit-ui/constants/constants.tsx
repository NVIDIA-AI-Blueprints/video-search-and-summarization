// =============================================================================
// Application Information
// =============================================================================

export const APPLICATION_NAME = 'NeMo Agent Toolkit';
export const APPLICATION_UI_NAME = 'NeMo Agent Toolkit UI';
export const botHeader = 'Scout Bot';

// =============================================================================
// Security & Session
// =============================================================================

export const SESSION_COOKIE_NAME = 'nemo-agent-toolkit-session';
export const MAX_FILE_SIZE_BYTES = 5242880; // 5MB

// =============================================================================
// Proxy & Routing Configuration
// =============================================================================

export const HTTP_PROXY_PATH = process.env.HTTP_PUBLIC_PATH || '/api';
export const WEBSOCKET_PROXY_PATH = process.env.WS_PUBLIC_PATH || '/ws';
export const WEBSOCKET_BACKEND_PATH = '/websocket';

// =============================================================================
// API Routes
// =============================================================================

export const CHAT_STREAM = '/chat/stream';
export const CHAT = '/chat';
export const GENERATE_STREAM = '/generate/stream';
export const GENERATE = '/generate';
export const CA_RAG_INIT = '/init';
export const CHAT_CA_RAG = '/call';
export const UPDATE_DATA_STREAM = '/update-data-stream';
export const MCP_CLIENT_TOOL_LIST = '/mcp/client/tool/list';
export const FEEDBACK = '/feedback';

// =============================================================================
// Route Collections
// =============================================================================

export const CORE_ROUTES = {
  CHAT_STREAM,
  CHAT,
  GENERATE_STREAM,
  GENERATE,
  MCP_CLIENT_TOOL_LIST,
};

export const EXTENDED_ROUTES = {
  CA_RAG_INIT,
  CHAT_CA_RAG,
  UPDATE_DATA_STREAM,
  FEEDBACK,
};

// =============================================================================
// Route UI Configuration
// =============================================================================

export const CORE_ROUTE_OPTIONS = [
  { label: 'Chat Completions — Streaming', value: CHAT_STREAM },
  { label: 'Chat Completions — Non-Streaming', value: CHAT },
  { label: 'Generate — Streaming', value: GENERATE_STREAM },
  { label: 'Generate — Non-Streaming', value: GENERATE },
  {
    label: 'Context-Aware RAG — Non-Streaming (Experimental)',
    value: CHAT_CA_RAG,
  },
];

export const DEFAULT_CORE_ROUTE = CHAT_STREAM;

// =============================================================================
// Security & Validation
// =============================================================================

export const ALLOWED_PATHS = [
  ...Object.values(CORE_ROUTES),
  ...Object.values(EXTENDED_ROUTES),
];

// =============================================================================
// HTTP Methods
// =============================================================================

export const HTTP_METHOD_GET = 'GET';
export const HTTP_METHOD_POST = 'POST';
export const HTTP_METHOD_PUT = 'PUT';
export const HTTP_METHOD_DELETE = 'DELETE';
export const HTTP_METHOD_OPTIONS = 'OPTIONS';

// =============================================================================
// HTTP Headers
// =============================================================================

export const HTTP_HEADER_CONTENT_TYPE = 'Content-Type';
export const HTTP_HEADER_AUTHORIZATION = 'Authorization';
export const HTTP_HEADER_CONVERSATION_ID = 'Conversation-Id';
export const HTTP_HEADER_TIMEZONE = 'X-Timezone';
export const HTTP_HEADER_USER_MESSAGE_ID = 'User-Message-ID';

// =============================================================================
// CORS Configuration
// =============================================================================

export const CORS_METHODS = [
  HTTP_METHOD_GET,
  HTTP_METHOD_POST,
  HTTP_METHOD_PUT,
  HTTP_METHOD_DELETE,
  HTTP_METHOD_OPTIONS,
].join(', ');

export const CORS_HEADERS = [
  HTTP_HEADER_CONTENT_TYPE,
  HTTP_HEADER_AUTHORIZATION,
  HTTP_HEADER_CONVERSATION_ID,
  HTTP_HEADER_TIMEZONE,
  HTTP_HEADER_USER_MESSAGE_ID,
].join(', ');

export const CORS_ORIGIN = process.env.CORS_ORIGIN || 'http://localhost:3000';
