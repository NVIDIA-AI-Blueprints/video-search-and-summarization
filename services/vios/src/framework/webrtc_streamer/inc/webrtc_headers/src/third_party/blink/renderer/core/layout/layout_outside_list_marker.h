// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_LAYOUT_OUTSIDE_LIST_MARKER_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_LAYOUT_OUTSIDE_LIST_MARKER_H_

#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/core/layout/layout_list_marker.h"

namespace blink {

// Used to layout the list item's outside marker.
// The LayoutOutsideListMarker always has to be a child of a LayoutListItem.
class CORE_EXPORT LayoutOutsideListMarker final : public LayoutListMarker {
 public:
  explicit LayoutOutsideListMarker(Element*);
  ~LayoutOutsideListMarker() override;

  const char* GetName() const override { return "LayoutOutsideListMarker"; }

 private:
  bool IsOfType(LayoutObjectType type) const override {
    return type == kLayoutObjectOutsideListMarker ||
           LayoutListMarker::IsOfType(type);
  }
};

DEFINE_LAYOUT_OBJECT_TYPE_CASTS(LayoutOutsideListMarker, IsOutsideListMarker());

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_LAYOUT_OUTSIDE_LIST_MARKER_H_
