// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_PAINT_GRAPHICS_LAYER_DISPLAY_ITEM_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_PAINT_GRAPHICS_LAYER_DISPLAY_ITEM_H_

#include "cc/layers/layer.h"
#include "third_party/blink/renderer/platform/graphics/paint/display_item.h"
#include "third_party/blink/renderer/platform/graphics/paint/property_tree_state.h"
#include "third_party/blink/renderer/platform/platform_export.h"

namespace blink {

class GraphicsContext;
class GraphicsLayer;

// Before CompositeAfterPaint, this is a placeholder for a GraphicsLayer
// allocated before paint. With CompositeAfterPaint, this class will be
// obsolete and should be removed.
//
// Before SquashAfterPaint, a cc::Layer will always be allocated to draw the
// GraphicsLayer's contents. With SquashAfter Paint, that may not be true; the
// GraphicsLayer may end up getting squashed by PaintArtifactCompositor.
class PLATFORM_EXPORT GraphicsLayerDisplayItem final : public DisplayItem {
 public:
  GraphicsLayerDisplayItem(const GraphicsLayer&);

  const GraphicsLayer& GetGraphicsLayer() const { return graphics_layer_; }

  // DisplayItem
  bool Equals(const DisplayItem&) const override;
#if DCHECK_IS_ON()
  void PropertiesAsJSON(JSONObject&) const override;
#endif

 private:
  const GraphicsLayer& graphics_layer_;
};

// Records a graphics layer into a GraphicsContext.
PLATFORM_EXPORT void RecordGraphicsLayer(GraphicsContext& context,
                                         const GraphicsLayer& graphics_layer);

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_GRAPHICS_PAINT_GRAPHICS_LAYER_DISPLAY_ITEM_H_
