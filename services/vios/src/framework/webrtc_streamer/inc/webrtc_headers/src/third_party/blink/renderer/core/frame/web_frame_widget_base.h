// Copyright 2016 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_WEB_FRAME_WIDGET_BASE_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_WEB_FRAME_WIDGET_BASE_H_

#include "base/single_thread_task_runner.h"
#include "cc/input/event_listener_properties.h"
#include "cc/input/layer_selection_bound.h"
#include "cc/input/overscroll_behavior.h"
#include "cc/trees/layer_tree_host.h"
#include "services/network/public/mojom/referrer_policy.mojom-blink-forward.h"
#include "third_party/blink/public/common/input/web_coalesced_input_event.h"
#include "third_party/blink/public/common/input/web_gesture_device.h"
#include "third_party/blink/public/mojom/manifest/display_mode.mojom-blink.h"
#include "third_party/blink/public/mojom/page/widget.mojom-blink.h"
#include "third_party/blink/public/platform/cross_variant_mojo_util.h"
#include "third_party/blink/public/platform/web_drag_data.h"
#include "third_party/blink/public/web/web_frame_widget.h"
#include "third_party/blink/renderer/core/clipboard/data_object.h"
#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/platform/graphics/apply_viewport_changes.h"
#include "third_party/blink/renderer/platform/graphics/paint/paint_image.h"
#include "third_party/blink/renderer/platform/heap/member.h"
#include "third_party/blink/renderer/platform/mojo/heap_mojo_associated_receiver.h"
#include "third_party/blink/renderer/platform/mojo/heap_mojo_associated_remote.h"
#include "third_party/blink/renderer/platform/mojo/heap_mojo_wrapper_mode.h"
#include "third_party/blink/renderer/platform/text/text_direction.h"
#include "third_party/blink/renderer/platform/timer.h"
#include "third_party/blink/renderer/platform/widget/frame_widget.h"
#include "third_party/blink/renderer/platform/widget/widget_base_client.h"
#include "third_party/blink/renderer/platform/wtf/casting.h"

namespace gfx {
class Point;
class PointF;
}  // namespace gfx

