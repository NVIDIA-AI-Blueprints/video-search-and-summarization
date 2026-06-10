// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_DISPLAY_LOCK_RENDER_SUBTREE_ACTIVATION_EVENT_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_DISPLAY_LOCK_RENDER_SUBTREE_ACTIVATION_EVENT_H_

#include "third_party/blink/renderer/core/dom/events/event.h"

namespace blink {

class Element;

class RenderSubtreeActivationEvent : public Event {
  DEFINE_WRAPPERTYPEINFO();

 public:
  explicit RenderSubtreeActivationEvent(Element& activated_element);

  Element& activatedElement() const { return *activated_element_.Get(); }

  const AtomicString& InterfaceName() const override;

  void Trace(Visitor*) const override;

 private:
  Member<Element> activated_element_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_DISPLAY_LOCK_RENDER_SUBTREE_ACTIVATION_EVENT_H_
