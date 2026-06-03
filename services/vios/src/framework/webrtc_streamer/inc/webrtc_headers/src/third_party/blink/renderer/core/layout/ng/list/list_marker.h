// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_NG_LIST_LIST_MARKER_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_NG_LIST_LIST_MARKER_H_

#include "third_party/blink/renderer/core/core_export.h"
#include "third_party/blink/renderer/core/layout/layout_object.h"
#include "third_party/blink/renderer/core/layout/ng/list/layout_ng_list_item.h"

namespace blink {

// This class holds code shared among LayoutNG classes for list markers.
class CORE_EXPORT ListMarker {
  friend class LayoutNGListItem;

 public:
  explicit ListMarker();

  static const ListMarker* Get(const LayoutObject*);
  static ListMarker* Get(LayoutObject*);

  LayoutNGListItem* ListItem(const LayoutObject&) const;

  String MarkerTextWithSuffix(const LayoutObject&) const;
  String MarkerTextWithoutSuffix(const LayoutObject&) const;

  // Marker text with suffix, e.g. "1. ", for use in accessibility.
  String TextAlternative(const LayoutObject&) const;

  bool IsMarkerImage(const LayoutObject& marker) const {
    DCHECK_EQ(Get(&marker), this);
    return ListItem(marker)->StyleRef().GeneratesMarkerImage();
  }

  void UpdateMarkerTextIfNeeded(LayoutObject& marker) {
    DCHECK_EQ(Get(&marker), this);
    if (marker_text_type_ == kUnresolved)
      UpdateMarkerText(marker);
  }
  void UpdateMarkerContentIfNeeded(LayoutObject&);

  void OrdinalValueChanged(LayoutObject&);

  LayoutObject* SymbolMarkerLayoutText(const LayoutObject&) const;

 private:
  enum MarkerTextFormat { kWithSuffix, kWithoutSuffix };
  enum MarkerTextType {
    kNotText,  // The marker doesn't have a LayoutText, either because it has
               // not been created yet or because 'list-style-type' is 'none',
               // 'list-style-image' is not 'none', or 'content' is not
               // 'normal'.
    kUnresolved,    // The marker has a LayoutText that needs to be updated.
    kOrdinalValue,  // The marker text depends on the ordinal.
    kStatic,        // The marker text doesn't depend on the ordinal.
    kSymbolValue,   // Like kStatic, but the marker is painted as a symbol.
  };
  MarkerTextType MarkerText(const LayoutObject&,
                            StringBuilder*,
                            MarkerTextFormat) const;
  void UpdateMarkerText(LayoutObject&);
  void UpdateMarkerText(LayoutObject&, LayoutText*);

  void ListStyleTypeChanged(LayoutObject&);

  unsigned marker_text_type_ : 3;  // MarkerTextType
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_NG_LIST_LIST_MARKER_H_
