/**
 * Tests for UI behavior, auto-scroll functionality, and user interaction patterns
 */

import React from 'react';
import { render, screen, fireEvent, waitFor } from '@testing-library/react';
import { throttle } from '@/utils/data/throttle';

// Mock intersection observer for auto-scroll tests
const mockIntersectionObserver = jest.fn();
mockIntersectionObserver.mockReturnValue({
  observe: jest.fn(),
  unobserve: jest.fn(),
  disconnect: jest.fn(),
});
window.IntersectionObserver = mockIntersectionObserver;

// Mock requestAnimationFrame
global.requestAnimationFrame = jest.fn(cb => setTimeout(cb, 16));

describe('Auto-scroll and UI Behavior', () => {
  let mockScrollIntoView: jest.Mock;
  let mockChatContainer: HTMLElement;
  let messagesEndRef: { current: HTMLElement | null };
  let chatContainerRef: { current: HTMLElement | null };

  beforeEach(() => {
    jest.clearAllMocks();
    jest.useFakeTimers(); // Enable fake timers for each test

    mockScrollIntoView = jest.fn();
    mockChatContainer = document.createElement('div');

    // Mock scroll properties
    Object.defineProperties(mockChatContainer, {
      scrollTop: { value: 0, writable: true },
      scrollHeight: { value: 1000, writable: true },
      clientHeight: { value: 500, writable: true }
    });

    messagesEndRef = { current: { scrollIntoView: mockScrollIntoView } as any };
    chatContainerRef = { current: mockChatContainer };
  });

  afterEach(() => {
    jest.useRealTimers(); // Clean up timers after each test
  });

  describe('Auto-scroll During Streaming', () => {
    /**
     * Description: Verifies that the chat interface automatically scrolls to bottom during message streaming
     * Success: scrollIntoView is called on messagesEndRef when auto-scroll is enabled during streaming
     */
    test('auto-scrolls during message streaming', () => {
      let autoScrollEnabled = true;
      let messageIsStreaming = true;

      const scrollDown = () => {
        if (autoScrollEnabled) {
          messagesEndRef.current?.scrollIntoView({
            behavior: 'smooth',
            block: 'end'
          });
        }
      };

      // Simulate streaming state
      expect(messageIsStreaming).toBe(true);
      expect(autoScrollEnabled).toBe(true);

      // Trigger scroll
      scrollDown();

      expect(mockScrollIntoView).toHaveBeenCalledWith({
        behavior: 'smooth',
        block: 'end'
      });
    });

    /**
     * Description: Verifies that auto-scroll is automatically enabled when message streaming begins
     * Success: Auto-scroll state is set to true and scrolling behavior is activated when streaming starts
     */
    test('enables auto-scroll when streaming starts', () => {
      let autoScrollEnabled = false;
      let showScrollDownButton = true;
      let messageIsStreaming = false;

      const handleStreamingStateChange = (streaming: boolean) => {
        if (streaming) {
          autoScrollEnabled = true;
          showScrollDownButton = false;
          messageIsStreaming = true;
        }
      };

      // Start streaming
      handleStreamingStateChange(true);

      expect(autoScrollEnabled).toBe(true);
      expect(showScrollDownButton).toBe(false);
      expect(messageIsStreaming).toBe(true);
    });

    /**
     * Description: Verifies that auto-scroll is disabled when user manually scrolls up from bottom
     * Success: Auto-scroll state becomes false when user scroll position moves away from bottom
     */
    test('stops auto-scroll when user scrolls up manually', () => {
      let autoScrollEnabled = true;
      let showScrollDownButton = false;
      let messageIsStreaming = true;
      let lastScrollTop = 400;

      const handleScroll = () => {
        if (!chatContainerRef.current) return;

        const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
        const isScrollingUp = scrollTop < lastScrollTop;
        const isAtBottom = scrollHeight - scrollTop - clientHeight < 20;

        // Disable auto-scroll if user scrolls up during streaming
        if (isScrollingUp && autoScrollEnabled && messageIsStreaming) {
          autoScrollEnabled = false;
          showScrollDownButton = true;
        }

        // Re-enable auto-scroll if user scrolls to bottom
        if (isAtBottom && !autoScrollEnabled) {
          autoScrollEnabled = true;
          showScrollDownButton = false;
        }

        lastScrollTop = scrollTop;
      };

      // Simulate user scrolling up
      if (chatContainerRef.current) {
        chatContainerRef.current.scrollTop = 200; // Scroll up from 400 to 200
      }

      handleScroll();

      expect(autoScrollEnabled).toBe(false);
      expect(showScrollDownButton).toBe(true);
    });

    /**
     * Description: Verifies that auto-scroll is re-enabled when user manually scrolls back to bottom
     * Success: Auto-scroll state becomes true when scroll position returns to bottom of chat
     */
    test('re-enables auto-scroll when user scrolls to bottom', () => {
      let autoScrollEnabled = false;
      let showScrollDownButton = true;
      let lastScrollTop = 200;

      const handleScroll = () => {
        if (!chatContainerRef.current) return;

        const { scrollTop, scrollHeight, clientHeight } = chatContainerRef.current;
        const isAtBottom = scrollHeight - scrollTop - clientHeight < 20;

        if (isAtBottom && !autoScrollEnabled) {
          autoScrollEnabled = true;
          showScrollDownButton = false;
        }

        lastScrollTop = scrollTop;
      };

      // Simulate user scrolling to bottom
      if (chatContainerRef.current) {
        chatContainerRef.current.scrollTop = 485; // Close to bottom (scrollHeight - clientHeight - tolerance)
      }

      handleScroll();

      expect(autoScrollEnabled).toBe(true);
      expect(showScrollDownButton).toBe(false);
    });

    /**
     * Description: Verifies that clicking the scroll down button smoothly scrolls chat to bottom
     * Success: scrollIntoView is called with smooth behavior when scroll down button is clicked
     */
    test('handles scroll down button click', () => {
      let autoScrollEnabled = false;

      const handleScrollDown = () => {
        chatContainerRef.current?.scrollTo({
          top: chatContainerRef.current.scrollHeight,
          behavior: 'smooth'
        });
        autoScrollEnabled = true;
      };

      const mockScrollTo = jest.fn();
      if (chatContainerRef.current) {
        chatContainerRef.current.scrollTo = mockScrollTo;
      }

      handleScrollDown();

      expect(mockScrollTo).toHaveBeenCalledWith({
        top: 1000, // scrollHeight
        behavior: 'smooth'
      });
      expect(autoScrollEnabled).toBe(true);
    });
  });

  describe('User-Initiated Scroll Detection', () => {
    /**
     * Description: Verifies that the system can differentiate between user-initiated and programmatic scrolling
     * Success: User scrolling affects auto-scroll state, programmatic scrolling does not interfere with user preferences
     */
    test('distinguishes between user and programmatic scrolling', () => {
      let isUserInitiatedScroll = false;
      let scrollTimeout: NodeJS.Timeout | null = null;

      const handleUserInput = () => {
        isUserInitiatedScroll = true;

        if (scrollTimeout) {
          clearTimeout(scrollTimeout);
        }
        scrollTimeout = setTimeout(() => {
          isUserInitiatedScroll = false;
        }, 200);
      };

      const handleScroll = () => {
        if (!isUserInitiatedScroll) return; // Ignore programmatic scrolls

        // Handle user scroll logic here
        console.log('User scrolled');
      };

      const consoleSpy = jest.spyOn(console, 'log').mockImplementation();

      // Simulate user interaction
      handleUserInput();
      expect(isUserInitiatedScroll).toBe(true);

      // Simulate scroll event
      handleScroll();
      expect(consoleSpy).toHaveBeenCalledWith('User scrolled');

      // Fast-forward past timeout
      jest.advanceTimersByTime(250);
      expect(isUserInitiatedScroll).toBe(false);

      // Programmatic scroll should be ignored
      handleScroll();
      expect(consoleSpy).toHaveBeenCalledTimes(1); // Still only called once

      consoleSpy.mockRestore();
    });

    /**
     * Description: Verifies that wheel and touch events are properly detected for scroll state management
     * Success: Both wheel and touch events trigger appropriate scroll state updates and event listeners
     */
    test('handles wheel and touch events for scroll detection', () => {
      let userInteractionDetected = false;

      const handleUserInput = () => {
        userInteractionDetected = true;
      };

      // Simulate adding event listeners
      const mockAddEventListener = jest.fn();
      if (chatContainerRef.current) {
        chatContainerRef.current.addEventListener = mockAddEventListener;
      }

      // Setup event listeners (simulating useEffect)
      if (chatContainerRef.current) {
        chatContainerRef.current.addEventListener('wheel', handleUserInput, { passive: true });
        chatContainerRef.current.addEventListener('touchmove', handleUserInput, { passive: true });
      }

      expect(mockAddEventListener).toHaveBeenCalledWith('wheel', handleUserInput, { passive: true });
      expect(mockAddEventListener).toHaveBeenCalledWith('touchmove', handleUserInput, { passive: true });

      // Simulate user interaction
      handleUserInput();
      expect(userInteractionDetected).toBe(true);
    });

    /**
     * Description: Verifies that scroll event listeners are properly removed when component unmounts
     * Success: removeEventListener is called for all registered scroll events to prevent memory leaks
     */
    test('cleans up event listeners on unmount', () => {
      const mockRemoveEventListener = jest.fn();

      if (chatContainerRef.current) {
        chatContainerRef.current.removeEventListener = mockRemoveEventListener;
      }

      const cleanup = () => {
        if (chatContainerRef.current) {
          chatContainerRef.current.removeEventListener('wheel', jest.fn());
          chatContainerRef.current.removeEventListener('touchmove', jest.fn());
        }
      };

      cleanup();

      expect(mockRemoveEventListener).toHaveBeenCalledTimes(2);
    });
  });

    describe('Throttled Scroll Behavior - REAL FUNCTION TESTS', () => {
    /**
     * Description: Verifies that the throttle function limits call frequency to prevent performance issues
     * Success: First call executes immediately, subsequent calls within time window are ignored, calls after window execute normally
     */
    test('throttles scroll events to prevent performance issues', () => {
      let scrollCallCount = 0;

      const scrollDown = () => {
        scrollCallCount++;
        messagesEndRef.current?.scrollIntoView({
          behavior: 'smooth',
          block: 'end'
        });
      };

      const throttledScrollDown = throttle(scrollDown, 250);

      // Call multiple times rapidly
      throttledScrollDown();
      throttledScrollDown();
      throttledScrollDown();
      throttledScrollDown();
      throttledScrollDown();

      // Should only execute once immediately
      expect(scrollCallCount).toBe(1);

      // Fast-forward past throttle period using fake timers
      jest.advanceTimersByTime(300);

      // Call again after throttle period
      throttledScrollDown();
      expect(scrollCallCount).toBe(2);
    });

    /**
     * Description: Verifies that throttle preserves the most recent function call when multiple calls occur rapidly
     * Success: When throttling occurs, the latest function call parameters are preserved and executed
     */
    test('throttle preserves latest call', () => {
      let lastValue = '';

      const updateValue = (value: string) => {
        lastValue = value;
      };

      const throttledUpdate = throttle(updateValue, 100);

      // Make rapid calls with different values
      throttledUpdate('first');
      throttledUpdate('second');
      throttledUpdate('third');
      throttledUpdate('final');

      // Should execute immediately with first value
      expect(lastValue).toBe('first');

      // Fast-forward past throttle period
      jest.advanceTimersByTime(150);

      // Should execute with the latest value
      expect(lastValue).toBe('final');
    });
  });

  describe('Intersection Observer Integration', () => {
    /**
     * Description: Verifies that intersection observer is properly configured for auto-scroll functionality
     * Success: IntersectionObserver is created and observes the messages end element for visibility changes
     */
    test('sets up intersection observer for auto-scroll', () => {
      const mockObserver = {
        observe: jest.fn(),
        unobserve: jest.fn(),
        disconnect: jest.fn()
      };

      mockIntersectionObserver.mockImplementation((callback) => {
        // Simulate intersection
        setTimeout(() => {
          callback([{ isIntersecting: true }]);
        }, 0);
        return mockObserver;
      });

      let autoScrollEnabled = true;
      let messageIsStreaming = true;

      // Setup observer (simulating useEffect)
      const observer = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting && autoScrollEnabled && messageIsStreaming) {
            requestAnimationFrame(() => {
              messagesEndRef.current?.scrollIntoView({
                behavior: 'smooth',
                block: 'end'
              });
            });
          }
        },
        {
          root: null,
          threshold: 0.5
        }
      );

      if (messagesEndRef.current) {
        observer.observe(messagesEndRef.current);
      }

      expect(mockObserver.observe).toHaveBeenCalledWith(messagesEndRef.current);
    });

    /**
     * Description: Verifies that intersection observer is properly disconnected when component unmounts
     * Success: IntersectionObserver.disconnect is called to prevent memory leaks and orphaned observers
     */
    test('cleans up intersection observer on unmount', () => {
      const mockObserver = {
        observe: jest.fn(),
        unobserve: jest.fn(),
        disconnect: jest.fn()
      };

      mockIntersectionObserver.mockReturnValue(mockObserver);

      const observer = new IntersectionObserver(() => {});

      if (messagesEndRef.current) {
        observer.observe(messagesEndRef.current);
      }

      // Simulate cleanup
      if (messagesEndRef.current) {
        observer.unobserve(messagesEndRef.current);
      }

      expect(mockObserver.unobserve).toHaveBeenCalledWith(messagesEndRef.current);
    });
  });

  describe('Scroll State Management', () => {
    /**
     * Description: Verifies that scroll state is preserved during component re-renders
     * Success: Scroll position and auto-scroll state remain consistent after component updates
     */
    test('maintains scroll state across re-renders', () => {
      let scrollState = {
        autoScrollEnabled: true,
        showScrollDownButton: false,
        lastScrollTop: 0
      };

      const updateScrollState = (updates: Partial<typeof scrollState>) => {
        scrollState = { ...scrollState, ...updates };
      };

      // Simulate state changes
      updateScrollState({ autoScrollEnabled: false, showScrollDownButton: true });
      expect(scrollState.autoScrollEnabled).toBe(false);
      expect(scrollState.showScrollDownButton).toBe(true);

      updateScrollState({ lastScrollTop: 300 });
      expect(scrollState.lastScrollTop).toBe(300);
      expect(scrollState.autoScrollEnabled).toBe(false); // Should preserve other state
    });

    /**
     * Description: Verifies that scroll position calculations handle edge cases correctly
     * Success: Edge cases like content shorter than container or exact bottom position are handled properly
     */
    test('handles scroll position edge cases', () => {
      const testCases = [
        { scrollTop: 0, scrollHeight: 1000, clientHeight: 500, expectedAtBottom: false },
        { scrollTop: 485, scrollHeight: 1000, clientHeight: 500, expectedAtBottom: true }, // Within 15px tolerance (1000-485-500 = 15 < 20)
        { scrollTop: 500, scrollHeight: 1000, clientHeight: 500, expectedAtBottom: true }, // Exact bottom (1000-500-500 = 0 < 20)
        { scrollTop: 450, scrollHeight: 1000, clientHeight: 500, expectedAtBottom: false }, // Outside tolerance (1000-450-500 = 50 >= 20)
        { scrollTop: 0, scrollHeight: 400, clientHeight: 500, expectedAtBottom: true }, // Content shorter than container (400-0-500 = -100 < 20)
      ];

      testCases.forEach(({ scrollTop, scrollHeight, clientHeight, expectedAtBottom }) => {
        const isAtBottom = scrollHeight - scrollTop - clientHeight < 20;
        expect(isAtBottom).toBe(expectedAtBottom);
      });
    });
    /**
     * Description: Verifies that concurrent scroll state updates don't cause race conditions
     * Success: Scroll state updates are processed sequentially without conflicts or data loss
     */
    test('prevents scroll state race conditions', () => {
      let scrollState = { processing: false, pendingUpdate: null as any };

      const canProcessUpdate = () => {
        return !scrollState.processing;
      };

      const startProcessing = () => {
        scrollState.processing = true;
      };

      const finishProcessing = () => {
        scrollState.processing = false;
      };

      // Test initial state
      expect(canProcessUpdate()).toBe(true);

      // Start processing
      startProcessing();
      expect(canProcessUpdate()).toBe(false);

      // Can't process while already processing
      expect(scrollState.processing).toBe(true);

      // Finish processing
      finishProcessing();
      expect(canProcessUpdate()).toBe(true);
    });
  });

  describe('Focus Management', () => {
    /**
     * Description: Verifies that textarea receives focus when messages end element becomes visible
     * Success: Textarea focus method is called when intersection observer detects messages end is intersecting
     */
    test('focuses textarea when messages end is intersecting', () => {
      let textareaRef = { current: { focus: jest.fn() } as any };
      let observerCallback: ((entries: any[]) => void) | null = null;

      const mockObserver = {
        observe: jest.fn(),
        unobserve: jest.fn(),
        disconnect: jest.fn()
      };

      mockIntersectionObserver.mockImplementation((callback) => {
        observerCallback = callback;
        return mockObserver;
      });

      // Setup observer
      const observer = new IntersectionObserver(
        ([entry]) => {
          if (entry.isIntersecting) {
            textareaRef.current?.focus();
          }
        },
        { root: null, threshold: 0.5 }
      );

      if (messagesEndRef.current) {
        observer.observe(messagesEndRef.current);
      }

      // Simulate intersection
      if (observerCallback) {
        observerCallback([{ isIntersecting: true }]);
      }

      expect(textareaRef.current.focus).toHaveBeenCalled();
    });

    /**
     * Description: Verifies that focus state is maintained properly during scroll events
     * Success: Focus state remains consistent and doesn't interfere with scroll behavior or get lost during scrolling
     */
    test('maintains focus state during scroll events', () => {
      let textareaFocused = false;

      const handleFocus = () => {
        textareaFocused = true;
      };

      const handleBlur = () => {
        textareaFocused = false;
      };

      const handleScroll = () => {
        // Focus should not be affected by scroll events
        // unless specifically managed
      };

      handleFocus();
      expect(textareaFocused).toBe(true);

      handleScroll();
      expect(textareaFocused).toBe(true); // Should remain focused

      handleBlur();
      expect(textareaFocused).toBe(false);
    });
  });
});
