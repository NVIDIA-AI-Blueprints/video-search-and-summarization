/*
 * Copyright (C) 2013 Google Inc. All rights reserved.
 *
 * Redistribution and use in source and binary forms, with or without
 * modification, are permitted provided that the following conditions
 * are met:
 *
 * 1.  Redistributions of source code must retain the above copyright
 *     notice, this list of conditions and the following disclaimer.
 * 2.  Redistributions in binary form must reproduce the above copyright
 *     notice, this list of conditions and the following disclaimer in the
 *     documentation and/or other materials provided with the distribution.
 *
 * THIS SOFTWARE IS PROVIDED BY APPLE AND ITS CONTRIBUTORS "AS IS" AND ANY
 * EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
 * WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
 * DISCLAIMED. IN NO EVENT SHALL APPLE OR ITS CONTRIBUTORS BE LIABLE FOR ANY
 * DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES
 * (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES;
 * LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND
 * ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT
 * (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF
 * THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.
 */

#ifndef THIRD_PARTY_BLINK_PUBLIC_COMMON_INDEXEDDB_WEB_IDB_TYPES_H_
#define THIRD_PARTY_BLINK_PUBLIC_COMMON_INDEXEDDB_WEB_IDB_TYPES_H_

#include "third_party/blink/public/mojom/indexeddb/indexeddb.mojom-shared.h"

namespace blink {

// kIDBOperationTypeCount corresponds to the number of mojom::IDBOperationType
// enum values that exist.  Mojo provides kMaxValue which corresponds to the
// value of the last item in the enum list.  To get the total number, we have
// to increment the max value by 1.
static const uint32_t kIDBOperationTypeCount =
    static_cast<uint32_t>(blink::mojom::IDBOperationType::kMaxValue) + 1;

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_PUBLIC_COMMON_INDEXEDDB_WEB_IDB_TYPES_H_
