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
// "multikit" Multicast Application Shell
// Copyright (c) 1996-2023, Live Networks, Inc.  All rights reserved
// "Group Endpoint Id"
// C++ header

#ifndef _GROUPEID_HH
#define _GROUPEID_HH

#ifndef _BOOLEAN_HH
#include "Boolean.hh"
#endif

#ifndef _NET_ADDRESS_HH
#include "NetAddress.hh"
#endif

class GroupEId {
public:
  GroupEId(struct sockaddr_storage const& groupAddr,
	   portNumBits portNum, u_int8_t ttl);
      // used for a 'source-independent multicast' group
  GroupEId(struct sockaddr_storage const& groupAddr,
	   struct sockaddr_storage const& sourceFilterAddr,
	   portNumBits portNum);
      // used for a 'source-specific multicast' group
  GroupEId(); // tmp default constructor, until "Groupsock" interface uses "sockaddr_storage"

  struct sockaddr_storage const& groupAddress() const { return fGroupAddress; }
  struct sockaddr_storage const& sourceFilterAddress() const { return fSourceFilterAddress; }

  Boolean isSSM() const;

  portNumBits portNum() const;

  u_int8_t ttl() const { return fTTL; }

private:
  void init(struct sockaddr_storage const& groupAddr,
	    struct sockaddr_storage const& sourceFilterAddr,
	    portNumBits portNum,
	    u_int8_t ttl);

private:
  struct sockaddr_storage fGroupAddress; // also includes port number (in network byte order)
  struct sockaddr_storage fSourceFilterAddress;
  u_int8_t fTTL;
};

#endif
