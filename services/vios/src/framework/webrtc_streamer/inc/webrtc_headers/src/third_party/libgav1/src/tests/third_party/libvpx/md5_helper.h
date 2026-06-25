/*
 *  Copyright (c) 2012 The WebM project authors. All Rights Reserved.
 *
 *  Use of this source code is governed by a BSD-style license
 *  that can be found in the LICENSE file in the root of the source
 *  tree. An additional intellectual property rights grant can be found
 *  in the file PATENTS.  All contributing project authors may
 *  be found in the AUTHORS file in the root of the source tree.
 */

#ifndef LIBGAV1_TESTS_THIRD_PARTY_LIBVPX_MD5_HELPER_H_
#define LIBGAV1_TESTS_THIRD_PARTY_LIBVPX_MD5_HELPER_H_

#include <cstddef>
#include <cstdint>

#include "tests/third_party/libvpx/md5_utils.h"

namespace libvpx_test {
class MD5 {
 public:
  MD5() { MD5Init(&md5_); }

  void Add(const uint8_t *data, size_t size) {
    MD5Update(&md5_, data, static_cast<uint32_t>(size));
  }

  const char *Get(void) {
    static const char hex[16] = {
        '0', '1', '2', '3', '4', '5', '6', '7',
        '8', '9', 'a', 'b', 'c', 'd', 'e', 'f',
    };
    uint8_t tmp[16];
    MD5Context ctx_tmp = md5_;

    MD5Final(tmp, &ctx_tmp);
    for (int i = 0; i < 16; i++) {
      res_[i * 2 + 0] = hex[tmp[i] >> 4];
      res_[i * 2 + 1] = hex[tmp[i] & 0xf];
    }
    res_[32] = 0;

    return res_;
  }

 protected:
  char res_[33];
  MD5Context md5_;
};

}  // namespace libvpx_test

#endif  // LIBGAV1_TESTS_THIRD_PARTY_LIBVPX_MD5_HELPER_H_
