// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_NATIVE_IO_NATIVE_IO_FILE_SYNC_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_NATIVE_IO_NATIVE_IO_FILE_SYNC_H_

#include <stdint.h>

#include "base/files/file.h"
#include "third_party/blink/public/mojom/native_io/native_io.mojom-blink.h"
#include "third_party/blink/renderer/core/execution_context/execution_context_lifecycle_observer.h"
#include "third_party/blink/renderer/core/typed_arrays/array_buffer_view_helpers.h"
#include "third_party/blink/renderer/core/typed_arrays/dom_array_buffer_view.h"
#include "third_party/blink/renderer/platform/bindings/script_wrappable.h"
#include "third_party/blink/renderer/platform/heap/handle.h"
#include "third_party/blink/renderer/platform/mojo/heap_mojo_remote.h"
#include "third_party/blink/renderer/platform/wtf/text/wtf_string.h"
#include "third_party/blink/renderer/platform/wtf/vector.h"

namespace blink {

class ExceptionState;
class ExecutionContext;

class NativeIOFileSync final : public ScriptWrappable {
  DEFINE_WRAPPERTYPEINFO();

 public:
  NativeIOFileSync(base::File backing_file,
                   HeapMojoRemote<mojom::blink::NativeIOFileHost> backend_file,
                   ExecutionContext*);

  NativeIOFileSync(const NativeIOFileSync&) = delete;
  NativeIOFileSync& operator=(const NativeIOFileSync&) = delete;

  // Needed because of the mojo::Remote<mojom::blink::NativeIOFile>.
  ~NativeIOFileSync() override;

  void close();
  int read(MaybeShared<DOMArrayBufferView> buffer,
           uint64_t file_offset,
           ExceptionState&);
  int write(MaybeShared<DOMArrayBufferView> buffer,
            uint64_t file_offset,
            ExceptionState&);

  // GarbageCollected
  void Trace(Visitor* visitor) const override;

 private:
  // Called when the mojo backend disconnects.
  void OnBackendDisconnect();

  // The file on disk backing this NativeIOFile.
  base::File backing_file_;

  // Mojo pipe that holds the renderer's lock on the file.
  HeapMojoRemote<mojom::blink::NativeIOFileHost> backend_file_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_NATIVE_IO_NATIVE_IO_FILE_SYNC_H_
