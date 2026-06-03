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
// "liveMedia"
// Copyright (c) 1996-2023 Live Networks, Inc.  All rights reserved.
// A data structure that implements a MIKEY message (RFC 3830)
// C++ header

#ifndef _MIKEY_HH
#define _MIKEY_HH

#ifndef _NET_COMMON_H
#include "NetCommon.h"
#endif
#ifndef _BOOLEAN_HH
#include "Boolean.hh"
#endif

class MIKEYState {
public:
  MIKEYState(Boolean useEncryption = True); // initialize with default parameters
  virtual ~MIKEYState();

  static MIKEYState* createNew(u_int8_t const* messageToParse, unsigned messageSize);
      // (Attempts to) parse a binary MIKEY message, returning a new "MIKEYState" if successful
      // (or NULL if unsuccessful).

  u_int8_t* generateMessage(unsigned& messageSize) const;
      // Returns a binary message representing the current MIKEY state, of size "messageSize" bytes.
      // This array is dynamically allocated by this routine, and must be delete[]d by the caller.

  // Accessors for the encryption/authentication parameters:
  Boolean encryptSRTP() const { return fEncryptSRTP; }
  Boolean encryptSRTCP() const { return fEncryptSRTCP; }
  u_int8_t const* keyData() const { return fKeyData; }
  u_int32_t MKI() const { return fMKI; }
  Boolean useAuthentication() const { return fUseAuthentication; }

private:
  MIKEYState(u_int8_t const* messageToParse, unsigned messageSize, Boolean& parsedOK);
      // called only by "createNew()"

  void addNewPayload(class MIKEYPayload* newPayload);
  Boolean parseHDRPayload(u_int8_t const*& ptr, u_int8_t const* endPtr, u_int8_t& nextPayloadType);
  Boolean parseNonHDRPayload(u_int8_t const*& ptr, u_int8_t const* endPtr, u_int8_t& nextPayloadType);
  
private:
  // Encryption/authentication parameters, either set by default
  // (if the first (parameterless) constructor is used), or set by parsing an input message
  // (if the second constructor is used):
  Boolean fEncryptSRTP;
  Boolean fEncryptSRTCP;
  u_int8_t fKeyData[16+14]; // encryption key + salt
  u_int32_t fMKI; // used only if encryption is used. (We assume a MKI length of 4.)
  Boolean fUseAuthentication;

  // Our internal binary representation of the MIKEY payloads:
  class MIKEYPayload* fHeaderPayload;
  class MIKEYPayload* fTailPayload;
  unsigned fTotalPayloadByteCount;
};

#endif
