/**
 * WebSocket mock for testing
 * Provides controllable WebSocket behavior for unit tests
 */

export interface MockWebSocket {
  send: any;
  close: any;
  addEventListener: any;
  removeEventListener: any;
  onopen: ((event: Event) => void) | null;
  onmessage: ((event: MessageEvent) => void) | null;
  onclose: ((event: CloseEvent) => void) | null;
  onerror: ((event: Event) => void) | null;
  readyState: number;
  url: string;
  
  // Test helpers
  mockOpen: () => void;
  mockMessage: (data: any) => void;
  mockClose: () => void;
  mockError: () => void;
}

class MockWebSocketClass implements MockWebSocket {
  static CONNECTING = 0;
  static OPEN = 1;
  static CLOSING = 2;
  static CLOSED = 3;

  public send = (() => {}) as any;
  public close = (() => {}) as any;
  public addEventListener = (() => {}) as any;
  public removeEventListener = (() => {}) as any;
  
  public onopen: ((event: Event) => void) | null = null;
  public onmessage: ((event: MessageEvent) => void) | null = null;
  public onclose: ((event: CloseEvent) => void) | null = null;
  public onerror: ((event: Event) => void) | null = null;
  
  public readyState = MockWebSocketClass.CONNECTING;
  public url: string;

  constructor(url: string) {
    this.url = url;
    // Store instance for test access
    MockWebSocketClass.lastInstance = this;
  }

  // Test helper methods
  public mockOpen() {
    this.readyState = MockWebSocketClass.OPEN;
    if (this.onopen) {
      this.onopen(new Event('open'));
    }
  }

  public mockMessage(data: any) {
    if (this.onmessage) {
      const event = new MessageEvent('message', { 
        data: typeof data === 'string' ? data : JSON.stringify(data) 
      });
      this.onmessage(event);
    }
  }

  public mockClose() {
    this.readyState = MockWebSocketClass.CLOSED;
    if (this.onclose) {
      this.onclose(new CloseEvent('close'));
    }
  }

  public mockError() {
    if (this.onerror) {
      this.onerror(new Event('error'));
    }
  }

  // Static reference to last created instance for test access
  static lastInstance: MockWebSocketClass | null = null;
}

// Global mock
(global as any).WebSocket = MockWebSocketClass;

export default MockWebSocketClass;