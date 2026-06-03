/**********
This library is free software; you can redistribute it and/or modify it under
the terms of the GNU Lesser General Public License as published by the
Free Software Foundation; either version 3 of the License, or (at your
option) any later version. (See <http://www.gnu.org/copyleft/lesser.html>.)

This library is distributed in the hope that it will be useful, but WITHOUT
ANY WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS
FOR A PARTICULAR PURPOSE.  See the GNU Lesser General Public License for
more details.

You should have received a copy of the GNU Lesser General Public License
along with this library; if not, write to the Free Software Foundation, Inc.,
51 Franklin Street, Fifth Floor, Boston, MA 02110-1301  USA
**********/
// Copyright (c) 1996-2023 Live Networks, Inc.  All rights reserved.
// The SRTP 'Cryptographic Context', used in all of our uses of SRTP.
// Definition

#ifndef _SRTP_CRYPTOGRAPHIC_CONTEXT_HH
#define _SRTP_CRYPTOGRAPHIC_CONTEXT_HH

#ifndef _MIKEY_HH
#include "MIKEY.hh"
#endif

class SRTPCryptographicContext {
public:
  SRTPCryptographicContext(MIKEYState const& mikeyState);
  virtual ~SRTPCryptographicContext();

  // Authenticate (if necessary) and decrypt (if necessary) incoming SRTP and SRTCP packets.
  // Returns True iff the packet is well-formed and authenticates OK.
  // ("outPacketSize" will be <= "inPacketSize".)
  Boolean processIncomingSRTPPacket(u_int8_t* buffer, unsigned inPacketSize,
				    unsigned& outPacketSize);
  Boolean processIncomingSRTCPPacket(u_int8_t* buffer, unsigned inPacketSize,
				     unsigned& outPacketSize);

  // Encrypt (if necessary) and add an authentication tag (if necessary) to an outgoing
  // RTP and RTCP packet.
  // Returns True iff the packet is well-formed.
  // ("outPacketSize" will be >= "inPacketSize"; there must be enough space at the end of
  //  "buffer" for the extra (4+10 bytes for SRTP; 4+4+10 bytes for SRTCP).)
  Boolean processOutgoingSRTPPacket(u_int8_t* buffer, unsigned inPacketSize,
				    unsigned& outPacketSize);
  Boolean processOutgoingSRTCPPacket(u_int8_t* buffer, unsigned inPacketSize,
				     unsigned& outPacketSize);

#ifndef NO_OPENSSL
private:
  // Definitions specific to the "SRTP_AES128_CM_HMAC_SHA1_80" ciphersuite.
  // Later generalize to support more SRTP ciphersuites #####
#define SRTP_CIPHER_KEY_LENGTH (128/8) // in bytes
#define SRTP_CIPHER_SALT_LENGTH (112/8) // in bytes
#define SRTP_MKI_LENGTH 4 // in bytes
#define SRTP_AUTH_KEY_LENGTH (160/8) // in bytes
#define SRTP_AUTH_TAG_LENGTH (80/8) // in bytes

  struct derivedKeys {
    u_int8_t cipherKey[SRTP_CIPHER_KEY_LENGTH];
    u_int8_t salt[SRTP_CIPHER_SALT_LENGTH];
    u_int8_t authKey[SRTP_AUTH_KEY_LENGTH];
  };

  struct allDerivedKeys {
    derivedKeys srtp;
    derivedKeys srtcp;
  };

  typedef enum {
		label_srtp_encryption  = 0x00,
		label_srtp_msg_auth    = 0x01,
		label_srtp_salt        = 0x02,
		label_srtcp_encryption = 0x03,
		label_srtcp_msg_auth   = 0x04,
		label_srtcp_salt       = 0x05
  } SRTPKeyDerivationLabel;

