#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_TESTING_STUB_GRAPHICS_LAYER_CLIENT_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_TESTING_STUB_GRAPHICS_LAYER_CLIENT_H_

#include "third_party/blink/renderer/platform/graphics/graphics_layer_client.h"

namespace blink {

class StubGraphicsLayerClient : public GraphicsLayerClient {
 public:
  StubGraphicsLayerClient() = default;
  ~StubGraphicsLayerClient() override = default;

  // GraphicsLayerClient implementation.
  IntRect ComputeInterestRect(
      const GraphicsLayer*,
      const IntRect& previous_interest_rect) const override {
    return IntRect();
  }
  LayoutSize SubpixelAccumulation() const override { return LayoutSize(); }
  bool NeedsRepaint(const GraphicsLayer&) const override { return false; }
  void PaintContents(const GraphicsLayer*,
                     GraphicsContext&,
                     GraphicsLayerPaintingPhase,
                     const IntRect& interest_rect) const override {}
  bool ShouldThrottleRendering() const override { return false; }
  bool IsTrackingRasterInvalidations() const override { return false; }
  String DebugName(const GraphicsLayer*) const override { return "DebugName"; }
#if DCHECK_IS_ON()
  void VerifyNotPainting() override {}
#endif
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_TESTING_STUB_GRAPHICS_LAYER_CLIENT_H_
