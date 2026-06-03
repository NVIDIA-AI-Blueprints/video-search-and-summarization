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
// State encapsulating a TLS connection
// C++ header

#ifndef _TLS_STATE_HH
#define _TLS_STATE_HH

#ifndef _NET_COMMON_H
#include "NetCommon.h"
#endif
#ifndef _BOOLEAN_HH
#include "Boolean.hh"
#endif
#ifndef _USAGE_ENVIRONMENT_HH
#include "UsageEnvironment.hh"
#endif
#ifndef NO_OPENSSL
#include <openssl/ssl.h>
#endif

class TLSState {
public:
  Boolean isNeeded;

  int write(const char* data, unsigned count);
  int read(u_int8_t* buffer, unsigned bufferSize);

  void nullify(); // clear the state so that the destructor will have no effect

protected: // we're an abstract base class
  TLSState();
  virtual ~TLSState();

#ifndef NO_OPENSSL
  void initLibrary();
  void reset();

protected:
  Boolean fHasBeenSetup;
  SSL_CTX* fCtx;
  SSL* fCon;
#endif
};

class ClientTLSState: public TLSState {
public:
  ClientTLSState(class RTSPClient& client);
  virtual ~ClientTLSState();

  int connect(int socketNum); // returns: <0 (error), 0 (pending), >0 (success)

#ifndef NO_OPENSSL
private:
  Boolean setup(int socketNum);

private:
  class RTSPClient& fClient;
#endif
};

class ServerTLSState: public TLSState {
public:
  ServerTLSState(UsageEnvironment& env);
  virtual ~ServerTLSState();

  void setCertificateAndPrivateKeyFileNames(char const* certFileName, char const* privKeyFileName);
  void assignStateFrom(ServerTLSState const& from);

  int accept(int socketNum); // returns: <0 (error), 0 (pending), >0 (success)

  Boolean tlsAcceptIsNeeded;

#ifndef NO_OPENSSL
private:
  Boolean setup(int socketNum);

private:
  UsageEnvironment& fEnv;
  char const* fCertificateFileName;
  char const* fPrivateKeyFileName;
#endif
};

#endif
