// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

/*
 * Copyright © 1991-2015 Unicode, Inc. All rights reserved.
 * Distributed under the Terms of Use in
 * http://www.unicode.org/copyright.html.
 *
 * Permission is hereby granted, free of charge, to any person obtaining
 * a copy of the Unicode data files and any associated documentation
 * (the "Data Files") or Unicode software and any associated documentation
 * (the "Software") to deal in the Data Files or Software
 * without restriction, including without limitation the rights to use,
 * copy, modify, merge, publish, distribute, and/or sell copies of
 * the Data Files or Software, and to permit persons to whom the Data Files
 * or Software are furnished to do so, provided that
 * (a) this copyright and permission notice appear with all copies
 * of the Data Files or Software,
 * (b) this copyright and permission notice appear in associated
 * documentation, and
 * (c) there is clear notice in each modified Data File or in the Software
 * as well as in the documentation associated with the Data File(s) or
 * Software that the data or software has been modified.
 *
 * THE DATA FILES AND SOFTWARE ARE PROVIDED "AS IS", WITHOUT WARRANTY OF
 * ANY KIND, EXPRESS OR IMPLIED, INCLUDING BUT NOT LIMITED TO THE
 * WARRANTIES OF MERCHANTABILITY, FITNESS FOR A PARTICULAR PURPOSE AND
 * NONINFRINGEMENT OF THIRD PARTY RIGHTS.
 * IN NO EVENT SHALL THE COPYRIGHT HOLDER OR HOLDERS INCLUDED IN THIS
 * NOTICE BE LIABLE FOR ANY CLAIM, OR ANY SPECIAL INDIRECT OR CONSEQUENTIAL
 * DAMAGES, OR ANY DAMAGES WHATSOEVER RESULTING FROM LOSS OF USE,
 * DATA OR PROFITS, WHETHER IN AN ACTION OF CONTRACT, NEGLIGENCE OR OTHER
 * TORTIOUS ACTION, ARISING OUT OF OR IN CONNECTION WITH THE USE OR
 * PERFORMANCE OF THE DATA FILES OR SOFTWARE.
 *
 * Except as contained in this notice, the name of a copyright holder
 * shall not be used in advertising or otherwise to promote the sale,
 * use or other dealings in these Data Files or Software without prior
 * written authorization of the copyright holder.
 */

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_INTERNAL_IS_UTF8_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_INTERNAL_IS_UTF8_H_

#include <cstdint>

#include "absl/base/attributes.h"
#include "absl/strings/string_view.h"

