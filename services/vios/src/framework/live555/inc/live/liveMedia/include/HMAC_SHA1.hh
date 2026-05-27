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
// Copyright (c) 1996-2023, Live Networks, Inc.  All rights reserved
//
// A function for computing the HMAC_SHA1 digest
// Definition

#ifndef _HMAC_SHA1_HH
#define _HMAC_SHA1_HH

#ifndef NO_OPENSSL
#ifndef _HMAC_HASH_HH
#include "HMAC_hash.hh"
#endif

#define SHA1_DIGEST_LEN 20

HMAC_hash HMAC_SHA1;
#endif
#endif
