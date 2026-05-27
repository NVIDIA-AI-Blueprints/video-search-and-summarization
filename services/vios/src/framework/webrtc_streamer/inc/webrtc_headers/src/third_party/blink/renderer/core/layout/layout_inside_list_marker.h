// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_LAYOUT_INSIDE_LIST_MARKER_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_LAYOUT_INSIDE_LIST_MARKER_H_

#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/core/layout/layout_list_marker.h"

namespace blink {

// Used to layout the list item's inside marker.
// The LayoutInsideListMarker always has to be a child of a LayoutListItem.
class CORE_EXPORT LayoutInsideListMarker final : public LayoutListMarker {
 public:
  explicit LayoutInsideListMarker(Element*);
  ~LayoutInsideListMarker() override;

  const char* GetName() const override { return "LayoutInsideListMarker"; }

 private:
  bool IsOfType(LayoutObjectType type) const override {
    return type == kLayoutObjectInsideListMarker ||
           LayoutListMarker::IsOfType(type);
  }
};

DEFINE_LAYOUT_OBJECT_TYPE_CASTS(LayoutInsideListMarker, IsInsideListMarker());

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_LAYOUT_INSIDE_LIST_MARKER_H_