namespace blink {
class AnimationWorkletMutatorDispatcherImpl;
class HitTestResult;
class LocalFrameView;
class Page;
class PageWidgetEventHandler;
class PaintWorkletPaintDispatcher;
class WebLocalFrameImpl;
class WebViewImpl;
class WidgetBase;

class CORE_EXPORT WebFrameWidgetBase
    : public GarbageCollected<WebFrameWidgetBase>,
      public WebFrameWidget,
      public WidgetBaseClient,
      public mojom::blink::FrameWidget,
      public FrameWidget {
 public:
  WebFrameWidgetBase(
      WebWidgetClient&,
      CrossVariantMojoAssociatedRemote<
          mojom::blink::FrameWidgetHostInterfaceBase> frame_widget_host,
      CrossVariantMojoAssociatedReceiver<mojom::blink::FrameWidgetInterfaceBase>
          frame_widget,
      CrossVariantMojoAssociatedRemote<mojom::blink::WidgetHostInterfaceBase>
          widget_host,
      CrossVariantMojoAssociatedReceiver<mojom::blink::WidgetInterfaceBase>
          widget);
  ~WebFrameWidgetBase() override;

  // Returns the WebFrame that this widget is attached to. It will be a local
  // root since only local roots have a widget attached.
  WebLocalFrameImpl* LocalRootImpl() const { return local_root_; }

  // Returns the bounding box of the block type node touched by the WebPoint.
  WebRect ComputeBlockBound(const gfx::Point& point_in_root_frame,
                            bool ignore_clipping) const;

  void BindLocalRoot(WebLocalFrame&);

  virtual bool ForSubframe() const = 0;
  virtual void IntrinsicSizingInfoChanged(
      mojom::blink::IntrinsicSizingInfoPtr) {}

  void AutoscrollStart(const gfx::PointF& position);
  void AutoscrollFling(const gfx::Vector2dF& position);
  void AutoscrollEnd();

  // Notifies RenderWidgetHostImpl that the frame widget has painted something.
  void DidMeaningfulLayout(WebMeaningfulLayout layout_type);

  // Creates or returns cached mutator dispatcher. This usually requires a
  // round trip to the compositor. The returned WeakPtr must only be
  // dereferenced on the output |mutator_task_runner|.
  base::WeakPtr<AnimationWorkletMutatorDispatcherImpl>
  EnsureCompositorMutatorDispatcher(
      scoped_refptr<base::SingleThreadTaskRunner>* mutator_task_runner);

  // TODO: consider merge the input and return value to be one parameter.
  // Creates or returns cached paint dispatcher. The returned WeakPtr must only
  // be dereferenced on the output |paint_task_runner|.
  base::WeakPtr<PaintWorkletPaintDispatcher> EnsureCompositorPaintDispatcher(
      scoped_refptr<base::SingleThreadTaskRunner>* paint_task_runner);

  virtual HitTestResult CoreHitTestResultAt(const gfx::PointF&) = 0;

  // FrameWidget implementation.
  WebWidgetClient* Client() const final { return client_; }
  cc::AnimationHost* AnimationHost() const final;
  void SetOverscrollBehavior(
      const cc::OverscrollBehavior& overscroll_behavior) final;
  void RequestAnimationAfterDelay(const base::TimeDelta&) final;
  void RegisterSelection(cc::LayerSelection selection) final;
  void RequestDecode(const cc::PaintImage&,
                     base::OnceCallback<void(bool)>) final;
  void NotifySwapAndPresentationTimeInBlink(
      WebReportTimeCallback swap_callback,
      WebReportTimeCallback presentation_callback) final;
  void RequestBeginMainFrameNotExpected(bool request) final;
  int GetLayerTreeId() final;
  void SetEventListenerProperties(cc::EventListenerClass,
                                  cc::EventListenerProperties) final;
  cc::EventListenerProperties EventListenerProperties(
      cc::EventListenerClass) const final;
  mojom::blink::DisplayMode DisplayMode() const override;
  void SetDelegatedInkMetadata(
      std::unique_ptr<viz::DelegatedInkMetadata> metadata) final;
  void DidOverscroll(const gfx::Vector2dF& overscroll_delta,
                     const gfx::Vector2dF& accumulated_overscroll,
                     const gfx::PointF& position,
                     const gfx::Vector2dF& velocity) override;
  void InjectGestureScrollEvent(WebGestureDevice device,
                                const gfx::Vector2dF& delta,
                                ui::ScrollGranularity granularity,
                                cc::ElementId scrollable_area_element_id,
                                WebInputEvent::Type injected_type) override;
  void DidChangeCursor(const ui::Cursor&) override;

  // WebFrameWidget implementation.
  WebLocalFrame* LocalRoot() const override;
  WebDragOperation DragTargetDragEnter(const WebDragData&,
                                       const gfx::PointF& point_in_viewport,
                                       const gfx::PointF& screen_point,
                                       WebDragOperationsMask operations_allowed,
                                       int modifiers) override;
  WebDragOperation DragTargetDragOver(const gfx::PointF& point_in_viewport,
                                      const gfx::PointF& screen_point,
                                      WebDragOperationsMask operations_allowed,
                                      int modifiers) override;
  void DragTargetDrop(const WebDragData&,
                      const gfx::PointF& point_in_viewport,
                      const gfx::PointF& screen_point,
                      int modifiers) override;
  void DragSourceEndedAt(const gfx::PointF& point_in_viewport,
                         const gfx::PointF& screen_point,
                         WebDragOperation) override;
  void SendOverscrollEventFromImplSide(
      const gfx::Vector2dF& overscroll_delta,
      cc::ElementId scroll_latched_element_id) override;
  void SendScrollEndEventFromImplSide(
      cc::ElementId scroll_latched_element_id) override;

  WebLocalFrame* FocusedWebLocalFrameInWidget() const override;
  void ApplyViewportChangesForTesting(
      const ApplyViewportChangesArgs& args) override;
  void NotifySwapAndPresentationTime(
      WebReportTimeCallback swap_callback,
      WebReportTimeCallback presentation_callback) override;
  scheduler::WebRenderWidgetSchedulingState* RendererWidgetSchedulingState()
      override;
  void WaitForDebuggerWhenShown() override;
  void SetTextZoomFactor(float text_zoom_factor) override;
  float TextZoomFactor() override;
  void SetMainFrameOverlayColor(SkColor) override;

  // Called when a drag-n-drop operation should begin.
  void StartDragging(network::mojom::ReferrerPolicy,
                     const WebDragData&,
                     WebDragOperationsMask,
                     const SkBitmap& drag_image,
                     const gfx::Point& drag_image_offset);

  bool DoingDragAndDrop() { return doing_drag_and_drop_; }
  static void SetIgnoreInputEvents(bool value) { ignore_input_events_ = value; }
  static bool IgnoreInputEvents() { return ignore_input_events_; }

  // WebWidget methods.
  cc::LayerTreeHost* InitializeCompositing(
      cc::TaskGraphRunner* task_graph_runner,
      const cc::LayerTreeSettings& settings,
      std::unique_ptr<cc::UkmRecorderFactory> ukm_recorder_factory) override;
  void Close(scoped_refptr<base::SingleThreadTaskRunner> cleanup_runner,
             base::OnceCallback<void()> cleanup_task) override;
  void DidAcquirePointerLock() override;
  void DidNotAcquirePointerLock() override;
  void DidLosePointerLock() override;
  void ShowContextMenu(WebMenuSourceType) override;
  void SetCompositorVisible(bool visible) override;
  void SetDisplayMode(mojom::blink::DisplayMode) override;
  void SetCursor(const ui::Cursor& cursor) override;
  bool HandlingInputEvent() override;
  void SetHandlingInputEvent(bool handling) override;
  void ProcessInputEventSynchronously(const WebCoalescedInputEvent&,
                                      HandledEventCallback) override;

  // WidgetBaseClient methods.
  void DispatchRafAlignedInput(base::TimeTicks frame_time) override;
  void RecordTimeToFirstActivePaint(base::TimeDelta duration) override;
  void EndCommitCompositorFrame(base::TimeTicks commit_start_time) override;
  void DidCommitAndDrawCompositorFrame() override;
  void OnDeferMainFrameUpdatesChanged(bool defer) override;
  void OnDeferCommitsChanged(bool defer) override;
  void RequestNewLayerTreeFrameSink(
      LayerTreeFrameSinkCallback callback) override;
  void DidCompletePageScaleAnimation() override;
  void DidObserveFirstScrollDelay(base::TimeDelta first_scroll_delay) override;
  void DidBeginMainFrame() override;
  void WillBeginMainFrame() override;
  void SubmitThroughputData(ukm::SourceId source_id,
                            int aggregated_percent,
                            int impl_percent,
                            base::Optional<int> main_percent) override;
  void FocusChangeComplete() override;
  void ShowVirtualKeyboard() override;
  void UpdateTextInputState() override;
  bool WillHandleGestureEvent(const WebGestureEvent& event) override;
  bool WillHandleMouseEvent(const WebMouseEvent& event) override;
  void ObserveGestureEventAndResult(
      const WebGestureEvent& gesture_event,
      const gfx::Vector2dF& unused_delta,
      const cc::OverscrollBehavior& overscroll_behavior,
      bool event_processed) override;
  bool SupportsBufferedTouchEvents() override { return true; }
  void DidHandleKeyEvent() override;
  void QueueSyntheticEvent(
      std::unique_ptr<blink::WebCoalescedInputEvent>) override;
  WebTextInputType GetTextInputType() override;
  void GetWidgetInputHandler(
      mojo::PendingReceiver<mojom::blink::WidgetInputHandler> request,
      mojo::PendingRemote<mojom::blink::WidgetInputHandlerHost> host) override;
  void ScheduleAnimationForWebTests() override;

  // mojom::blink::FrameWidget methods.
  void DragTargetDragLeave(const gfx::PointF& point_in_viewport,
                           const gfx::PointF& screen_point) override;
  void DragSourceSystemDragEnded() override;
  void SetBackgroundOpaque(bool opaque) override;

  // Changes the text direction of the currently selected input field (if any).
  void SetTextDirection(
      mojo_base::mojom::blink::TextDirection direction) override {}

  // Sets the inherited effective touch action on an out-of-process iframe.
  void SetInheritedEffectiveTouchActionForSubFrame(
      WebTouchAction touch_action) override {}

  // Toggles render throttling for an out-of-process iframe. Local frames are
  // throttled based on their visibility in the viewport, but remote frames
  // have to have throttling information propagated from parent to child
  // across processes.
  void UpdateRenderThrottlingStatusForSubFrame(
      bool is_throttled,
      bool subtree_throttled) override {}

  // Sets the inert bit on an out-of-process iframe, causing it to ignore
  // input.
  void SetIsInertForSubFrame(bool inert) override {}

  // Called when the FrameView for this Widget's local root is created.
  virtual void DidCreateLocalRootView() {}

  // This method returns the focused frame belonging to this WebWidget, that
  // is, a focused frame with the same local root as the one corresponding
  // to this widget. It will return nullptr if no frame is focused or, the
  // focused frame has a different local root.
  LocalFrame* FocusedLocalFrameInWidget() const;

  virtual void Trace(Visitor*) const;

  // For when the embedder itself change scales on the page (e.g. devtools)
  // and wants all of the content at the new scale to be crisp
  void SetNeedsRecalculateRasterScales();

  // Sets the background color to be filled in as gutter behind/around the
  // painted content. Non-composited WebViews need not implement this, as they
  // paint into another widget which has a background color of its own.
  void SetBackgroundColor(SkColor color);

  // Starts an animation of the page scale to a target scale factor and scroll
  // offset.
  // If use_anchor is true, destination is a point on the screen that will
  // remain fixed for the duration of the animation.
  // If use_anchor is false, destination is the final top-left scroll position.
  void StartPageScaleAnimation(const gfx::Vector2d& destination,
                               bool use_anchor,
                               float new_page_scale,
                               base::TimeDelta duration);

  // Called to update if scroll events should be sent.
  void SetHaveScrollEventHandlers(bool);

  // Start deferring commits to the compositor, allowing document lifecycle
  // updates without committing the layer tree. Commits are deferred
  // until at most the given |timeout| has passed. If multiple calls are made
  // when deferral is active then the initial timeout applies.
  void StartDeferringCommits(base::TimeDelta timeout);
  // Immediately stop deferring commits.
  void StopDeferringCommits(cc::PaintHoldingCommitTrigger);

  // Prevents any updates to the input for the layer tree, and the layer tree
  // itself, and the layer tree from becoming visible.
  std::unique_ptr<cc::ScopedDeferMainFrameUpdate> DeferMainFrameUpdate();

  // Sets the amount that the top and bottom browser controls are showing, from
  // 0 (hidden) to 1 (fully shown).
  void SetBrowserControlsShownRatio(float top_ratio, float bottom_ratio);

  // Set browser controls params. These params consist of top and bottom
  // heights, min-heights, browser_controls_shrink_blink_size, and
  // animate_browser_controls_height_changes. If
  // animate_browser_controls_height_changes is set to true, changes to the
  // browser controls height will be animated. If
  // browser_controls_shrink_blink_size is set to true, then Blink shrunk the
  // viewport clip layers by the top and bottom browser controls height. Top
  // controls will translate the web page down and do not immediately scroll
  // when hiding. The bottom controls scroll immediately and never translate the
  // content (only clip it).
  void SetBrowserControlsParams(cc::BrowserControlsParams params);

  cc::LayerTreeDebugState GetLayerTreeDebugState();
  void SetLayerTreeDebugState(const cc::LayerTreeDebugState& state);

  // Ask compositor to composite a frame for testing. This will generate a
  // BeginMainFrame, and update the document lifecycle.
  void SynchronouslyCompositeForTesting(base::TimeTicks frame_time);

  void SetToolTipText(const String& tooltip_text, TextDirection dir);

  void ShowVirtualKeyboardOnElementFocus();
  void ProcessTouchAction(WebTouchAction touch_action);

  // Called when a gesture event has been processed.
  void DidHandleGestureEvent(const WebGestureEvent& event,
                             bool event_cancelled);

 protected:
  enum DragAction { kDragEnter, kDragOver };

  // Consolidate some common code between starting a drag over a target and
  // updating a drag over a target. If we're starting a drag, |isEntering|
  // should be true.
  WebDragOperation DragTargetDragEnterOrOver(
      const gfx::PointF& point_in_viewport,
      const gfx::PointF& screen_point,
      DragAction,
      int modifiers);

  // Helper function to call VisualViewport::viewportToRootFrame().
  gfx::PointF ViewportToRootFrame(const gfx::PointF& point_in_viewport) const;

  WebViewImpl* View() const;

  // Returns the page object associated with this widget. This may be null when
  // the page is shutting down, but will be valid at all other times.
  Page* GetPage() const;

  mojom::blink::FrameWidgetHost* GetAssociatedFrameWidgetHost() const;

  // Helper function to process events while pointer locked.
  void PointerLockMouseEvent(const WebCoalescedInputEvent&);

  virtual PageWidgetEventHandler* GetPageWidgetEventHandler() = 0;

  // Return the LocalFrameView used for animation scrolling. This is overridden
  // by WebViewFrameWidget and should eventually be removed once null does not
  // need to be passed for the main frame.
  virtual LocalFrameView* GetLocalFrameViewForAnimationScrolling() = 0;

  // A copy of the web drop data object we received from the browser.
  Member<DataObject> current_drag_data_;

  bool doing_drag_and_drop_ = false;

  // The available drag operations (copy, move link...) allowed by the source.
  WebDragOperation operations_allowed_ = kWebDragOperationNone;

  // The current drag operation as negotiated by the source and destination.
  // When not equal to DragOperationNone, the drag data can be dropped onto the
  // current drop target in this WebView (the drop target can accept the drop).
  WebDragOperation drag_operation_ = kWebDragOperationNone;

  // Base functionality all widgets have. This is a member as to avoid
  // complicated inheritance structures.
  std::unique_ptr<WidgetBase> widget_base_;

 private:
  void CancelDrag();
  void RequestAnimationAfterDelayTimerFired(TimerBase*);
  void PresentationCallbackForMeaningfulLayout(blink::WebSwapResult,
                                               base::TimeTicks);

  static bool ignore_input_events_;

  WebWidgetClient* client_;

  // WebFrameWidget is associated with a subtree of the frame tree,
  // corresponding to a maximal connected tree of LocalFrames. This member
  // points to the root of that subtree.
  Member<WebLocalFrameImpl> local_root_;

  mojom::blink::DisplayMode display_mode_;

  // This is owned by the LayerTreeHostImpl, and should only be used on the
  // compositor thread, so we keep the TaskRunner where you post tasks to
  // make that happen.
  base::WeakPtr<AnimationWorkletMutatorDispatcherImpl> mutator_dispatcher_;
  scoped_refptr<base::SingleThreadTaskRunner> mutator_task_runner_;

  // The |paint_dispatcher_| should only be dereferenced on the
  // |paint_task_runner_| (in practice this is the compositor thread). We keep a
  // copy of it here to provide to new PaintWorkletProxyClient objects (which
  // run on the worklet thread) so that they can talk to the
  // PaintWorkletPaintDispatcher on the compositor thread.
  base::WeakPtr<PaintWorkletPaintDispatcher> paint_dispatcher_;
  scoped_refptr<base::SingleThreadTaskRunner> paint_task_runner_;

  std::unique_ptr<TaskRunnerTimer<WebFrameWidgetBase>>
      request_animation_after_delay_timer_;

  // WebFrameWidgetBase is not tied to ExecutionContext
  HeapMojoAssociatedRemote<mojom::blink::FrameWidgetHost,
                           HeapMojoWrapperMode::kWithoutContextObserver>
      frame_widget_host_{nullptr};
  // WebFrameWidgetBase is not tied to ExecutionContext
  HeapMojoAssociatedReceiver<mojom::blink::FrameWidget,
                             WebFrameWidgetBase,
                             HeapMojoWrapperMode::kWithoutContextObserver>
      receiver_{this, nullptr};

  // Different consumers in the browser process makes different assumptions, so
  // must always send the first IPC regardless of value.
  base::Optional<bool> has_touch_handlers_;

  friend class WebViewImpl;
  friend class ReportTimeSwapPromise;
};

template <>
struct DowncastTraits<WebFrameWidgetBase> {
  // All concrete implementations of WebFrameWidget are derived from
  // WebFrameWidgetBase.
  static bool AllowFrom(const WebFrameWidget& widget) { return true; }
};

}  // namespace blink

#endif
