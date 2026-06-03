/*
 * SPDX-FileCopyrightText: Copyright (c) 2022-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include "network_utils.h"
#include <memory>
#include <libxml/encoding.h>
#include <libxml/xmlwriter.h>
#include <unistd.h>
#include <sys/types.h>
#include <sys/socket.h>
#include <arpa/inet.h>
#include <netinet/in.h>
#include <net/if.h>
#include <sys/time.h>
#include <sys/ioctl.h>
#include <algorithm>
#include <curl/curl.h>
#include <unordered_set>


#include "nvsoap.h"
#include "utils.h"
#include "config.h"
#include "macros.h"
#include "logger.h"


constexpr int CAMERA_REMOVE_VALIDATE_TIMEOUT = 5; // 5 secs
constexpr int PROBE_PORT = 3702;
constexpr const char* PROBE_IP = "239.255.255.250";
constexpr const char* ENCODING = "utf-8";
constexpr const char* EXIT_MESSAGE = "exit_listener_thread";
constexpr int CURL_REQUEST_TIMEOUT = 408;
constexpr const char* CURL_REQUEST_TIMEOUT_MSG = "Device request timeout";

std::mutex g_probeMutex;
std::mutex g_probeMatchMutex;
vector<int> g_probePort;
int g_fdCtrl[2];

namespace
{
    size_t callback( const char* in, size_t size, size_t num, string* out)
    {
        const size_t totalBytes(size * num);
        out->append(in, totalBytes);
        return totalBytes;
    }
}

#ifdef DEBUG
struct curlData {
  char trace_ascii; /* 1 or 0 */
};
#endif

class AutoDestroyXml
{
public:
    AutoDestroyXml(xmlBufferPtr xml) :m_xml(xml) {}
    ~AutoDestroyXml() { xmlBufferFree(m_xml); }
private:
    xmlBufferPtr m_xml;
};

bool isCameraOnline(SensorInfo& sensor)
{
    std::shared_ptr<ClientSession>& clientSession = sensor.getClientSession();
    if (nullptr == clientSession || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client Session for sensor: " << sensor.id << " url:" << sensor.url << endl;
        return false;
    }

    nvsoap_ soap;
    soap.url = sensor.url;
    soap.user = sensor.user;
    soap.password = sensor.password;
    soap.timeout = CAMERA_REMOVE_VALIDATE_TIMEOUT;
    soap.curl = clientSession->getCurlClient();
    soap.authMethod = sensor.serviceCapabilities.securedAuthMethod;
    string out;
    return clientSession->getNvSoap()->GetSystemDateAndTime(soap, out) == 0;
}

static string composeProbeXml()
{
      int rc;
      xmlTextWriterPtr writer;
      xmlBufferPtr xmlBuf;
      string retString;

      xmlBuf = xmlBufferCreate();
      if (xmlBuf == nullptr)
      {
        LOG(error) << "testXmlwriterMemory: Error creating the xml buffer" << endl;
        return retString;
      }
      AutoDestroyXml xml(xmlBuf);

      writer = xmlNewTextWriterMemory(xmlBuf, 0);
      if (writer == nullptr)
      {
          LOG(error) << "testXmlwriterMemory: Error creating the xml writer\n" << endl;
          return retString;
      }

      rc = _xmlTextWriterStartDocument_(writer, nullptr, ENCODING, nullptr);
      _xmlTextWriterStartElement_(writer, BAD_CAST "s:Envelope");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:s", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:a", BAD_CAST "http://schemas.xmlsoap.org/ws/2004/08/addressing");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
/*
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:d", BAD_CAST "http://docs.oasis-open.org/ws-dd/ns/discovery/2009/01");
      if (rc < 0)
      {
        cerr <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:i", BAD_CAST "http://printer.example.org/2003/imaging");
      if (rc < 0)
      {
        cerr <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }*/

      _xmlTextWriterStartElement_(writer, BAD_CAST "s:Header");

      _xmlTextWriterStartElement_(writer, BAD_CAST "a:Action");

      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "s:mustUnderstand", BAD_CAST "1");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }

      _xmlTextWriterWriteString_(writer, BAD_CAST "http://schemas.xmlsoap.org/ws/2005/04/discovery/Probe");

      _xmlTextWriterEndElement_(writer); // Action

      _xmlTextWriterStartElement_(writer, BAD_CAST "a:MessageID");
      string msgId = string("urn:uuid:") + generate_uuid();
      _xmlTextWriterWriteString_(writer, BAD_CAST msgId.c_str() );//"urn:uuid:0a6dc791-2be6-4991-9af1-454778a1917a");
      _xmlTextWriterEndElement_(writer); // MessageID

      _xmlTextWriterStartElement_(writer, BAD_CAST "a:ReplyTo");
      _xmlTextWriterWriteElement_(writer, BAD_CAST "a:Address", BAD_CAST "http://schemas.xmlsoap.org/ws/2004/08/addressing/role/anonymous");
      _xmlTextWriterEndElement_(writer); // ReplyTo

      _xmlTextWriterStartElement_(writer, BAD_CAST "a:To");

      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "s:mustUnderstand", BAD_CAST "1");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      _xmlTextWriterWriteString_(writer, BAD_CAST "urn:schemas-xmlsoap-org:ws:2005:04:discovery");
      _xmlTextWriterEndElement_(writer); // To

      _xmlTextWriterEndElement_(writer); // Header


      _xmlTextWriterStartElement_(writer, BAD_CAST "s:Body");

      _xmlTextWriterStartElement_(writer, BAD_CAST "Probe");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://schemas.xmlsoap.org/ws/2005/04/discovery");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }

      _xmlTextWriterStartElement_(writer, BAD_CAST "d:Types");
      //_xmlTextWriterWriteString_(writer, BAD_CAST "dn:NetworkVideoTransmitter");
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:d", BAD_CAST "http://schemas.xmlsoap.org/ws/2005/04/discovery");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:dp0", BAD_CAST "http://www.onvif.org/ver10/network/wsdl");
      if (rc < 0)
      {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
      }
      _xmlTextWriterWriteString_(writer, BAD_CAST "dp0:NetworkVideoTransmitter");
      _xmlTextWriterEndElement_(writer); // Types

      //_xmlTextWriterStartElement_(writer, BAD_CAST "d:Scopes");
      //_xmlTextWriterEndElement_(writer); // Scopes

      _xmlTextWriterEndElement_(writer); // Probe
      _xmlTextWriterEndElement_(writer); // "soap:Body"
      _xmlTextWriterEndDocument_(writer); // "Envelope"
      xmlFreeTextWriter(writer);


      retString = ((char *)xmlBuf->content);

      //cout << retString << endl;
      return retString;
}

