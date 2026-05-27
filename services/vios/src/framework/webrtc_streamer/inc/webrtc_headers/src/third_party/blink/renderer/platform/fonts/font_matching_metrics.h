// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_PLATFORM_FONTS_FONT_MATCHING_METRICS_H_
#define THIRD_PARTY_BLINK_RENDERER_PLATFORM_FONTS_FONT_MATCHING_METRICS_H_

#include "services/metrics/public/cpp/ukm_source_id.h"
#include "third_party/blink/renderer/platform/platform_export.h"
#include "third_party/blink/renderer/platform/wtf/hash_set.h"
#include "third_party/blink/renderer/platform/wtf/text/atomic_string.h"
#include "third_party/blink/renderer/platform/wtf/text/atomic_string_hash.h"

namespace ukm {
class UkmRecorder;
}  // namespace ukm

namespace blink {

// Tracks and reports UKM metrics of the number of attempted font family match
// attempts (both successful and not successful) by the current frame.
//
// Only the number of successful / not successful font family match attempts are
// reported to UKM. The class de-dupes attempts to match the same font family
// name such that they are counted as one attempt.
class PLATFORM_EXPORT FontMatchingMetrics {
 public:
  FontMatchingMetrics(bool top_level,
                      ukm::UkmRecorder* ukm_recorder,
                      ukm::SourceId source_id);

  // Called when a page attempts to match a font family, and the font family is
  // available.
  void ReportSuccessfulFontFamilyMatch(const AtomicString& font_family_name);

  // Called when a page attempts to match a font family, and the font family is
  // not available.
  void ReportFailedFontFamilyMatch(const AtomicString& font_family_name);

  // Called when a page attempts to match a system font family.
  void ReportSystemFontFamily(const AtomicString& font_family_name);

  // Called when a page attempts to match a web font family.
  void ReportWebFontFamily(const AtomicString& font_family_name);

  // Reports a font listed in a @font-face src:local rule that successfully
  // matched.
  void ReportSuccessfulLocalFontMatch(const AtomicString& font_name);

  // Reports a font listed in a @font-face src:local rule that didn't
  // successfully match.
  void ReportFailedLocalFontMatch(const AtomicString& font_name);

  // Publishes the number of font family matches attempted (both successful and
  // otherwise) to UKM. Called at page unload.
  void PublishUkmMetrics();

 private:
  // Font family names successfully matched.
  HashSet<AtomicString> successful_font_families_;

  // Font family names that weren't successfully matched.
  HashSet<AtomicString> failed_font_families_;

  // System font families the page attempted to match.
  HashSet<AtomicString> system_font_families_;

  // Web font families the page attempted to match.
  HashSet<AtomicString> web_font_families_;

  // @font-face src:local fonts that successfully matched.
  HashSet<AtomicString> local_fonts_succeeded_;

  // @font-face src:local fonts that didn't successfully match.
  HashSet<AtomicString> local_fonts_failed_;

  // True if this FontMatchingMetrics instance is for a top-level frame, false
  // otherwise.
  const bool top_level_ = false;

  ukm::UkmRecorder* const ukm_recorder_;
  const ukm::SourceId source_id_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_PLATFORM_FONTS_FONT_MATCHING_METRICS_H_