namespace rs_std::internal {

// Returns whether the given character is ASCII.
constexpr bool IsAscii(char c) {
  // We explicitly use `unsigned char` because the comparison would always be
  // true if `char` is signed.
  constexpr unsigned char kMaxAscii = '\u007f';
  return static_cast<unsigned char>(c) <= kMaxAscii;
}

// Returns whether the given string is all ASCII.
constexpr bool IsAscii(absl::string_view str) {
  for (const char c : str) {
    if (!IsAscii(c)) {
      return false;
    }
  }
  return true;
}

// A mapping from the initial byte of a UTF-8 encoded code point to the number
// of bytes in the code point.
//
// See https://tools.ietf.org/html/rfc3629
// clang-format off
// NOLINTNEXTLINE(clang-diagnostic-unsafe-buffer-usage): Indexed with uint8_t.
static constexpr const uint8_t kFirstByteToUtf8CharSize[256] = {
    // 1  2  3  4  5  6  7  8  9  A  B  C  D  E  F
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  // 0
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  // 1
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  // 2
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  // 3
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  // 4
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  // 5
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  // 6
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1,  // 7
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  // 8
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  // 9
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  // A
    0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  // B
    0, 0, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,  // C
    2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2, 2,  // D
    3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3,  // E
    4, 4, 4, 4, 4, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,  // F
};
// clang-format on

// Returns whether the given string is a valid single UTF-8 code point.
//
// The caller must first ensure that `str`'s length matches the length
// specified by the first byte.
constexpr bool IsUtf8Char(absl::string_view str) {
  // 2-byte encoding is for codepoints  \u{0080} to  \u{07ff}
  //        first  C2 80        last DF BF
  // 3-byte encoding is for codepoints  \u{0800} to  \u{ffff}
  //        first  E0 A0 80     last EF BF BF
  //   excluding surrogates codepoints  \u{d800} to  \u{dfff}
  //               ED A0 80 to       ED BF BF
  // 4-byte encoding is for codepoints \u{10000} to \u{10ffff}
  //        first  F0 90 80 80  last F4 8F BF BF
  //
  // Use the UTF-8 syntax from the RFC
  //
  // https://tools.ietf.org/html/rfc3629
  // UTF8-1      = %x00-7F
  // UTF8-2      = %xC2-DF UTF8-tail
  // UTF8-3      = %xE0 %xA0-BF UTF8-tail / %xE1-EC 2( UTF8-tail ) /
  //               %xED %x80-9F UTF8-tail / %xEE-EF 2( UTF8-tail )
  // UTF8-4      = %xF0 %x90-BF 2( UTF8-tail ) / %xF1-F3 3( UTF8-tail ) /
  //               %xF4 %x80-8F 2( UTF8-tail )
  //
  // References:
  // - Rust stdlib:
  // https://github.com/rust-lang/rust/blob/48994b1674b3212d27b5e83841c0966bc2b4be43/library/core/src/str/validations.rs#L185-L213
  // - LLVM:
  // https://github.com/llvm/llvm-project/blob/357306572d4734a75e649284b4808299d0aba9c8/llvm/lib/Support/ConvertUTF.cpp#L397-L420
  switch (str.size()) {
    // 0 or >4 bytes lengths are invalid.
    default:
      return false;

      // We explicitly use `unsigned char` in the following because some of the
      // comparisons would otherwise be tautological if `char` is signed.
      // (For example, `fourth` > 0xBF is always false if `fourth` is a
      // `signed char`.)

    case 4: {
      unsigned char fourth = str[3];
      if (fourth < 0x80 || fourth > 0xBF) {
        return false;
      }
      ABSL_FALLTHROUGH_INTENDED;
    }
    case 3: {
      unsigned char third = str[2];
      if (third < 0x80 || third > 0xBF) {
        return false;
      }
      ABSL_FALLTHROUGH_INTENDED;
    }
    case 2: {
      unsigned char second = str[1];
      if (second < 0x80 || second > 0xBF) {
        return false;
      }
      switch (str[0]) {
        case 0xE0: {
          if (second < 0xA0) {
            return false;
          }
          break;
        }
        case 0xED: {
          if (second > 0x9F) {
            return false;
          }
          break;
        }
        case 0xF0: {
          if (second < 0x90) {
            return false;
          }
          break;
        }
        case 0xF4: {
          if (second > 0x8F) {
            return false;
          }
          break;
        }
        default: {
          if (second < 0x80) {
            return false;
          }
        }
      }
      ABSL_FALLTHROUGH_INTENDED;
    }
    case 1: {
      unsigned char first = str[0];
      if (first >= 0x80 && first < 0xC2) {
        return false;
      }
      if (first > 0xF4) {
        return false;
      }
    }
  }
  return true;
}

// Returns whether the given string is a valid UTF-8.
constexpr bool IsUtf8(absl::string_view str) {
  // ASCII fast-path.
  if (IsAscii(str)) {
    return true;
  }
  while (!str.empty()) {
    // Cast to `unsigned char` to ensure correctness if char is signed.
    const uint8_t char_size =
        kFirstByteToUtf8CharSize[static_cast<unsigned char>(str[0])];
    if (char_size > str.size()) {
      return false;
    }
    const absl::string_view char_view = str.substr(0, char_size);
    if (!IsUtf8Char(char_view)) {
      return false;
    }
    str = str.substr(char_size);
  }
  return true;
}

}  // namespace rs_std::internal

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_INTERNAL_IS_UTF8_H_