static bool isValidOnvifService(const string& url, SensorInfo& sensor)
{
    std::shared_ptr<ClientSession>& clientSession = sensor.getClientSession();
    if (nullptr == clientSession || clientSession->getNvSoap() == nullptr || clientSession->getCurlClient() == nullptr)
    {
        LOG(error) << "Failed to get client Session for sensor: " << sensor.id << " url:" << sensor.url << endl;
        return false;
    }

    nvsoap_ soap;
    soap.url = url;
    soap.timeout = CAMERA_REMOVE_VALIDATE_TIMEOUT;
    soap.user = sensor.user;
    soap.password = sensor.password;
    soap.curl = clientSession->getCurlClient();
    soap.authMethod = sensor.serviceCapabilities.securedAuthMethod;
    string out;
    return clientSession->getNvSoap()->GetSystemDateAndTime(soap, out) == 0;
}

static bool getProbeResponse(const string& xmlData, SensorInfo& sensor)
{
    xmlDocPtr    doc;
    string value;
    bool matchFound = false;

    if (xmlData.empty())
    {
        LOG(error) << "xml data is empty" <<endl;
        return false;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    xmlNodePtr cursor = xmlDocGetRootElement(doc);
    if(cursor)
    {
        xmlNodePtr cur = findNode(doc, cursor, "ProbeMatch");
        if (cur)
        {
            matchFound = true;
            xmlNodePtr cur_ = findNode(doc, cur, "XAddrs");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              vector<string> v = splitString(value, " ");
              for (auto url : v)
              {
                  // Ignore Service with ip-addr 169.254.x.x, Since it is link-local address with no connectivity.
                  if (url.find("169.254") != string::npos || isValidOnvifService(url, sensor) == false)
                  {
                      continue;
                  }
                  sensor.url = url;
                  vector<string> str_arr = splitString(sensor.url, "/");
                  string ip = str_arr.size() > 2 ? str_arr[2] : "";
                  sensor.ip = ip;
                  break;
              }
            }
            cur_ = findNode(doc, cur, "EndpointReference");
            if (cur_ && cur_->xmlChildrenNode)
            {
              value = getNodeValue(doc, cur_->xmlChildrenNode);

              if (value.find("uuid:") != string::npos)
              {
                int len = string("uuid:").size();
                sensor.sensorId = value.substr(value.find("uuid:") + len);
              }
              else
              {
                sensor.sensorId = generate_uuid();
                LOG(info) << "UUID is missing in the probe match, So generating UUID:" << sensor.sensorId << endl;
              }
            }
            cur_ = findNode(doc, cur, "Scopes");
            if (cur_)
            {
              value = getNodeValue(doc, cur_);
              vector<string> ss = splitString(value, " ");
              for(unsigned int i = 0; i < ss.size(); i++)
              {
                  string str = ss[i];
                  std::size_t found = str.find("name");
                  if (found != std::string::npos)
                  {
                      sensor.name = parseattributes(str, ONVIF_PROBE_MATCH_NAME_PREFIX);
                      continue;
                  }
                  found = str.find("type");
                  if (found != std::string::npos)
                  {
                      sensor.type = parseattributes(str, ONVIF_PROBE_MATCH_TYPE_PREFIX);
                      continue;
                  }
                  found = str.find("hardware");
                  if (found != std::string::npos)
                  {
                      sensor.hardware = parseattributes(str, ONVIF_PROBE_MATCH_HARDWARE_PREFIX);
                      continue;
                  }
                  found = str.find("location");
                  if (found != std::string::npos)
                  {
                      sensor.location = parseLocation(str);
                      continue;
                  }
              }
            }
        }
    }
    xmlFreeDoc(doc);
    return matchFound;
}

