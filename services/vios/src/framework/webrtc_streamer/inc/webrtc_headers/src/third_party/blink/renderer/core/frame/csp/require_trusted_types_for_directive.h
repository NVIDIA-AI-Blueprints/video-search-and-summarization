// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_CSP_REQUIRE_TRUSTED_TYPES_FOR_DIRECTIVE_H_
#define THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_CSP_REQUIRE_TRUSTED_TYPES_FOR_DIRECTIVE_H_

#include "third_party/blink/renderer/core/frame/csp/csp_directive.h"

namespace blink {

class ContentSecurityPolicy;

class CORE_EXPORT RequireTrustedTypesForDirective final : public CSPDirective {
 public:
  RequireTrustedTypesForDirective(const String& name,
                                  const String& value,
                                  ContentSecurityPolicy*);
  void Trace(Visitor*) const override;
  bool require() const;

 private:
  bool require_trusted_types_for_script_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_CORE_FRAME_CSP_REQUIRE_TRUSTED_TYPES_FOR_DIRECTIVE_H_
