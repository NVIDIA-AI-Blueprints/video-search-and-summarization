// Copyright 2015 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be found
// in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_WEB_VIEW_FRAME_WIDGET_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_WEB_VIEW_FRAME_WIDGET_H_

#include "base/macros.h"
#include "base/memory/scoped_refptr.h"
#include "base/single_thread_task_runner.h"
#include "base/util/type_safety/pass_key.h"
#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/core/frame/web_frame_widget_base.h"
#include "third_party/blink/renderer/core/frame/web_local_frame_impl.h"
#include "third_party/blink/renderer/platform/graphics/apply_viewport_changes.h"
#include "third_party/blink/renderer/platform/heap/member.h"
#include "third_party/blink/renderer/platform/heap/self_keep_alive.h"

namespace blink {

class WebFrameWidget;
class WebViewImpl;
class WebWidgetClient;

// Shim class to help normalize the widget interfaces in the Blink public API.
// For OOPI, subframes have WebFrameWidgets for input and rendering.
// Unfortunately, the main frame still uses WebView's WebWidget for input and
// rendering. This results in complex code, since there are two different
// implementations of WebWidget and code needs to have branches to handle both
// cases.
// This class allows a Blink embedder to create a WebFrameWidget that can be
// used for the main frame. Internally, it currently wraps WebView's WebWidget
// and just forwards almost everything to it.
// After the embedder starts using a WebFrameWidget for the main frame,
// WebView will be updated to no longer inherit WebWidget. The eventual goal is
// to unfork the widget code duplicated in WebFrameWidgetImpl and WebViewImpl
// into one class.
// A more detailed writeup of this transition can be read at
// https://goo.gl/7yVrnb.
class CORE_EXPORT WebViewFrameWidget : public WebFrameWidgetBase {
 public:
  WebViewFrameWidget(
      util::PassKey<WebFrameWidget>,
      WebWidgetClient&,
      WebViewImpl&,
      CrossVariantMojoAssociatedRemote<
          mojom::blink::FrameWidgetHostInterfaceBase> frame_widget_host,
      CrossVariantMojoAssociatedReceiver<mojom::blink::FrameWidgetInterfaceBase>
          frame_widget,
      CrossVariantMojoAssociatedRemote<mojom::blink::WidgetHostInterfaceBase>
          widget_host,
      CrossVariantMojoAssociatedReceiver<mojom::blink::WidgetInterfaceBase>
          widget);
  ~WebViewFrameWidget() override;

  // WebWidget overrides:
  void Close(scoped_refptr<base::SingleThreadTaskRunner> cleanup_runner,
             base::OnceCallback<void()> cleanup_task) override;
  WebSize Size() override;
  void Resize(const WebSize&) override;
  void DidEnterFullscreen() override;
  void DidExitFullscreen() override;
  void UpdateLifecycle(WebLifecycleUpdate requested_update,
                       DocumentUpdateReason reason) override;
  void ThemeChanged() override;
  WebInputEventResult HandleInputEvent(const WebCoalescedInputEvent&) override;
  WebInputEventResult DispatchBufferedTouchEvents() override;
  void SetCursorVisibilityState(bool is_visible) override;
  void MouseCaptureLost() override;
  void SetFocus(bool) override;
  bool SelectionBounds(WebRect& anchor, WebRect& focus) const override;
  WebURL GetURLForDebugTrace() override;
  WebString GetLastToolTipTextForTesting() const override;

  // WebFrameWidget overrides:
  void DidDetachLocalFrameTree() override;
  WebInputMethodController* GetActiveWebInputMethodController() const override;
  bool ScrollFocusedEditableElementIntoView() override;
  WebHitTestResult HitTestResultAt(const gfx::PointF&) override;

  // WebFrameWidgetBase overrides:
  bool ForSubframe() const override { return false; }
  HitTestResult CoreHitTestResultAt(const gfx::PointF&) override;
  void ZoomToFindInPageRect(const WebRect& rect_in_root_frame) override;

  // FrameWidget overrides:
  void SetRootLayer(scoped_refptr<cc::Layer>) override;

  // WidgetBaseClient overrides:
  void BeginMainFrame(base::TimeTicks last_frame_time) override;
  void SetSuppressFrameRequestsWorkaroundFor704763Only(bool) final;
  void RecordStartOfFrameMetrics() override;
  void RecordEndOfFrameMetrics(
      base::TimeTicks frame_begin_time,
      cc::ActiveFrameSequenceTrackers trackers) override;
  std::unique_ptr<cc::BeginMainFrameMetrics> GetBeginMainFrameMetrics()
      override;
  void BeginUpdateLayers() override;
  void EndUpdateLayers() override;
  void DidBeginMainFrame() override;
  void ApplyViewportChanges(const cc::ApplyViewportChangesArgs& args) override;
  void RecordManipulationTypeCounts(cc::ManipulationInfo info) override;
  void SendOverscrollEventFromImplSide(
      const gfx::Vector2dF& overscroll_delta,
      cc::ElementId scroll_latched_element_id) override;
  void SendScrollEndEventFromImplSide(
      cc::ElementId scroll_latched_element_id) override;
  void BeginCommitCompositorFrame() override;
  void EndCommitCompositorFrame(base::TimeTicks commit_start_time) override;

  void Trace(Visitor*) const override;

 private:
  PageWidgetEventHandler* GetPageWidgetEventHandler() override;
  LocalFrameView* GetLocalFrameViewForAnimationScrolling() override;

  scoped_refptr<WebViewImpl> web_view_;
  base::Optional<base::TimeTicks> commit_compositor_frame_start_time_;

  SelfKeepAlive<WebViewFrameWidget> self_keep_alive_;

  DISALLOW_COPY_AND_ASSIGN(WebViewFrameWidget);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_WEB_VIEW_FRAME_WIDGET_H_