#if 0
static int sendProbe(map<string, SensorInfo>& deviceList)
{
    struct sockaddr_in groupSock;
    int sd = -1, max_sd;
    int port = PROBE_PORT;
    int ret = 0;
    string mcast_ip = PROBE_IP;
    string xmlout;
    fd_set readfds;
    struct timeval      timeout;
    DeviceConfig& config = GET_CONFIG();
    timeout.tv_sec  = config.sensor_discovery_timeout;
    timeout.tv_usec = 0;
    vector<string> net_interfaces = config.sensor_discovery_interfaces;

    std::lock_guard<std::mutex> lock(g_probeMutex);
    if (net_interfaces.empty())
    {
        net_interfaces.push_back("INADDR_ANY");
    }
    for (unsigned pt = 0; pt < g_probePort.size(); pt++)
    {
        sd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, IPPROTO_UDP);
        if(sd < 0)
        {
            LOG(error) << "Opening datagram socket error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
            LOG(verbose) << "Opening the datagram socket...OK." << endl;

        /*
        * Disable loopback so you do not receive your own datagrams.
        */
        char loopch=0;
        if (setsockopt(sd, IPPROTO_IP, IP_MULTICAST_LOOP, (char *)&loopch, sizeof(loopch)) < 0)
        {
            LOG(error) << "setting IP_MULTICAST_LOOP:" << endl;
            ret = -1;
            goto cleanup;
        }

        if (net_interfaces[pt] != "INADDR_ANY")
        {
            struct ifreq ifr;
            memset(&ifr, 0, sizeof(ifr));
            memcpy(ifr.ifr_name, net_interfaces[pt].c_str(), sizeof(ifr.ifr_name));
            if ((setsockopt(sd, SOL_SOCKET, SO_BINDTODEVICE, (void *)&ifr, sizeof(ifr))) < 0)
            {
                LOG(error) << "Server-setsockopt() error for SO_BINDTODEVICE: "<< strerror(errno) << endl;
                ret = -1;
                goto cleanup;
            }
        }

        struct sockaddr_in localSock;
        memset((char *) &localSock, 0, sizeof(localSock));
        localSock.sin_family = AF_INET;
        localSock.sin_port = htons(port);
        localSock.sin_addr.s_addr = INADDR_ANY;

        if(bind(sd, (struct sockaddr*)&localSock, sizeof(localSock)))
        {
            LOG(error) << "Binding datagram socket error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
            LOG(verbose) << "Binding datagram socket...OK." << endl;

        {
            int reuse = 1;
            if(setsockopt(sd, SOL_SOCKET, SO_REUSEADDR, (char *)&reuse, sizeof(reuse)) < 0)
            {
                LOG(error) << "Setting SO_REUSEADDR error" << endl;
                goto cleanup;
            }
            else
            LOG(verbose) << "Setting SO_REUSEADDR...OK." << endl;
        }

        memset((char *) &groupSock, 0, sizeof(groupSock));
        groupSock.sin_family = AF_INET;
        groupSock.sin_addr.s_addr = inet_addr(mcast_ip.c_str());
        groupSock.sin_port = htons(port);

        xmlout = composeProbeXml();
        LOG(verbose2) << xmlout << endl;

        struct ip_mreq group;
        group.imr_multiaddr.s_addr = inet_addr(mcast_ip.c_str());
        group.imr_interface.s_addr = htonl( INADDR_ANY );;
        if(setsockopt(sd, IPPROTO_IP, IP_ADD_MEMBERSHIP, (char *)&group, sizeof(group)) < 0)
        {
            LOG(error) << "Adding multicast group error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
            LOG(verbose) << "Adding multicast group...OK." << endl;

        if(sendto(sd, xmlout.c_str(), xmlout.size(), 0, (struct sockaddr*)&groupSock, sizeof(groupSock)) < 0)
        {
            LOG(error) << "Sending datagram message error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
        LOG(verbose) << "Sending datagram message...OK" << endl;

        max_sd = sd;
        FD_ZERO(&readfds);
        FD_SET(max_sd, &readfds);
        while(true)
        {
            struct sockaddr_in cliaddr;
            memset(&cliaddr, 0, sizeof(cliaddr));
            int n;
            socklen_t length;
            length = sizeof(cliaddr);
            int buffer_len = 1024 * 10;
            char buffer[buffer_len];
            LOG(verbose) << "Checking data from socket (select)..." << endl;
            int rc = select(max_sd + 1, &readfds, nullptr, nullptr, &timeout);
            if (rc < 0 )
            {
                LOG(error) << "Select on get probeMatch failed" << endl;
                break;
            }
            if (rc == 0 )
            {
                LOG(error) << "Select on get probeMatch timeout" << endl;
                break;
            }
            LOG(verbose) << "Reading data from socket (recvfrom)..." << endl;
            n = recvfrom(sd, (char *)buffer, buffer_len,
                    MSG_WAITALL, ( struct sockaddr *) &cliaddr,
                    &length);
            if (n < 0)
            {
                if (errno != EINTR)
                {
                    perror("recvfrom");
                }
                goto cleanup;
            }
            buffer[n] = '\0';
            string match (buffer);
            LOG(verbose) << "probe Match: " << getCurrentTime() << endl << match << endl;

            g_probeMatchMutex.lock();
            SensorInfo dev;
            if (getProbeResponse(match, dev))
            {
                deviceList[dev.id] = dev;
            }
            else
            {
                g_probeMatchMutex.unlock();
                break;
            }
            g_probeMatchMutex.unlock();
        }
    }
cleanup:
    if (sd != -1)
    {
        close(sd);
        sd = -1;
    }
    return ret;
}
static bool ping(SensorInfo& sensor)
{
    return sendProbeToSensor(sensor, true) == 0;
}
#endif

int sendProbeToSensor(SensorInfo& sensor)
{
    int ret = -1;
    if (sensor.ip.empty())
    {
        LOG(error) << "Sensor IP is empty" << endl;
        return -1;
    }
    LOG(info) << "Sensor IP: " << sensor.ip << endl;

    if (openProbe() != 0)
    {
        LOG(error) << "openProbe Failed" << endl;
        return -1;
    }

    if (sendProbe(sensor.ip) == 0)
    {
        ret = getProbeMatch(sensor);
    }
    else
    {
        LOG(error) << "Failed to get probematch" << endl;
    }

    closeProbe();

    return ret;
}



int sendProbe(const string& inIpAddress)
{
    int sd;
    struct sockaddr_in groupSock;
    string xmlout;
    int ret = 0;
    const char* ip = inIpAddress.empty() ? PROBE_IP : inIpAddress.c_str();
    int port = PROBE_PORT;

    std::lock_guard<std::mutex> lock(g_probeMutex);

    if (g_probePort.empty())
    {
        LOG(error) << "Probe port is still not opened" << endl;
        return -1;
    }
    for (unsigned pt = 0; pt < g_probePort.size(); pt++)
    {
        sd = g_probePort[pt];
        LOG(verbose) << "PROBE PORT FD : " << sd << " ip: "<< ip << endl;
        if (!inIpAddress.empty())
        {
            struct ip_mreq group = {};
            if(setsockopt(sd, IPPROTO_IP, IP_DROP_MEMBERSHIP, (char *)&group, sizeof(group)) < 0)
            {
                LOG(error) << "Error in Dropping multicast group " << endl;
            }
            else
            {
                LOG(verbose) << "Dropping multicast group...OK." << endl;
            }
        }

        memset((char *) &groupSock, 0, sizeof(groupSock));
        groupSock.sin_family = AF_INET;
        groupSock.sin_addr.s_addr = inet_addr(ip);
        groupSock.sin_port = htons(port);

        xmlout = composeProbeXml();
        LOG(verbose2) << xmlout << endl;

        if(sendto(sd, xmlout.c_str(), xmlout.size(), 0, (struct sockaddr*)&groupSock, sizeof(groupSock)) < 0)
        {
            LOG(error) << "Sending datagram message error" << endl;
            ret = -1;
        }
        else
        {
            LOG(verbose) << "Sending datagram message...OK" << endl;
        }
    }

    return ret;
}


static bool checkIfValidInterface(vector<string> user_list)
{
    bool validIface = false;
    vector<string> nw_list = getNwInterfaceList();
    for (auto iface : user_list)
    {
        vector<string>::iterator it;
        it = std::find (nw_list.begin(), nw_list.end(), iface);
        if (it != nw_list.end())
        {
            validIface = true;
        }
    }
    return validIface;
}

int openProbe()
{
    int port = PROBE_PORT;
    int ret = 0;
    string mcast_ip = PROBE_IP;
    string xmlout;
    ifconf ifc;
    ifreq* item;
    char buf[1024];
    string iface_ipAddr = to_string(INADDR_ANY);
    bool iface_found = false;

    DeviceConfig& config = GET_CONFIG();
    vector<string> net_interfaces = config.sensor_discovery_interfaces;

    std::lock_guard<std::mutex> lock(g_probeMutex);
    if (net_interfaces.empty() || !checkIfValidInterface(net_interfaces))
    {
        LOG(error) << "Either interface list is empty or wrong interfaces provided, use INADDR_ANY" << endl;
        net_interfaces.clear();
        net_interfaces.push_back("INADDR_ANY");
        iface_found = true;
    }
    for (unsigned pt = 0; pt < net_interfaces.size(); pt++)
    {
        int sd = -1, socket_reuse = -1;
        ret = 0;
        char loopch = config.enable_loopback_multicast ? 1 : 0;

        sd = socket(AF_INET, SOCK_DGRAM | SOCK_CLOEXEC, IPPROTO_UDP);
        if(sd < 0)
        {
            LOG(error) << "Opening datagram socket error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
        {
            LOG(info) << "Opening the datagram socket...OK." << endl;
        }

        // Get the ip address of network interface.
        ifc.ifc_len = sizeof(buf);
        ifc.ifc_buf = buf;
        if(ioctl(sd, SIOCGIFCONF, &ifc) < 0)
        {
            LOG(error) << "cannot get interface list" << endl;
        }
        else
        {
            for(unsigned int i = 0; i < ifc.ifc_len / sizeof(ifreq); i++)
            {
                item = &ifc.ifc_req[i];
                if (item && item->ifr_name == net_interfaces[pt])
                {
                    iface_ipAddr = inet_ntoa(((sockaddr_in*)&item->ifr_addr)->sin_addr);
                    iface_found = true;
                    break;
                }
            }
        }

        if (iface_found == false)
        {
            LOG(error) << "Wrong network interface provided:" << net_interfaces[pt] << endl;
            close(sd);
            continue;
        }
        iface_found = false;

        /*
        * Disable loopback so you do not receive your own datagrams.
        */
        if (setsockopt(sd, IPPROTO_IP, IP_MULTICAST_LOOP, (char *)&loopch, sizeof(loopch)) < 0)
        {
            LOG(error) << "Error setting IP_MULTICAST_LOOP:" << endl;
            ret = -1;
            goto cleanup;
        }

        LOG(info) << "Loopback multicast: " << config.enable_loopback_multicast << endl;

        if (net_interfaces[pt] != "INADDR_ANY")
        {
            struct ifreq ifr;
            memset(&ifr, 0, sizeof(ifr));
            memcpy(ifr.ifr_name, net_interfaces[pt].c_str(), sizeof(ifr.ifr_name));
            if ((setsockopt(sd, SOL_SOCKET, SO_BINDTODEVICE, (void *)&ifr, sizeof(ifr))) < 0)
            {
                LOG(error) << "Server-setsockopt() error for SO_BINDTODEVICE: "<< strerror(errno) << endl;
                ret = -1;
                goto cleanup;
            }
        }
        LOG(info) << "PROBE PORT FD : " << sd << ", interface:" << net_interfaces[pt] << endl;
        g_probePort.push_back(sd);

        // set SO_REUSEADDR option before bind
        if (setsockopt(sd, SOL_SOCKET, SO_REUSEADDR, (char *)&socket_reuse, sizeof(socket_reuse)) < 0)
        {
            LOG(error) << "Setting SO_REUSEADDR error" << endl;
            goto cleanup;
        }
        else
        {
            LOG(info) << "Setting SO_REUSEADDR...OK." << endl;
        }

        struct sockaddr_in localSock;
        memset((char *) &localSock, 0, sizeof(localSock));
        localSock.sin_family = AF_INET;
        localSock.sin_port = htons(port);
        localSock.sin_addr.s_addr = iface_ipAddr.empty() ? INADDR_ANY : inet_addr(iface_ipAddr.c_str());

        if(bind(sd, (struct sockaddr*)&localSock, sizeof(localSock)))
        {
            LOG(error) << "Binding datagram socket error" << endl;
            ret = -1;
            goto cleanup;
        }
        else
        {
            LOG(info) << "Binding datagram socket...OK." << endl;
        }

        struct ip_mreq group;
        group.imr_multiaddr.s_addr = inet_addr(PROBE_IP);
        group.imr_interface.s_addr = inet_addr(iface_ipAddr.c_str());
        if(setsockopt(sd, IPPROTO_IP, IP_ADD_MEMBERSHIP, (char *)&group, sizeof(group)) < 0)
        {
            LOG(error) << "Error in Adding multicast group " << endl;
        }
        else
        {
            LOG(verbose) << "Adding multicast group ...OK. for interface:" << net_interfaces[pt] << endl;
        }
        continue;
cleanup:
        if (sd != -1)
        {
            close(sd);
        }
    }
    // Dummy pipe desciptor to terminate the listener thread.
    if (ret == 0 && pipe(g_fdCtrl) < 0)
    {
        perror("pipe error");
        LOG(error) << "Failed to create pipe g_fdCtrl" << endl;
    }
    return ret;
}

void closeProbe()
{
    for (auto pt : g_probePort)
    {
        close(pt);
    }
    g_probePort.clear();
}

int getProbeMatch(SensorInfo& sensor)
{
    int max_sd;
    int ret = 0;
    string mcast_ip = PROBE_IP;
    string xmlout;
    fd_set readfds;
    struct timeval      timeout;
    DeviceConfig& config = GET_CONFIG();
    timeout.tv_sec  = config.sensor_discovery_timeout;
    timeout.tv_usec = 0;

    if (g_probePort.empty())
    {
        LOG(error) << "Probe port is still not opened" << endl;
        return -1;
    }

    FD_ZERO(&readfds);
    max_sd = g_fdCtrl[0];
    for (auto sd : g_probePort)
    {
        FD_SET(sd, &readfds);
        if (sd > max_sd)
            max_sd = sd;
    }
    FD_SET(g_fdCtrl[0], &readfds);
    {
        struct sockaddr_in cliaddr;
        memset(&cliaddr, 0, sizeof(cliaddr));
        int n;
        socklen_t length;
        length = sizeof(cliaddr);
        int buffer_len = 1024 * 10;
        char buffer[buffer_len];
        LOG(verbose2) << "Checking data from socket (select)..." << endl;
        int rc = select(max_sd + 1, &readfds, nullptr, nullptr, &timeout);
        if (rc < 0 )
        {
            LOG(error) << "Select on get probeMatch failed" << endl;
            ret = -1;
            goto cleanup;
        }
        if (rc == 0 )
        {
            LOG(verbose) << "Select on get probeMatch timeout: " << endl;
            goto cleanup;
        }

        // Check if exit message is received from pipe.
        if (FD_ISSET(g_fdCtrl[0], &readfds))
        {
            int msg_len = 1024;
            char message[msg_len];
            if (read(g_fdCtrl[0], message, msg_len) < 0)
            {
                LOG(error) << "read from pipe failed" << endl;
                return -1;
            }
            else
            {
                string objMsg (message);
                if (objMsg == EXIT_MESSAGE)
                {
                    LOG(info) << "Received exit message, terminate the thread" << endl;
                    close(g_fdCtrl[0]);
                    close(g_fdCtrl[1]);
                    return -1;
                }
            }
        }

        int sd_set = -1;
        for (auto sd : g_probePort)
        {
            if (FD_ISSET(sd, &readfds))
            {
                sd_set = sd;
                break;
            }
        }

        LOG(verbose2) << "Reading data from socket (recvfrom)..." << endl;
        n = recvfrom(sd_set, (char *)buffer, buffer_len,
                MSG_WAITALL, ( struct sockaddr *) &cliaddr,
                &length);
        if (n < 0)
        {
			if (errno != EINTR)
            {
				perror("recvfrom");
            }
            goto cleanup;
        }
        buffer[n] = '\0';
        string match (buffer);
        LOG(verbose2) << "probe Match: " << getCurrentTime() << endl << match << endl;

        if (getProbeResponse(match, sensor) == false)
        {
            ret = -1;
        }
    }
cleanup:
    return ret;
}

int stopOnvifDiscovery()
{
    int result = 0;
    string bye_message = EXIT_MESSAGE;

    std::lock_guard<std::mutex> lock(g_probeMutex);
    result = write (g_fdCtrl[1], bye_message.c_str(), sizeof(bye_message));
    if (result < 0)
    {
        LOG(error) << ("writting by-message failed") << endl;
        return -1;
    }
    return 0;
}

static size_t curlWriteCallback(void *contents, size_t size, size_t nmemb, void *userp)
{
    ((std::string*)userp)->append((char*)contents, size * nmemb);
    return size * nmemb;
}

bool curlGetRequest(const string& url, long& http_code)
{
    CURL *curl;
    CURLcode res = CURLE_OK;
    std::string response;
    bool ret = false;

    curl = curl_easy_init();
    if (curl == nullptr)
    {
        LOG(error) << "Unable to initialise Curl" << endl;
        return false;
    }
    res = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    /*Set curl request timeout 10secs*/
    res = curl_easy_setopt(curl, CURLOPT_TIMEOUT, 5L);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    res = curl_easy_perform(curl);
    if (res != CURLE_OK)
    {
        LOG(error) << "Curl call failed for url:" << url << endl;
        ret = false;
        goto cleanup;
    }
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
    ret = true;

cleanup:
    curl_easy_cleanup(curl);
    return ret;
}

bool curlGetRequest(const string& url, const string& username,
                              const string& password, string& outData)
{
    bool ret = false;
    CURLcode errCode = CURLE_OK;

    CURL* curl = curl_easy_init();
    if(!curl)
    {
        cerr << "Curl initialzation failed" <<endl;
    }

    string credentials = username + ":" + password;
    errCode = curl_easy_setopt(curl, CURLOPT_USERPWD, credentials.c_str());
    /* Securely erase credentials from memory after use */
    std::fill(credentials.begin(), credentials.end(), '\0');
    credentials.clear();
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)
    errCode = curl_easy_setopt(curl, CURLOPT_HTTPAUTH, (long)CURLAUTH_DIGEST);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    /*Set curl request timeout 10secs*/
    errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    unique_ptr<string> httpData(new string());
    // Hook up data handling function.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, callback);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)
    // Hook up data container (will be passed as the last parameter to the
    // callback handling function).  Can be any pointer type, since it will
    // internally be passed as a void pointer.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEDATA, httpData.get());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Response information.
    long httpCode(0);
    // Run our HTTP GET command, capture the HTTP response code, and clean up.
    errCode = curl_easy_perform(curl);
    if(errCode == CURLE_OK)
    {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);
        curl_easy_cleanup(curl);

        if (httpCode == 200)
        {
            outData = *httpData.get();
            ret = true;
        }
        else
        {
            LOG(error) << "Http error code: " << httpCode << " url:" << url << endl;
        }
    }
    else
    {
        LOG(error) << "CURL Request failed: " << errCode << " : " << curl_easy_strerror(errCode) << " url:" << url << endl;
    }

    curl_global_cleanup();
    return ret;
}

bool curlPostRequest(const string& httpUrl, const string& username, const string& password,
    const string& rest_api, const string& params, string& response, bool is_digest, vector<string> headers)
{
    CURL *curl;
    CURLcode res = CURLE_OK;
    std::string responseBuffer;
    bool ret = false;

    curl = curl_easy_init();
    if (curl == nullptr)
    {
        LOG(error) << "Unable to initialise Curl" << endl;
        return false;
    }

    struct curl_slist* headers_list = nullptr;
    for (auto header : headers)
    {
        headers_list = curl_slist_append(headers_list, header.c_str());
    }
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers_list);

    string credentials = username + ":" + password;
    res = curl_easy_setopt(curl, CURLOPT_URL, httpUrl.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    long auth_type = is_digest ? (long)CURLAUTH_DIGEST : long(CURLAUTH_BASIC);
    res = curl_easy_setopt(curl, CURLOPT_HTTPAUTH, auth_type);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    res = curl_easy_setopt(curl, CURLOPT_USERPWD, credentials.c_str());
    /* Securely erase credentials from memory after use */
    std::fill(credentials.begin(), credentials.end(), '\0');
    credentials.clear();
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    if(rest_api.empty() == false)
    {
        res = curl_easy_setopt(curl, CURLOPT_URL, rest_api.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    }
    if(params.empty() == false)
    {
        res = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, params.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, res, false)
        res = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, params.length());
        CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    }

    res = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, curlWriteCallback);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    res = curl_easy_setopt(curl, CURLOPT_WRITEDATA, &responseBuffer);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    LOG(verbose) << "curl_easy_perform .." <<  endl;
    res = curl_easy_perform(curl);
    if (res != CURLE_OK)
    {
        LOG(error) << "Curl call failed for url:" << httpUrl << endl;
        ret = false;
        goto cleanup;
    }
    else
    {
        long http_code;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
    }
    LOG(verbose) << "Server response = " << responseBuffer << endl;
    response = responseBuffer;
    ret = true;

cleanup:
    curl_easy_cleanup(curl);
    return ret;
}

