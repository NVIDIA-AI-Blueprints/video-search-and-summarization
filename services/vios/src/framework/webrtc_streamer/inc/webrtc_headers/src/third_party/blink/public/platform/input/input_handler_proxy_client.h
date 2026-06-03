// Copyright 2013 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_PUBLIC_PLATFORM_INPUT_INPUT_HANDLER_PROXY_CLIENT_H_
#define THIRD_PARTY_BLINK_PUBLIC_PLATFORM_INPUT_INPUT_HANDLER_PROXY_CLIENT_H_

namespace blink {
class WebCoalescedInputEvent;
class WebGestureEvent;

// All callbacks invoked from the compositor thread.
class InputHandlerProxyClient {
 public:
  // Called just before the InputHandlerProxy shuts down.
  virtual void WillShutdown() = 0;

  // Dispatch a non blocking event to the main thread. This is used when a
  // gesture fling from a touchpad is processed and the target only has
  // passive event listeners.
  virtual void DispatchNonBlockingEventToMainThread(
      std::unique_ptr<WebCoalescedInputEvent> event,
      const blink::WebInputEventAttribution& attribution) = 0;

  virtual void DidAnimateForInput() = 0;

  virtual void DidStartScrollingViewport() = 0;

  // Used to send a GSB to the main thread when the scrolling should switch to
  // the main thread.
  virtual void GenerateScrollBeginAndSendToMainThread(
      const blink::WebGestureEvent& update_event,
      const blink::WebInputEventAttribution& attribution) = 0;

  virtual void SetWhiteListedTouchAction(
      cc::TouchAction touch_action,
      uint32_t unique_touch_event_id,
      InputHandlerProxy::EventDisposition event_disposition) = 0;

 protected:
  virtual ~InputHandlerProxyClient() {}
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_PUBLIC_PLATFORM_INPUT_INPUT_HANDLER_PROXY_CLIENT_H_
