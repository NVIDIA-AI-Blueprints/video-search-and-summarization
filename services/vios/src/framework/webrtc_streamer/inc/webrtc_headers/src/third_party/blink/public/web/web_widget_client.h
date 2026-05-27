/*
 * Copyright (C) 2009 Google Inc. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions are
 * met:
 *
 *     * Redistributions of source code must retain the above copyright
 * notice, this list of conditions and the following disclaimer.
 *     * Redistributions in binary form must reproduce the above
 * copyright notice, this list of conditions and the following disclaimer
 * in the documentation and/or other materials provided with the
 * distribution.
 *     * Neither the name of Google Inc. nor the names of its
 * contributors may be used to endorse or promote products derived from
 * this software without specific prior written permission.
 *
 * THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS
 * "AS IS" AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT
 * LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR
 * A PARTICULAR PURPOSE ARE DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT
 * OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT, INDIRECT, INCIDENTAL,
 * SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING, BUT NOT
 * LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
 * DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY
 * THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
 * OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#ifndef THIRD_PARTY_BLINK_PUBLIC_WEB_WEB_WIDGET_CLIENT_H_
#define THIRD_PARTY_BLINK_PUBLIC_WEB_WEB_WIDGET_CLIENT_H_

#include <memory>

#include "base/callback.h"
#include "base/i18n/rtl.h"
#include "base/time/time.h"
#include "cc/trees/layer_tree_host.h"
#include "components/viz/common/surfaces/frame_sink_id.h"
#include "services/network/public/mojom/referrer_policy.mojom-shared.h"
#include "third_party/blink/public/common/input/web_coalesced_input_event.h"
#include "third_party/blink/public/common/input/web_gesture_event.h"
#include "third_party/blink/public/common/page/web_drag_operation.h"
#include "third_party/blink/public/mojom/input/input_handler.mojom-shared.h"
#include "third_party/blink/public/mojom/input/pointer_lock_result.mojom-forward.h"
#include "third_party/blink/public/platform/cross_variant_mojo_util.h"
#include "third_party/blink/public/platform/web_common.h"
#include "third_party/blink/public/platform/web_rect.h"
#include "third_party/blink/public/platform/web_screen_info.h"
#include "third_party/blink/public/platform/web_touch_action.h"
#include "third_party/blink/public/web/web_meaningful_layout.h"
#include "third_party/blink/public/web/web_navigation_policy.h"

class SkBitmap;

namespace cc {
class PaintImage;
}

namespace gfx {
class Point;
class PointF;
}

namespace ui {
class Cursor;
}

namespace blink {
class WebDragData;
class WebMouseEvent;
class WebGestureEvent;
struct WebFloatRect;
class WebWidget;
class WebLocalFrame;

class WebWidgetClient {
 public:
  virtual ~WebWidgetClient() = default;

  // Called to request a BeginMainFrame from the compositor. For tests with
  // single thread and no scheduler, the impl should schedule a task to run
  // a synchronous composite.
  virtual void ScheduleAnimation() {}

  // Called to request a BeginMainFrame from the compositor, meant to be used
  // for web tests only, where commits must be explicitly scheduled. Contrary to
  // ScheduleAnimation() this will be a no-op on multi-threaded environments and
  // will unconditionally ensure that the compositor is actually run.
  virtual void ScheduleAnimationForWebTests() {}

  // Called immediately following the first compositor-driven (frame-generating)
  // layout that happened after an interesting document lifecyle change (see
  // WebMeaningfulLayout for details.)
  virtual void DidMeaningfulLayout(WebMeaningfulLayout) {}

  // Called when the cursor for the widget changes.
  virtual void DidChangeCursor(const ui::Cursor&) {}

  // Called to show the widget according to the given policy.
  virtual void Show(WebNavigationPolicy) {}

  // Returns information about the screen where this view's widgets are being
  // displayed.
  virtual WebScreenInfo GetScreenInfo() { return {}; }

  // Called to get/set the position of the widget's window in screen
  // coordinates. Note, the window includes any decorations such as borders,
  // scrollbars, URL bar, tab strip, etc. if they exist.
  virtual WebRect WindowRect() { return WebRect(); }
  virtual void SetWindowRect(const WebRect&) {}

  // Called to get the view rect in screen coordinates. This is the actual
  // content view area, i.e. doesn't include any window decorations.
  virtual WebRect ViewRect() { return WebRect(); }

  // Requests to lock the mouse cursor for the |requester_frame| in the
  // widget. If true is returned, the success result will be asynchronously
  // returned via a single call to WebWidget::didAcquirePointerLock() or
  // WebWidget::didNotAcquirePointerLock() and a single call to the callback.
  // If false, the request has been denied synchronously.
  using PointerLockCallback =
      base::OnceCallback<void(mojom::PointerLockResult)>;
  virtual bool RequestPointerLock(WebLocalFrame* requester_frame,
                                  PointerLockCallback callback,
                                  bool request_unadjusted_movement) {
    return false;
  }

  virtual bool RequestPointerLockChange(WebLocalFrame* requester_frame,
                                        PointerLockCallback callback,
                                        bool request_unadjusted_movement) {
    return false;
  }

  // Cause the pointer lock to be released. This may be called at any time,
  // including when a lock is pending but not yet acquired.
  // WebWidget::didLosePointerLock() is called when unlock is complete.
  virtual void RequestPointerUnlock() {}

  // Returns true iff the pointer is locked to this widget.
  virtual bool IsPointerLocked() { return false; }

  // Called when overscrolled on main thread. All parameters are in
  // viewport-space.
  virtual void DidOverscroll(const gfx::Vector2dF& overscroll_delta,
                             const gfx::Vector2dF& accumulated_overscroll,
                             const gfx::PointF& position_in_viewport,
                             const gfx::Vector2dF& velocity_in_viewport,
                             cc::OverscrollBehavior overscroll_behavior) {}

  // Called to update if pointerrawupdate events should be sent.
  virtual void SetHasPointerRawUpdateEventHandlers(bool) {}

  // Called to update whether low latency input mode is enabled or not.
  virtual void SetNeedsLowLatencyInput(bool) {}

  // Requests unbuffered (ie. low latency) input until a pointerup
  // event occurs.
  virtual void RequestUnbufferedInputEvents() {}

  // Requests unbuffered (ie. low latency) input due to debugger being
  // attached. Debugger needs to paint when stopped in the event handler.
  virtual void SetNeedsUnbufferedInputForDebugger(bool) {}

  // Called during WebWidget::HandleInputEvent for a TouchStart event to inform
  // the embedder of the touch actions that are permitted for this touch.
  virtual void SetTouchAction(WebTouchAction touch_action) {}

  // Converts the |rect| from Blink's Viewport coordinates to the
  // coordinates in the native window used to display the content, in
  // DIP.  They're identical in tradional world, but will differ when
  // use-zoom-for-dsf feature is eanbled, and Viewport coordinates
  // becomes DSF times larger than window coordinates.
  // TODO(oshima): Update the comment when the migration is completed.
  virtual void ConvertViewportToWindow(WebRect* rect) {}

  // Converts the |rect| from Blink's Viewport coordinates to the
  // coordinates in the native window used to display the content, in
  // DIP.  They're identical in tradional world, but will differ when
  // use-zoom-for-dsf feature is eanbled, and Viewport coordinates
  // becomes DSF times larger than window coordinates.
  // TODO(oshima): Update the comment when the migration is completed.
  virtual void ConvertViewportToWindow(WebFloatRect* rect) {}

  // Converts the |rect| from the coordinates in native window in
  // DIP to Blink's Viewport coordinates. They're identical in
  // tradional world, but will differ when use-zoom-for-dsf feature
  // is eanbled.  TODO(oshima): Update the comment when the
  // migration is completed.
  virtual void ConvertWindowToViewport(WebFloatRect* rect) {}

  // Called when a drag-and-drop operation should begin.
  virtual void StartDragging(network::mojom::ReferrerPolicy,
                             const WebDragData&,
                             WebDragOperationsMask,
                             const SkBitmap& drag_image,
                             const gfx::Point& drag_image_offset) {}

  // Sets the current page scale factor and minimum / maximum limits. Both
  // limits are initially 1 (no page scale allowed).
  virtual void SetPageScaleStateAndLimits(float page_scale_factor,
                                          bool is_pinch_gesture_active,
                                          float minimum,
                                          float maximum) {}

  // Dispatch any pending input. This method will called before
  // dispatching a RequestAnimationFrame to the widget.
  virtual void DispatchRafAlignedInput(base::TimeTicks frame_time) {}

  // Requests an image decode and will have the |callback| run asynchronously
  // when it completes. Forces a new main frame to occur that will trigger
  // pushing the decode through the compositor.
  virtual void RequestDecode(const cc::PaintImage& image,
                             base::OnceCallback<void(bool)> callback) {}

  using LayerTreeFrameSinkCallback = base::OnceCallback<void(
      std::unique_ptr<cc::LayerTreeFrameSink>,
      std::unique_ptr<cc::RenderFrameMetadataObserver>)>;

  // Requests a LayerTreeFrameSink to submit CompositorFrames to.
  virtual void RequestNewLayerTreeFrameSink(
      LayerTreeFrameSinkCallback callback) {}

  virtual viz::FrameSinkId GetFrameSinkId() {
    NOTREACHED();
    return viz::FrameSinkId();
  }

  // Notification that the LayerTreeHost started or stopped deferring main frame
  // updates.
  virtual void OnDeferMainFrameUpdatesChanged(bool defer) {}

  // Notification that the LayerTreeHost started or stopped deferring commits.
  virtual void OnDeferCommitsChanged(bool defer) {}

  // For more information on the sequence of when these callbacks are made
  // consult cc/trees/layer_tree_host_client.h.

  // Indicates that the compositor is about to begin a frame. This is primarily
  // to signal to flow control mechanisms that a frame is beginning, not to
  // perform actual painting work.
  virtual void WillBeginMainFrame() {}

  // Notification that the BeginMainFrame completed, was committed into the
  // compositor (thread) and submitted to the display compositor.
  virtual void DidCommitAndDrawCompositorFrame() {}

  // Notification that page scale animation was changed.
  virtual void DidCompletePageScaleAnimation() {}

  // Notification that the output of a BeginMainFrame was committed to the
  // compositor (thread), though would not be submitted to the display
  // compositor yet (see DidCommitAndDrawCompositorFrame()).
  virtual void DidCommitCompositorFrame(base::TimeTicks commit_start_time) {}

  // Notifies that the layer tree host has completed a call to
  // RequestMainFrameUpdate in response to a BeginMainFrame.
  virtual void DidBeginMainFrame() {}

  // Record the time it took for the first paint after the widget transitioned
  // from background inactive to active.
  virtual void RecordTimeToFirstActivePaint(base::TimeDelta duration) {}

  // Returns a scale of the device emulator from the widget.
  virtual float GetEmulatorScale() const { return 1.0f; }

  // Returns whether we handled a GestureScrollEvent.
  virtual void DidHandleGestureScrollEvent(
      const WebGestureEvent& gesture_event,
      const gfx::Vector2dF& unused_delta,
      const cc::OverscrollBehavior& overscroll_behavior,
      bool event_processed) {}

  // Called after a key event has been processed.
  virtual void DidHandleKeyEvent() {}

  // Requests the virtual keyboard be displayed.
  virtual void ShowVirtualKeyboard() {}

  // Requests the text input state be updated.
  virtual void UpdateTextInputState() {}

  // Called before gesture events are processed and allows the
  // client to handle the event itself. Return true if event was handled
  // and further processing should stop.
  virtual bool WillHandleGestureEvent(const WebGestureEvent& event) {
    return false;
  }

  // Called before mouse events are processed and allows the
  // client to handle the event itself. Return true if event was handled
  // and further processing should stop.
  virtual bool WillHandleMouseEvent(const WebMouseEvent& event) {
    return false;
  }

  // Queue a sythentic event in the MainThreadEventQueue. This is called
  // for when handling scrollbars.
  virtual void QueueSyntheticEvent(
      std::unique_ptr<blink::WebCoalescedInputEvent>) {}

  // Connect the Widget Input Handler to the channels provided.
  virtual void GetWidgetInputHandler(
      CrossVariantMojoReceiver<mojom::WidgetInputHandlerInterfaceBase>
          widget_input_receiver,
      CrossVariantMojoRemote<mojom::WidgetInputHandlerHostInterfaceBase>
          widget_input_host_remote) {}
};

}  // namespace blink

#endif