bool curlPostRequest_2(const string& httpUrl, vector<string> headers, string& response)
{
    CURL *curl;
    CURLcode res = CURLE_OK;
    std::string responseBuffer;
    bool ret = false;
    long http_code;

    curl = curl_easy_init();
    if (curl == nullptr)
    {
        LOG(error) << "Unable to initialise Curl" << endl;
        return false;
    }

    curl_easy_setopt(curl, CURLOPT_URL, httpUrl.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    struct curl_slist* headers_list = nullptr;
    for (auto header : headers)
    {
        headers_list = curl_slist_append(headers_list, header.c_str());
    }
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers_list);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    curl_easy_setopt(curl, CURLOPT_POST, 1);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    res = curl_easy_setopt(curl, CURLOPT_HEADERFUNCTION, curlWriteCallback);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    res = curl_easy_setopt(curl, CURLOPT_HEADERDATA, &responseBuffer);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    res = curl_easy_perform(curl);
    if (res != CURLE_OK)
    {
        LOG(error) << "Curl call failed for url:" << httpUrl << endl;
        ret = false;
        goto cleanup;
    }
    else
    {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
    }
    response = responseBuffer;
    ret = true;

cleanup:
    curl_easy_cleanup(curl);
    return ret;
}

bool curlDeleteRequest(const string& httpUrl, vector<string> headers, string& response)
{
    CURL *curl;
    CURLcode res = CURLE_OK;
    std::string responseBuffer;
    bool ret = false;
    long http_code;

    curl = curl_easy_init();
    if (curl == nullptr)
    {
        LOG(error) << "Unable to initialise Curl" << endl;
        return false;
    }

    curl_easy_setopt(curl, CURLOPT_URL, httpUrl.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    struct curl_slist* headers_list = nullptr;
    for (auto header : headers)
    {
        headers_list = curl_slist_append(headers_list, header.c_str());
    }
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers_list);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, "DELETE");
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    res = curl_easy_setopt(curl, CURLOPT_HEADERFUNCTION, curlWriteCallback);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)
    res = curl_easy_setopt(curl, CURLOPT_HEADERDATA, &responseBuffer);
    CURL_CHECK_ERROR(curl_easy_setopt, res, false)

    res = curl_easy_perform(curl);
    if (res != CURLE_OK)
    {
        LOG(error) << "Curl call failed for url:" << httpUrl << endl;
        ret = false;
        goto cleanup;
    }
    else
    {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &http_code);
    }
    response = responseBuffer;
    ret = true;

