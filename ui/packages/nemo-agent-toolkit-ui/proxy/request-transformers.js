/**
 * Request payload builders for backend API routes
 * These functions build endpoint-specific payloads that the UI calls directly
 */

/**
 * Parse optional generation parameters string into object
 * Format: "key1=value1,key2=value2" or JSON string
 * @param {string} paramsString - String containing optional parameters
 * @returns {Object} Object with parsed parameters
 */
function parseOptionalParams(paramsString) {
  if (!paramsString || !paramsString.trim()) {
    return {};
  }

  try {
    // Try parsing as JSON first
    return JSON.parse(paramsString);
  } catch {
    // Fall back to comma-separated key=value format
    const params = {};
    const pairs = paramsString.split(',');

    for (const pair of pairs) {
      const [key, value] = pair.split('=').map((s) => s.trim());
      if (key && value) {
        // Try to parse as number or boolean
        if (value === 'true') params[key] = true;
        else if (value === 'false') params[key] = false;
        else if (!isNaN(Number(value))) params[key] = Number(value);
        else params[key] = value;
      }
    }

    return params;
  }
}

/**
 * Build request payload for /generate/stream endpoint
 * Backend format: {"input_message": "..."}
 *
 * @param {string} message - The user's message content
 * @returns {Object} Backend request payload
 */
function buildGenerateStreamPayload(message) {
  return {
    input_message: message || '',
  };
}

/**
 * Build request payload for /generate endpoint
 * Backend format: {"input_message": "..."}
 *
 * @param {string} message - The user's message content
 * @returns {Object} Backend request payload
 */
function buildGeneratePayload(message) {
  return {
    input_message: message || '',
  };
}

/**
 * Build request payload for /chat endpoint
 * Backend format: {"messages": [...], "model": "...", "stream": false, "temperature": 0.7, ...}
 *
 * @param {Array} messages - Array of message objects with role and content
 * @param {boolean} useChatHistory - Whether to use full chat history or just last message
 * @param {string} optionalParams - Optional generation parameters string
 * @returns {Object} Backend request payload
 */
function buildChatPayload(messages, useChatHistory, optionalParams) {
  // Reserved fields that cannot be overridden by optionalParams
  const RESERVED_FIELDS = ['messages', 'stream'];

  const payload = {
    messages: useChatHistory ? messages : [messages[messages.length - 1]],
    model: 'nvidia/nemotron',
    stream: false,
    temperature: 0.7,
  };

  // Merge optional generation parameters if provided, filtering out reserved fields
  if (optionalParams && optionalParams.trim()) {
    try {
      const parsedParams = parseOptionalParams(optionalParams);

      // Only merge non-reserved fields
      Object.keys(parsedParams).forEach((key) => {
        if (!RESERVED_FIELDS.includes(key)) {
          payload[key] = parsedParams[key];
        }
      });
    } catch (error) {
      // Silently ignore parse errors - payload will use defaults
    }
  }

  return payload;
}

/**
 * Build request payload for /chat/stream endpoint
 * Backend format: {"messages": [...], "model": "...", "stream": true, "temperature": 0.7, ...}
 *
 * @param {Array} messages - Array of message objects with role and content
 * @param {boolean} useChatHistory - Whether to use full chat history or just last message
 * @param {string} optionalParams - Optional generation parameters string
 * @returns {Object} Backend request payload
 */
function buildChatStreamPayload(messages, useChatHistory, optionalParams) {
  // Reserved fields that cannot be overridden by optionalParams
  const RESERVED_FIELDS = ['messages', 'stream'];

  const payload = {
    messages: useChatHistory ? messages : [messages[messages.length - 1]],
    model: 'nvidia/nemotron',
    stream: true,
    temperature: 0.7,
  };

  // Merge optional generation parameters if provided, filtering out reserved fields
  if (optionalParams && optionalParams.trim()) {
    try {
      const parsedParams = parseOptionalParams(optionalParams);

      // Only merge non-reserved fields
      Object.keys(parsedParams).forEach((key) => {
        if (!RESERVED_FIELDS.includes(key)) {
          payload[key] = parsedParams[key];
        }
      });
    } catch (error) {
      // Silently ignore parse errors - payload will use defaults
    }
  }

  return payload;
}

module.exports = {
  buildGenerateStreamPayload,
  buildGeneratePayload,
  buildChatPayload,
  buildChatStreamPayload,
  parseOptionalParams,
};
