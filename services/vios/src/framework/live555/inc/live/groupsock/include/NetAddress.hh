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
// Copyright (c) 1996-2026 Live Networks, Inc.  All rights reserved.
// Network Addresses
// C++ header

#ifndef _NET_ADDRESS_HH
#define _NET_ADDRESS_HH

#ifndef _HASH_TABLE_HH
#include "HashTable.hh"
#endif

#ifndef _NET_COMMON_H
#include "NetCommon.h"
#endif

#ifndef _USAGE_ENVIRONMENT_HH
#include "UsageEnvironment.hh"
#endif

// Definition of a type representing a low-level network address.
    // Note that the type "netAddressBits" is no longer defined; use "ipv4AddressBits" instead.
typedef u_int32_t ipv4AddressBits;
typedef u_int8_t ipv6AddressBits[16]; // 128 bits

class NetAddress {
public:
  NetAddress(u_int8_t const* data,
	     unsigned length = 4 /* default: 32 bits (for IPv4); use 16 (128 bits) for IPv6 */);
  NetAddress(unsigned length = 4); // sets address data to all-zeros
  NetAddress(NetAddress const& orig);
  NetAddress& operator=(NetAddress const& rightSide);
  virtual ~NetAddress();
  
  unsigned length() const { return fLength; }
  u_int8_t const* data() const // always in network byte order
  { return fData; }
  
private:
  void assign(u_int8_t const* data, unsigned length);
  void clean();
  
  unsigned fLength;
  u_int8_t* fData;
};

struct sockaddr_storage const& nullAddress(int addressFamily = AF_INET);
Boolean addressIsNull(sockaddr_storage const& address);

SOCKLEN_T addressSize(sockaddr_storage const& address);

void copyAddress(struct sockaddr_storage& to, NetAddress const* from);

Boolean operator==(struct sockaddr_storage const& left, struct sockaddr_storage const& right);
    // compares the family and address parts only; not the port number or anything else

class NetAddressList {
public:
  NetAddressList(char const* hostname, int addressFamily = AF_UNSPEC);
  NetAddressList(NetAddressList const& orig);
  NetAddressList& operator=(NetAddressList const& rightSide);
  virtual ~NetAddressList();
  
  unsigned numAddresses() const { return fNumAddresses; }
  
  NetAddress const* firstAddress() const;
  
  // Used to iterate through the addresses in a list:
  class Iterator {
  public:
    Iterator(NetAddressList const& addressList);
    NetAddress const* nextAddress(); // NULL iff none
  private:
    NetAddressList const& fAddressList;
    unsigned fNextIndex;
  };
  
private:
  void assign(unsigned numAddresses, NetAddress** addressArray);
  void clean();
  
  friend class Iterator;
  unsigned fNumAddresses;
  NetAddress** fAddressArray;
};

typedef u_int16_t portNumBits;

class Port {
public:
  Port(portNumBits num /* in host byte order */);
  
  portNumBits num() const { return fPortNum; } // in network byte order
  
private:
  portNumBits fPortNum; // stored in network byte order
#ifdef IRIX
  portNumBits filler; // hack to overcome a bug in IRIX C++ compiler
#endif
};

UsageEnvironment& operator<<(UsageEnvironment& s, const Port& p);


// A generic table for looking up objects by (address1, address2, port)
class AddressPortLookupTable {
public:
  AddressPortLookupTable();
  virtual ~AddressPortLookupTable();
  
  void* Add(struct sockaddr_storage const& address1,
	    struct sockaddr_storage const& address2,
	    Port port,
	    void* value);
      // Returns the old value if different, otherwise 0
  void* Add(struct sockaddr_storage const& address1,
	    Port port,
	    void* value) {
    return Add(address1, nullAddress(), port, value);
  }

  Boolean Remove(struct sockaddr_storage const& address1,
		 struct sockaddr_storage const& address2,
		 Port port);
  Boolean Remove(struct sockaddr_storage const& address1,
		 Port port) {
    return Remove(address1, nullAddress(), port);
  }

  void* Lookup(struct sockaddr_storage const& address1,
	       struct sockaddr_storage const& address2,
	       Port port);
      // Returns 0 if not found
  void* Lookup(struct sockaddr_storage const& address1,
	       Port port) {
    return Lookup(address1, nullAddress(), port);
  }

  void* RemoveNext() { return fTable->RemoveNext(); }

  // Used to iterate through the entries in the table
  class Iterator {
  public:
    Iterator(AddressPortLookupTable& table);
    virtual ~Iterator();
    
    void* next(); // NULL iff none
    
  private:
    HashTable::Iterator* fIter;
  };
  
private:
  friend class Iterator;
  HashTable* fTable;
};


Boolean IsMulticastAddress(struct sockaddr_storage const& address);


// A mechanism for displaying an IP (v4 or v6) address in ASCII.
// (This encapsulates the "inet_ntop()" function.)
class AddressString {
public:
  // IPv4 input:
  AddressString(struct sockaddr_in const& addr);
  AddressString(struct in_addr const& addr);
  AddressString(ipv4AddressBits const& addr); // "addr" is assumed to be in network byte order

  // IPv6 input:
  AddressString(struct sockaddr_in6 const& addr);
  AddressString(struct in6_addr const& addr);
  AddressString(ipv6AddressBits const& addr);

  // IPv4 or IPv6 input:
  AddressString(struct sockaddr_storage const& addr);

  virtual ~AddressString();

  char const* val() const { return fVal; }

private:
  void init(ipv4AddressBits const& addr); // used to implement the IPv4 constructors
  void init(ipv6AddressBits const& addr); // used to implement the IPv6 constructors

private:
  char* fVal; // The result ASCII string: allocated by the constructor; deleted by the destructor
};

portNumBits portNum(struct sockaddr_storage const& address);
void setPortNum(struct sockaddr_storage& address, portNumBits portNum/*in network order*/);

#endif