cleanup:
    curl_easy_cleanup(curl);
    return ret;
}

bool isRtspServerReachable(const string& rtsp_server, bool is_url_provided)
{
    bool ret = false;
    CURLcode errCode = CURLE_OK;
    if (rtsp_server.empty())
    {
        return false;
    }

    string rtsp_server_url;
    if (is_url_provided)
    {
        rtsp_server_url = rtsp_server;
    }
    else
    {
        rtsp_server_url = "rtsp://" + rtsp_server; // Assuming port 554
    }

    CURL *curl = curl_easy_init();
    if (curl)
    {
        errCode = curl_easy_setopt(curl, CURLOPT_URL, rtsp_server_url.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

        errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, 1L); //1sec timeout
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

        errCode = curl_easy_setopt(curl, CURLOPT_RTSP_REQUEST, CURL_RTSPREQ_OPTIONS);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

        // Execute the rtsp oprion request.
        errCode = curl_easy_perform(curl);
        if (errCode != CURLE_OK)
        {
            // Ignore few curl errors
            if (errCode == CURLE_RTSP_CSEQ_ERROR || errCode == CURLE_RECV_ERROR)
            {
                errCode = CURLE_OK;
                ret = true;
                goto cleanup;
            }
            ret = false;
            goto cleanup;
        }
        ret = true;
    }
cleanup:
    if (curl)
    {
        curl_easy_cleanup(curl);
    }
    return ret;
}

