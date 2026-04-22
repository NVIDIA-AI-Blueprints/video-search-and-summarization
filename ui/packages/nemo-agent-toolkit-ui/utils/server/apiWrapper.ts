import { NextApiRequest, NextApiResponse } from 'next';

export interface ApiWrapperOptions {
  allowedMethods?: string[];
  bodyParserConfig?: {
    sizeLimit?: string;
  };
}

/**
 * Creates a Next.js API route handler that wraps an Edge Runtime handler
 * @param edgeHandler - The Edge Runtime handler function
 * @param options - Configuration options
 * @returns Next.js API route handler
 */
export function createApiWrapper(
  edgeHandler: (request: Request) => Promise<Response>,
  options: ApiWrapperOptions = {}
) {
  const { 
    allowedMethods = ['POST'], 
    bodyParserConfig = { sizeLimit: '5mb' } 
  } = options;

  const handler = async (req: NextApiRequest, res: NextApiResponse) => {
    // Method validation
    if (!allowedMethods.includes(req.method || '')) {
      return res.status(405).json({ error: 'Method not allowed' });
    }

    try {
      // Convert NextApiRequest to Web API Request
      const protocol = req.headers.host?.startsWith('localhost') ? 'http' : 'https';
      const url = `${protocol}://${req.headers.host}${req.url}`;
      
      const webRequest = new Request(url, {
        method: req.method,
        headers: new Headers(req.headers as HeadersInit),
        body: req.method !== 'GET' ? JSON.stringify(req.body) : undefined,
      });

      // Call the Edge Runtime handler
      const webResponse = await edgeHandler(webRequest);

      // Transfer headers from web response to Next.js response
      webResponse.headers.forEach((value, key) => {
        res.setHeader(key, value);
      });

      // Set status
      res.status(webResponse.status);

      // Handle streaming vs non-streaming responses
      if (webResponse.body) {
        const reader = webResponse.body.getReader();
        const decoder = new TextDecoder();

        try {
          while (true) {
            const { done, value } = await reader.read();
            if (done) break;
            
            const chunk = decoder.decode(value, { stream: true });
            res.write(chunk);
          }
        } finally {
          reader.releaseLock();
        }
      }

      res.end();
    } catch (error) {
      console.error('API wrapper error:', error);
      res.status(500).json({ error: 'Internal server error' });
    }
  };

  // Attach Next.js API route config
  (handler as any).config = {
    api: {
      bodyParser: bodyParserConfig,
    },
  };

  return handler;
}

// Specialized wrapper for chat API
export function createChatApiWrapper(edgeHandler: (request: Request) => Promise<Response>) {
  return createApiWrapper(edgeHandler, {
    allowedMethods: ['POST'],
    bodyParserConfig: { sizeLimit: '5mb' }
  });
}
