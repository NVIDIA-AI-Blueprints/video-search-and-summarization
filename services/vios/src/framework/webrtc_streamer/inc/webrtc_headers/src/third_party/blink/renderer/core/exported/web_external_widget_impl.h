// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_EXPORTED_WEB_EXTERNAL_WIDGET_IMPL_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_EXPORTED_WEB_EXTERNAL_WIDGET_IMPL_H_

#include "third_party/blink/public/web/web_external_widget.h"

#include "third_party/blink/public/mojom/page/widget.mojom-blink.h"
#include "third_party/blink/public/platform/cross_variant_mojo_util.h"
#include "third_party/blink/public/platform/web_url.h"
#include "third_party/blink/renderer/platform/widget/widget_base_client.h"

namespace blink {
class WidgetBase;

class WebExternalWidgetImpl : public WebExternalWidget,
                              public WidgetBaseClient {
 public:
  WebExternalWidgetImpl(
      WebExternalWidgetClient* client,
      const WebURL& debug_url,
      CrossVariantMojoAssociatedRemote<mojom::blink::WidgetHostInterfaceBase>
          widget_host,
      CrossVariantMojoAssociatedReceiver<mojom::blink::WidgetInterfaceBase>
          widget);
  ~WebExternalWidgetImpl() override;

  // WebWidget overrides:
  cc::LayerTreeHost* InitializeCompositing(
      cc::TaskGraphRunner* task_graph_runner,
      const cc::LayerTreeSettings& settings,
      std::unique_ptr<cc::UkmRecorderFactory> ukm_recorder_factory) override;
  void SetCompositorVisible(bool visible) override;
  void Close(scoped_refptr<base::SingleThreadTaskRunner> cleanup_runner,
             base::OnceCallback<void()> cleanup_task) override;
  WebHitTestResult HitTestResultAt(const gfx::PointF&) override;
  WebURL GetURLForDebugTrace() override;
  WebSize Size() override;
  void Resize(const WebSize& size) override;
  WebInputEventResult HandleInputEvent(
      const WebCoalescedInputEvent& coalesced_event) override;
  WebInputEventResult DispatchBufferedTouchEvents() override;
  scheduler::WebRenderWidgetSchedulingState* RendererWidgetSchedulingState()
      override;
  void SetCursor(const ui::Cursor& cursor) override;
  bool HandlingInputEvent() override;
  void SetHandlingInputEvent(bool handling) override;
  void ProcessInputEventSynchronously(const WebCoalescedInputEvent&,
                                      HandledEventCallback) override;
  void DidOverscrollForTesting(
      const gfx::Vector2dF& overscroll_delta,
      const gfx::Vector2dF& accumulated_overscroll,
      const gfx::PointF& position_in_viewport,
      const gfx::Vector2dF& velocity_in_viewport) override;

  // WebExternalWidget overrides:
  void SetRootLayer(scoped_refptr<cc::Layer>) override;

  // WidgetBaseClient overrides:
  void DispatchRafAlignedInput(base::TimeTicks frame_time) override {}
  void BeginMainFrame(base::TimeTicks last_frame_time) override {}
  void RecordTimeToFirstActivePaint(base::TimeDelta duration) override;
  void UpdateLifecycle(WebLifecycleUpdate requested_update,
                       DocumentUpdateReason reason) override {}
  void RequestNewLayerTreeFrameSink(
      LayerTreeFrameSinkCallback callback) override;
  void DidCommitAndDrawCompositorFrame() override;
  bool WillHandleGestureEvent(const WebGestureEvent& event) override;
  bool WillHandleMouseEvent(const WebMouseEvent& event) override;
  void ObserveGestureEventAndResult(
      const WebGestureEvent& gesture_event,
      const gfx::Vector2dF& unused_delta,
      const cc::OverscrollBehavior& overscroll_behavior,
      bool event_processed) override;
  bool SupportsBufferedTouchEvents() override;
  void DidHandleKeyEvent() override;
  void QueueSyntheticEvent(
      std::unique_ptr<blink::WebCoalescedInputEvent>) override;
  void GetWidgetInputHandler(
      mojo::PendingReceiver<mojom::blink::WidgetInputHandler> request,
      mojo::PendingRemote<mojom::blink::WidgetInputHandlerHost> host) override;

 private:
  WebExternalWidgetClient* const client_;
  const WebURL debug_url_;
  WebSize size_;
  std::unique_ptr<WidgetBase> widget_base_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_EXPORTED_WEB_EXTERNAL_WIDGET_IMPL_H_