bool portInTimedWaitState(const std::string& line)
{
    std::istringstream iss(line);
    std::string token;
    /* Skip the first few columns we don't need */
    for (int i = 0; i < 3; ++i)
    {
        iss >> token;
    }
    /* Get the port state column */
    iss >> token;
    return token == "06"; // "06" represents TIME_WAIT state
}

int checkIfPortAvailable(const int& port, const string& proto)
{
    int ret = 0;
    string proc_filename = "/proc/net/udp";
    if (proto == "tcp")
    {
        proc_filename = "/proc/net/tcp";
    }
    string port_hex = string(":") + decimalToHex(port);
    std::ifstream file(proc_filename);
    if (file.is_open())
    {
        std::string line;
        while (std::getline(file, line))
        {
            if (findStringIgnoreCase(line, port_hex) == true)
            {
                if (proto == "tcp" && portInTimedWaitState(line))
                {
                    /* Check if port is in timed_wait state */
                    LOG(warning) << "Port " << port << " is TIMED_WAIT, Reusing it\n" << endl;
                    return PORT_TIMED_WAIT_STATE;
                }
                LOG(warning) << "Port " << port << " is not free, use another port\n" << endl;
                ret = 1; /* PortNotAvailable - Not a error*/
                break;
            }
        }
        file.close();
    }
    else
    {
        LOG(error) << "Error opening file = " << proc_filename << endl;
        ret = -1;
    }
    return ret;
}

// vector<string> customHeaders = {"Authorization: Bearer YOUR_ACCESS_TOKEN", "Custom-Header: Value"};
bool curlGetRequest(const string url, string& outData, const vector<string>& customHeaders)
{
    bool ret = false;
    CURLcode errCode = CURLE_OK;

    CURL* curl = curl_easy_init();
    if(!curl)
    {
        cerr << "Unable to initialize Curl" << endl;
        return false;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Set custom headers if the headers vector is not empty.
    struct curl_slist* headers = nullptr;
    if (!customHeaders.empty())
    {
        for (const string& header : customHeaders)
        {
            headers = curl_slist_append(headers, header.c_str());
        }
        errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        if (errCode != CURLE_OK)
        {
            cerr << "Error setting custom headers: " << curl_easy_strerror(errCode) << endl;
            return false;
        }
    }

    /* Set curl request timeout to 10 seconds */
    errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    unique_ptr<string> httpData(new string());
    // Hook up data handling function.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, callback);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)
    // Hook up data container (will be passed as the last parameter to the
    // callback handling function). Can be any pointer type since it will
    // internally be passed as a void pointer.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEDATA, httpData.get());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Response information.
    long httpCode(0);
    // Run our HTTP GET command, capture the HTTP response code, and clean up.
    errCode = curl_easy_perform(curl);
    if(errCode == CURLE_OK)
    {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);

        if (httpCode == 200)
        {
            outData = *httpData.get();
            ret = true;
        }
        else
        {
            LOG(error) << "Http error code: " << httpCode << " url:" << url << endl;
        }
    }
    else
    {
        cerr << "CURL Request failed: " << errCode << " : " << curl_easy_strerror(errCode) << " url:" << url << endl;
    }

    curl_easy_cleanup(curl);
    curl_slist_free_all(headers); // Clean up custom headers
    return ret;
}

bool curlGetRequest(const string url, string& outData)
{
    bool ret = false;
    CURLcode errCode = CURLE_OK;

    CURL* curl = curl_easy_init();
    if(!curl)
    {
        LOG(error) << "Unable to initialise Curl" << endl;
        return false;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    /*Set curl request timeout 10secs*/
    errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    unique_ptr<string> httpData(new string());
    // Hook up data handling function.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, callback);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)
    // Hook up data container (will be passed as the last parameter to the
    // callback handling function).  Can be any pointer type, since it will
    // internally be passed as a void pointer.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEDATA, httpData.get());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Response information.
    long httpCode(0);
    // Run our HTTP GET command, capture the HTTP response code, and clean up.
    errCode = curl_easy_perform(curl);
    if(errCode == CURLE_OK)
    {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);

        if (httpCode == 200)
        {
            outData = *httpData.get();
            ret = true;
        }
        else
        {
            LOG(error) << "Http error code: " << httpCode << " url:" << url << endl;
        }
    }
    else
    {
        LOG(error) << "CURL Request failed: " << errCode << " : " << curl_easy_strerror(errCode) << " url:" << url << endl;
    }

    curl_easy_cleanup(curl);
    return ret;
}

