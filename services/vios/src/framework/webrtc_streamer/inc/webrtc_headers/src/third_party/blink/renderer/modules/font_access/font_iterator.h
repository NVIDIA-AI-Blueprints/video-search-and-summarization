// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_FONT_ACCESS_FONT_ITERATOR_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_FONT_ACCESS_FONT_ITERATOR_H_

#include "third_party/blink/renderer/modules/modules_export.h"
#include "third_party/blink/renderer/platform/bindings/script_wrappable.h"

namespace blink {

class ScriptPromise;
class ScriptState;
class FontMetadata;
struct FontEnumerationEntry;

class FontIterator final : public ScriptWrappable {
  DEFINE_WRAPPERTYPEINFO();

 public:
  // Using std::vector, because at the boundary with platform code.
  explicit FontIterator(const std::vector<FontEnumerationEntry>& entries);

  ScriptPromise next(ScriptState*);

  void Trace(Visitor*) const override;

 private:
  HeapDeque<Member<FontMetadata>> entries_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_FONT_ACCESS_FONT_ITERATOR_H_