  unsigned generateSRTPAuthenticationTag(u_int8_t const* dataToAuthenticate, unsigned numBytesToAuthenticate,
					 u_int8_t* resultAuthenticationTag);
      // returns the size of the resulting authentication tag
  unsigned generateSRTCPAuthenticationTag(u_int8_t const* dataToAuthenticate, unsigned numBytesToAuthenticate,
					  u_int8_t* resultAuthenticationTag);
      // returns the size of the resulting authentication tag

  Boolean verifySRTPAuthenticationTag(u_int8_t* dataToAuthenticate, unsigned numBytesToAuthenticate,
				      u_int32_t roc, u_int8_t const* authenticationTag);
  Boolean verifySRTCPAuthenticationTag(u_int8_t const* dataToAuthenticate, unsigned numBytesToAuthenticate,
				       u_int8_t const* authenticationTag);

  void decryptSRTPPacket(u_int64_t index, u_int32_t ssrc, u_int8_t* data, unsigned numDataBytes);
  void decryptSRTCPPacket(u_int32_t index, u_int32_t ssrc, u_int8_t* data, unsigned numDataBytes);

  void encryptSRTPPacket(u_int64_t index, u_int32_t ssrc, u_int8_t* data, unsigned numDataBytes);
  void encryptSRTCPPacket(u_int32_t index, u_int32_t ssrc, u_int8_t* data, unsigned numDataBytes);

  unsigned generateAuthenticationTag(derivedKeys& keysToUse,
				     u_int8_t const* dataToAuthenticate, unsigned numBytesToAuthenticate,
				     u_int8_t* resultAuthenticationTag);
      // returns the size of the resulting authentication tag
      // "resultAuthenticationTag" must point to an array of at least SRTP_AUTH_TAG_LENGTH
  Boolean verifyAuthenticationTag(derivedKeys& keysToUse,
				  u_int8_t const* dataToAuthenticate, unsigned numBytesToAuthenticate,
				  u_int8_t const* authenticationTag);

  void cryptData(derivedKeys& keys, u_int64_t index, u_int32_t ssrc,
		 u_int8_t* data, unsigned numDataBytes);

  void performKeyDerivation();

  void deriveKeysFromMaster(u_int8_t const* masterKey, u_int8_t const* salt,
			    allDerivedKeys& allKeysResult);
      // used to implement "performKeyDerivation()"
  void deriveSingleKey(u_int8_t const* masterKey, u_int8_t const* salt,
		       SRTPKeyDerivationLabel label,
		       unsigned resultKeyLength, u_int8_t* resultKey);
      // used to implement "deriveKeysFromMaster()".
      // ("resultKey" must be an existing buffer, of size >= "resultKeyLength")

private:
  MIKEYState const& fMIKEYState;

  // Master key + salt:
  u_int8_t const* masterKeyPlusSalt() const { return fMIKEYState.keyData(); }

  u_int8_t const* masterKey() const { return &masterKeyPlusSalt()[0]; }
  u_int8_t const* masterSalt() const { return &masterKeyPlusSalt()[SRTP_CIPHER_KEY_LENGTH]; }

  Boolean weEncryptSRTP() const { return fMIKEYState.encryptSRTP(); }
  Boolean weEncryptSRTCP() const { return fMIKEYState.encryptSRTCP(); }
  Boolean weAuthenticate() const { return fMIKEYState.useAuthentication(); }
  u_int32_t MKI() const { return fMIKEYState.MKI(); }

  // Derived (i.e., session) keys:
  allDerivedKeys fDerivedKeys;

  // State used for handling the reception of SRTP packets:
  Boolean fHaveReceivedSRTPPackets;
  u_int16_t fPreviousHighRTPSeqNum;
  u_int32_t fReceptionROC; // rollover counter

  // State used for handling the sending of SRTP packets:
  Boolean fHaveSentSRTPPackets;
  u_int32_t fSendingROC;

  // State used for handling the sending of SRTCP packets:
  u_int32_t fSRTCPIndex;
#endif
};

#endif