string getUrlWithQueryParameters(const string& url, const std::map<string, string, std::less<>>& queryParams)
{
    CURL* curl = curl_easy_init();
    if (!curl)
    {
        LOG(error) << "Unable to initialise Curl" << endl;
        return url;
    }
    // Construct valid URL with query parameters
    string fullUrl = url;
    if (!queryParams.empty())
    {
        fullUrl += "?";
        for (const auto& param : queryParams)
        {
            char* escapedKey = curl_easy_escape(curl, param.first.c_str(), param.first.length());
            char* escapedValue = curl_easy_escape(curl, param.second.c_str(), param.second.length());
            if (escapedKey && escapedValue)
            {
                fullUrl += escapedKey;
                fullUrl += "=";
                fullUrl += escapedValue;
                fullUrl += "&";
            }
            // Cleanup for curl_easy_escape()
            curl_free(escapedKey);
            curl_free(escapedValue);
        }
        // Remove the trailing '&'
        fullUrl.pop_back();
    }
    // Cleanup for curl_easy_init()
    curl_easy_cleanup(curl);
    return fullUrl;
}

bool curlPostRequest(const string url, string& outData, const Json::Value& postData)
{
    bool ret = false;
    CURLcode errCode = CURLE_OK;

    CURL* curl = curl_easy_init();
    if(!curl)
    {
        LOG(error) << "Unable to initialise Curl" << endl;
        return false;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    struct curl_slist *headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    string post_string = jsonToString(postData);
    errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, post_string.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, -1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Don't bother trying IPv6, which would increase DNS resolution time.
    errCode = curl_easy_setopt(curl, CURLOPT_IPRESOLVE, CURL_IPRESOLVE_V4);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Follow HTTP redirects if necessary.
    errCode = curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)



    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    /*Set curl request timeout 10secs*/
    errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    unique_ptr<string> httpData(new string());
    // Hook up data handling function.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, callback);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)
    // Hook up data container (will be passed as the last parameter to the
    // callback handling function).  Can be any pointer type, since it will
    // internally be passed as a void pointer.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEDATA, httpData.get());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Response information.
    long httpCode(0);
    // Run our HTTP GET command, capture the HTTP response code, and clean up.
    errCode = curl_easy_perform(curl);
    if(errCode == CURLE_OK)
    {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);

        if (httpCode == 200)
        {
            outData = *httpData.get();
            ret = true;
        }
        else
        {
            LOG(error) << "Http error code: " << httpCode << " url:" << url << endl;
            ret = false;
        }
    }
    else
    {
        LOG(error) << "CURL Request failed: " << errCode << " : " << curl_easy_strerror(errCode) << " url:" << url << endl;
        ret = false;
    }

    curl_easy_cleanup(curl);
    return ret;
}

// vector<string> customHeaders = {"Authorization: Bearer YOUR_ACCESS_TOKEN", "Custom-Header: Value"};
bool curlPostRequest(const string url, string& outData, const Json::Value& postData, const vector<string>& customHeaders)
{
    bool ret = false;
    CURLcode errCode = CURLE_OK;

    CURL* curl = curl_easy_init();
    if(!curl)
    {
        LOG(error) << "Unable to initialise Curl" << endl;
        return false;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    struct curl_slist *contentHeader = nullptr;
    contentHeader = curl_slist_append(contentHeader, "Content-Type: application/json");
    errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, contentHeader);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    string post_string = jsonToString(postData);
    errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, post_string.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, -1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Don't bother trying IPv6, which would increase DNS resolution time.
    errCode = curl_easy_setopt(curl, CURLOPT_IPRESOLVE, CURL_IPRESOLVE_V4);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Follow HTTP redirects if necessary.
    errCode = curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)



    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Set custom headers if the headers vector is not empty.
    struct curl_slist* headers = nullptr;
    if (!customHeaders.empty())
    {
        for (const string& header : customHeaders)
        {
            headers = curl_slist_append(headers, header.c_str());
        }
        errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        if (errCode != CURLE_OK)
        {
            cerr << "Error setting custom headers: " << curl_easy_strerror(errCode) << endl;
            return false;
        }
    }

    /*Set curl request timeout 10secs*/
    errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    unique_ptr<string> httpData(new string());
    // Hook up data handling function.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, callback);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)
    // Hook up data container (will be passed as the last parameter to the
    // callback handling function).  Can be any pointer type, since it will
    // internally be passed as a void pointer.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEDATA, httpData.get());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, false)

    // Response information.
    long httpCode(0);
    // Run our HTTP GET command, capture the HTTP response code, and clean up.
    errCode = curl_easy_perform(curl);
    if(errCode == CURLE_OK)
    {
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);

        if (httpCode == 200)
        {
            outData = *httpData.get();
            ret = true;
        }
        else
        {
            LOG(error) << "Http error code: " << httpCode << " url:" << url << endl;
            ret = false;
        }
    }
    else
    {
        LOG(error) << "CURL Request failed: " << errCode << " : " << curl_easy_strerror(errCode) << " url:" << url << endl;
        ret = false;
    }

    curl_easy_cleanup(curl);
    curl_slist_free_all(headers); // Clean up custom headers
    return ret;
}

#ifdef DEBUG
static int my_trace(CURL *handle, curl_infotype type,
             char *data, size_t size,
             void *userp)
{
    LOG(verbose) << string(data) << endl;
    return 0;
}
#endif

int curlSendRequest(CurlRequestFields& curlFields, string& outData)
{
    CURLcode errCode = CURLE_OK;
    CURL* curl = curl_easy_init();
    int ret = 0;
    if(!curl)
    {
        LOG(error) << "Curl initialzation failed" <<endl;
        return -1;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, curlFields.m_url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    if (!curlFields.m_jsonData.empty())
    {
        errCode = curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, curlFields.m_method.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }

    struct curl_slist *headers = nullptr;
    if (!curlFields.m_jsonData.empty())
    {
        headers = curl_slist_append(headers, "Content-Type: application/json");
        errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, curlFields.m_jsonData.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, -1L);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        // Don't bother trying IPv6, which would increase DNS resolution time.
        errCode = curl_easy_setopt(curl, CURLOPT_IPRESOLVE, CURL_IPRESOLVE_V4);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        // Follow HTTP redirects if necessary.
        errCode = curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }

    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    /*Set curl request timeout 20secs*/
    if(curlFields.m_timeout == -1)
    {
        errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, GET_CONFIG().onvif_request_timeout_secs);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }
    else
    {
        errCode = curl_easy_setopt(curl, CURLOPT_TIMEOUT, curlFields.m_timeout);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }
