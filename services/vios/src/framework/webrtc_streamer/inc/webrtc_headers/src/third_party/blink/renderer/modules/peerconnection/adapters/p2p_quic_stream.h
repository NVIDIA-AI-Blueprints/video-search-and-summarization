// Copyright 2018 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_P2P_QUIC_STREAM_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_P2P_QUIC_STREAM_H_

#include <stdint.h>

#include "third_party/blink/renderer/platform/wtf/vector.h"

namespace blink {

// The bidirectional QUIC stream object to be used by the RTCQuicStream Web
// API. See: https://w3c.github.io/webrtc-quic/#quicstream*
//
// Lifetime: The P2PQuicStream is owned by the P2PQuicTransport, and can be
// deleted after the stream is closed for reading and writing. This can happen
// in 3 ways: 1) OnRemoteReset has been fired. 2) Calling Reset(). 3) Both
// a FIN bit has been sent with WriteData(_, true) and OnRemoteFinish has been
// fired.
class P2PQuicStream {
 public:
  // Receives callbacks for receiving RST_STREAM frames or a STREAM_FRAME with
  // the FIN bit set. The Delegate should be subclassed by an object that can
  // post the task to the main JS thread. The delegate's lifetime should outlive
  // this P2PQuicStream.
  class Delegate {
   public:
    virtual ~Delegate() {}

    // Called when the stream receives a RST_STREAM frame from the remote side.
    // This means the stream is closed and can no longer read or write, and is
    // deleted by the quic::QuicSession.
    virtual void OnRemoteReset() {}

    // Called when the P2PQuicStream has received data from the remote side.
    // If |fin| is set to true that means that the FIN bit has been received
    // and the Delegate will no longer receive data with OnDataReceived.
    // If the stream has already finished writing, then upon receiving the FIN
    // bit the stream can no longer read or write and is deleted by the
    // quic::QuicSession.
    virtual void OnDataReceived(Vector<uint8_t> data, bool fin) {}

    // Called when data written with WriteData() has been consumed by QUIC.
    //
    // This will happen immediately after calling WriteData(), unless QUIC has
    // buffered the data in which case it will be fired when the stream is no
    // longer write blocked and the data is consumed. |amount| specifies how
    // much data was consumed in bytes.
    virtual void OnWriteDataConsumed(uint32_t amount) {}
  };

  virtual ~P2PQuicStream() = default;

  // Sends a RST_STREAM frame to the remote side. This closes the P2PQuicStream
  // for reading & writing and it will be deleted by the quic::QuicSession. When
  // the remote side receives the RST_STREAM frame it will close the stream for
  // reading and writing and send a RST_STREAM frame back. Calling Reset() will
  // not trigger OnRemoteReset to be called locally when the RST_STREAM frame is
  // received from the remote side, because the local stream is already closed.
  virtual void Reset() = 0;

  // Marks received data of size |amount| as being consumed by the Delegate.
  // This is used in conjuction with Delegate::OnDataReceived, to let the
  // P2PQuicStream know that received data has been consumed. This allows the
  // P2PQuicStream to send back pressure to the send side, if the Delegate
  // cannot receive more data.
  virtual void MarkReceivedDataConsumed(uint32_t amount) = 0;

  // Writes |data| to a STREAM frame and gives it to QUIC to be buffered or sent
  // to the remote endpoint. Once that data has been sent Delegate::OnDataSent()
  // will be fired. Specifying |fin| to true will mark the STREAM frame with the
  // FIN bit set, which notifies the remote side that this stream is done
  // writing. After sending the FIN bit, the P2PQuicStream can no longer write.
  // Once the P2PQuicStream has sent AND received the FIN bit it will be closed
  // for reading and writing and deleted by the quic::QuicSession.
  virtual void WriteData(Vector<uint8_t> data, bool fin) = 0;

  // Sets the delegate object. In the case that the Delegate does not outlive
  // the P2PQuicStream object, it must unset itself before destructing.
  virtual void SetDelegate(Delegate* delegate) = 0;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_P2P_QUIC_STREAM_H_
