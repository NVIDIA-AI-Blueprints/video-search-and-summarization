/*
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: MIT
 *
 * Permission is hereby granted, free of charge, to any person obtaining a
 * copy of this software and associated documentation files (the "Software"),
 * to deal in the Software without restriction, including without limitation
 * the rights to use, copy, modify, merge, publish, distribute, sublicense,
 * and/or sell copies of the Software, and to permit persons to whom the
 * Software is furnished to do so, subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in
 * all copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
 * IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
 * FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL
 * THE AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
 * LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING
 * FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER
 * DEALINGS IN THE SOFTWARE.
 */

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
// "groupsock"
// Copyright (c) 1996-2023 Live Networks, Inc.  All rights reserved.
// Network Interfaces
// C++ header

#ifndef _NET_INTERFACE_HH
#define _NET_INTERFACE_HH

#ifndef _NET_ADDRESS_HH
#include "NetAddress.hh"
#endif

class NetInterface {
public:
  virtual ~NetInterface();

  static UsageEnvironment* DefaultUsageEnvironment;
      // if non-NULL, used for each new interface

protected:
  NetInterface(); // virtual base class
};

class Socket: public NetInterface {
public:
  virtual ~Socket();
  void reset(); // closes the socket, and sets "fSocketNum" to -1

  virtual Boolean handleRead(unsigned char* buffer, unsigned bufferMaxSize,
			     unsigned& bytesRead,
			     struct sockaddr_storage& fromAddress) = 0;
      // Returns False on error; resultData == NULL if data ignored

  int socketNum() const { return fSocketNum; }

  Port port() const {
    return fPort;
  }

  UsageEnvironment& env() const { return fEnv; }

  static int DebugLevel;

protected:
  Socket(UsageEnvironment& env, Port port, int family); // virtual base class

  Boolean changePort(Port newPort); // will also cause socketNum() to change

private:
  int fSocketNum;
  UsageEnvironment& fEnv;
  Port fPort;
  int fFamily;
};

UsageEnvironment& operator<<(UsageEnvironment& s, const Socket& sock);

// A data structure for looking up a Socket by port:

class SocketLookupTable {
public:
  virtual ~SocketLookupTable();

  Socket* Fetch(UsageEnvironment& env, Port port, Boolean& isNew);
  // Creates a new Socket if none already exists
  Boolean Remove(Socket const* sock);

protected:
  SocketLookupTable(); // abstract base class
  virtual Socket* CreateNew(UsageEnvironment& env, Port port) = 0;

private:
  HashTable* fTable;
};

// A data structure for counting traffic:

class NetInterfaceTrafficStats {
public:
  NetInterfaceTrafficStats();

  void countPacket(unsigned packetSize);

  float totNumPackets() const {return fTotNumPackets;}
  float totNumBytes() const {return fTotNumBytes;}

  Boolean haveSeenTraffic() const;

private:
  float fTotNumPackets;
  float fTotNumBytes;
};

#endif