#ifdef DEBUG
    struct curlData config;
    errCode = curl_easy_setopt(curl, CURLOPT_DEBUGFUNCTION, my_trace);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    errCode = curl_easy_setopt(curl, CURLOPT_DEBUGDATA, &config);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    errCode = curl_easy_setopt(curl, CURLOPT_VERBOSE, 1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
#endif

    // Response information.
    long httpCode(0);
    CURLcode code;
    unique_ptr<string> httpData(new string());

    // Hook up data handling function.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, callback);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Hook up data container (will be passed as the last parameter to the
    // callback handling function).  Can be any pointer type, since it will
    // internally be passed as a void pointer.
    errCode = curl_easy_setopt(curl, CURLOPT_WRITEDATA, httpData.get());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Run our HTTP GET command, capture the HTTP response code, and clean up.
    code = curl_easy_perform(curl);
    if(code == CURLE_OK)
    {
        if(headers)
        {
            curl_slist_free_all(headers);
            headers = nullptr;
        }
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);
        curlFields.m_httpErrorCode = (int)httpCode;
        curlFields.m_httpErrorString = "No error";
        if (httpCode == 200)
        {
            outData = *httpData.get();
        }
        else
        {
            LOG(error) << "Couldn't GET from " << curlFields.m_url << ", method:" << curlFields.m_method << ", Http errorCode: "<< httpCode << endl;
            if (httpCode == 400 || httpCode == 401 )
            {
                curlFields.m_httpErrorString = "Bad request/Unauthorized error";
            }
            else
            {
                curlFields.m_httpErrorString = "Curl request error";
            }
            ret = -1;
            goto cleanup;
        }
    }
    else
    {
        if (!curlFields.m_jsonData.empty())
        {
            LOG(error) << "Error in json curl" << endl;
            LOG(error) << "CURL Request failed: " << code << " : " << curl_easy_strerror(code) << " httpCode: " << httpCode << std::endl;
            ret = -1;
            goto cleanup;
        }

        httpCode = CURL_REQUEST_TIMEOUT;
        LOG(error) << "CURL Request failed: " << code << " : " << curl_easy_strerror(code) << " httpCode: " << httpCode << ", url:" << curlFields.m_url << ", method:" << curlFields.m_method << endl;
        curlFields.m_httpErrorCode = httpCode; // Assume that server is offline
        curlFields.m_httpErrorString = CURL_REQUEST_TIMEOUT_MSG;
        ret = -1;
        goto cleanup;
    }
cleanup:
    curl_easy_cleanup(curl);
    return ret;
}

bool safeStringEqual(const char* str1, const char* str2)
{
    if (!str1 || !str2)
    {
        return false;
    }
    return strcmp(str1, str2) == 0;
}

bool isValidHttpMethod(const char* method)
{
    if (!method)
    {
        return false;
    }
    static const std::unordered_set<std::string> validMethods = {
        "GET", "POST", "PUT", "DELETE", "HEAD", "OPTIONS", "PATCH"
    };
    
    std::string methodStr(method);
    return validMethods.find(methodStr) != validMethods.end();
}

bool isValidContentLength(long long contentLength, long long maxLength, const char* httpMethod)
{
    if (httpMethod)
    {
        std::string method(httpMethod);
        if (method == "GET" || method == "HEAD" || method == "DELETE" || method == "OPTIONS") 
        {
            return contentLength == -1 || contentLength == 0;
        }
    }
    
    // For POST, PUT, PATCH and other methods: -1 (no content) or valid positive length
    return contentLength == -1 || (contentLength >= 0 && contentLength <= maxLength);
}

std::string safeGetString(const char* ptr, const std::string& defaultValue, bool sanitize)
{
    if (!ptr) return defaultValue;
    
    std::string result(ptr);
    if (sanitize)
    {
        // Remove control characters for security
        result.erase(std::remove_if(result.begin(), result.end(), 
            [](char c) { return c < 32 && c != '\t' && c != '\n' && c != '\r'; }), 
            result.end());
    }
    return result;
}

bool isJsonSafe(const std::string& jsonStr, size_t maxDepth, size_t maxKeys)
{
    if (jsonStr.empty() || jsonStr.size() > MAX_JSON_SIZE_BYTES) 
    { // Max 1MB JSON
        return false;
    }
    
    size_t depth = 0;
    size_t maxDepthSeen = 0;
    size_t keyCount = 0;
    bool inString = false;
    bool escaped = false;
    
    for (size_t i = 0; i < jsonStr.size(); ++i) 
    {
        char c = jsonStr[i];
        
        if (escaped) 
        {
            escaped = false;
            continue;
        }
        
        if (c == '\\' && inString) 
        {
            escaped = true;
            continue;
        }
        
        if (c == '"') 
        {
            inString = !inString;
            continue;
        }
        
        if (inString) 
        {
            continue;
        }
        
        if (c == '{' || c == '[') 
        {
            depth++;
            maxDepthSeen = std::max(maxDepthSeen, depth);
            if (maxDepthSeen > maxDepth) 
            {
                return false;
            }
        } 
        else if (c == '}' || c == ']') 
        {
            if (depth > 0) 
            {
                depth--;
            }
        } 
        else if (c == ':') 
        {
            keyCount++;
            if (keyCount > maxKeys) 
            {
                return false;
            }
        }
    }
    
    return depth == 0; // Balanced brackets
}

Json::CharReaderBuilder createSafeJsonReaderBuilder()
{
    Json::CharReaderBuilder builder;
    
    // Set strict parsing settings to prevent attacks
    builder["collectComments"] = false;           // Don't collect comments
    builder["allowComments"] = false;             // Disallow comments in JSON
    builder["strictRoot"] = true;                 // Require single root element
    builder["allowDroppedNullPlaceholders"] = false; // Disallow dropped nulls
    builder["allowNumericKeys"] = false;          // Disallow numeric object keys
    builder["allowSingleQuotes"] = false;         // Disallow single-quoted strings
    builder["stackLimit"] = MAX_JSON_STACK_LIMIT; // Limit parsing stack depth
    builder["failIfExtra"] = true;                // Fail if extra data after root
    builder["rejectDupKeys"] = true;              // Reject duplicate keys
    builder["allowSpecialFloats"] = false;        // Disallow NaN/Inf floats
    
    return builder;
}

bool validateJsonStructure(const Json::Value& json, size_t maxDepth, size_t currentDepth)
{
    if (currentDepth > maxDepth) 
    {
        return false;
    }
    
    if (json.isObject()) 
    {
        if (json.size() > MAX_JSON_OBJECT_KEYS) 
        { 
            // Max 1000 keys per object
            return false;
        }
        for (const auto& key : json.getMemberNames()) 
        {
            if (key.size() > MAX_JSON_KEY_LENGTH) 
            { 
                // Max key length 256 chars
                return false;
            }
            if (!validateJsonStructure(json[key], maxDepth, currentDepth + 1)) 
            {
                return false;
            }
        }
    } 
    else if (json.isArray()) 
    {
        if (json.size() > MAX_JSON_ARRAY_ELEMENTS) 
        { // Max 10000 array elements
            return false;
        }
        for (const auto& element : json) 
        {
            if (!validateJsonStructure(element, maxDepth, currentDepth + 1)) 
            {
                return false;
            }
        }
    } 
    else if (json.isString()) 
    {
        if (json.asString().size() > MAX_JSON_STRING_LENGTH) 
        { 
            // Max string length 64KB
            return false;
        }
    }
    
    return true;
}
