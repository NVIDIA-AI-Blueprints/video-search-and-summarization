// Copyright 2017 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_NG_INLINE_NG_TEXT_FRAGMENT_BUILDER_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_NG_INLINE_NG_TEXT_FRAGMENT_BUILDER_H_

#include "third_party/blink/renderer/core/layout/geometry/logical_size.h"
#include "third_party/blink/renderer/core/layout/ng/inline/ng_inline_node.h"
#include "third_party/blink/renderer/core/layout/ng/inline/ng_physical_text_fragment.h"
#include "third_party/blink/renderer/core/layout/ng/ng_fragment_builder.h"
#include "third_party/blink/renderer/platform/wtf/allocator/allocator.h"

namespace blink {

class LayoutObject;
class ShapeResultView;

class CORE_EXPORT NGTextFragmentBuilder final : public NGFragmentBuilder {
  STACK_ALLOCATED();

 public:
  NGTextFragmentBuilder(WritingMode writing_mode)
      : NGFragmentBuilder(writing_mode, TextDirection::kLtr) {}

  NGTextFragmentBuilder(const NGPhysicalTextFragment& fragment);

  TextDirection ResolvedDirection() const { return resolved_direction_; }

  // NOTE: Takes ownership of the shape result within the item result.
  void SetItem(const String& text_content,
               const NGInlineItem& inline_item,
               scoped_refptr<const ShapeResultView> shape_result,
               const NGTextOffset& text_offset,
               const LogicalSize& size);

  // Set text for generated text, e.g. hyphen and ellipsis.
  void SetText(LayoutObject*,
               const String& text,
               scoped_refptr<const ComputedStyle>,
               bool is_ellipsis_style,
               scoped_refptr<const ShapeResultView>);

  // Creates the fragment. Can only be called once.
  scoped_refptr<const NGPhysicalTextFragment> ToTextFragment();

 private:
  String text_;
  NGTextOffset text_offset_;
  scoped_refptr<const ShapeResultView> shape_result_;

  NGTextType text_type_ = NGTextType::kNormal;

  // Set from |NGInlineItem| by |SetItem()|.
  TextDirection resolved_direction_ = TextDirection::kLtr;

  friend class NGPhysicalTextFragment;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_LAYOUT_NG_INLINE_NG_TEXT_FRAGMENT_BUILDER_H_
