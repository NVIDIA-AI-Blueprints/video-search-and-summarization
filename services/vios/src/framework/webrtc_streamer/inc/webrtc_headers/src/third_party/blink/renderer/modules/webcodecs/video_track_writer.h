// Copyright 2020 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_WEBCODECS_VIDEO_TRACK_WRITER_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_WEBCODECS_VIDEO_TRACK_WRITER_H_

#include "third_party/blink/renderer/modules/mediastream/media_stream_track.h"
#include "third_party/blink/renderer/modules/modules_export.h"
#include "third_party/blink/renderer/platform/bindings/script_wrappable.h"

namespace blink {

class ScriptState;
class WritableStream;
class VideoTrackWriterParameters;

class MODULES_EXPORT VideoTrackWriter final : public ScriptWrappable {
  DEFINE_WRAPPERTYPEINFO();

 public:
  // video_track_writer.idl implementation
  static VideoTrackWriter* Create(ScriptState* script_state,
                                  const VideoTrackWriterParameters* params,
                                  ExceptionState& exception_state);
  VideoTrackWriter(MediaStreamTrack* track, WritableStream* writable);
  MediaStreamTrack* track();
  WritableStream* writable();

  // GarbageCollected override
  void Trace(Visitor* visitor) const override;

 private:
  Member<MediaStreamTrack> track_;
  Member<WritableStream> writable_;

  DISALLOW_COPY_AND_ASSIGN(VideoTrackWriter);
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_WEBCODECS_VIDEO_TRACK_WRITER_H_
