// Copyright 2019 The Chromium Authors. All rights reserved.
// Use of this source code is governed by a BSD-style license that can be
// found in the LICENSE file.

#ifndef THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_QUIC_PACKET_TRANSPORT_ADAPTER_H_
#define THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_QUIC_PACKET_TRANSPORT_ADAPTER_H_

#include "third_party/blink/renderer/modules/modules_export.h"
#include "third_party/blink/renderer/modules/peerconnection/adapters/p2p_quic_packet_transport.h"
#include "third_party/webrtc/p2p/base/p2p_transport_channel.h"

namespace blink {

// Implementation of P2PQuicPacketTransport backed by a IceTransportInternal.
// It adapts the underlying ICE transport to be used with the QUIC library.
// This does not filter packets. It requires that the IceTransportInternal is
// only being used for QUIC packets.
class MODULES_EXPORT QuicPacketTransportAdapter : public P2PQuicPacketTransport,
                                                  public sigslot::has_slots<> {
 public:
  QuicPacketTransportAdapter(
      cricket::IceTransportInternal* p2p_transport_channel);

  ~QuicPacketTransportAdapter() override;

  // P2PQuicPacketTransport overrides.
  int WritePacket(const QuicPacket& packet) override;
  void SetReceiveDelegate(ReceiveDelegate* receive_delegate) override;
  void SetWriteObserver(WriteObserver* write_observer) override;
  bool Writable() override;

 private:
  // IceTransportInternal callbacks.
  void OnReadPacket(rtc::PacketTransportInternal* packet_transport,
                    const char* buffer,
                    size_t buffer_length,
                    const int64_t& packet_time,
                    int flags);

  void OnReadyToSend(rtc::PacketTransportInternal* packet_transport);

  // Owned by the IceTransportAdapter and will outlive this object.
  cricket::IceTransportInternal* ice_transport_;
  // The ReceiveDelegate and WriteObserver must be unset before
  // this object is destroyed.
  ReceiveDelegate* receive_delegate_ = nullptr;
  WriteObserver* write_observer_ = nullptr;
  // If the CHLO arrives early it is cached. This can occur if the QUIC
  // handshake begins on the client side before the remote parameters are
  // received on the server side.
  std::string cached_client_hello_packet_;
};

}  // namespace blink

#endif  // THIRD_PARTY_BLINK_RENDERER_MODULES_PEERCONNECTION_ADAPTERS_QUIC_PACKET_TRANSPORT_ADAPTER_H_
