/*
 * SPDX-FileCopyrightText: Copyright (c) 2019-2024 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include "milestone_vms.h"
#include "utils.h"
#include "logger.h"
#include "macros.h"

#include <curl/curl.h>
#include <sstream>
#include <iomanip>
#include <uuid/uuid.h>
#include <libxml/encoding.h>
#include <libxml/xmlwriter.h>
#include <string.h>
#include <algorithm>
#include <assert.h>

using namespace std;
using namespace nv_vms;

#define ENCODING "utf-8"

unsigned int max_n_threads = 100;

namespace
{
    static size_t callback(
            const char* in,
            size_t size,
            size_t num,
            string* out)
    {
        const size_t totalBytes(size * num);
        out->append(in, totalBytes);
        return totalBytes;
    }
}

extern "C" ISensorControlInterface* createObject()
{
    return new MilestoneVmsVendor;
}

extern "C" void destroyObject( MilestoneVmsVendor* object )
{
    delete object;
}

class AutoDestroyXml
{
public:
    AutoDestroyXml(xmlBufferPtr xml) :m_xml(xml) {}
    ~AutoDestroyXml() { xmlBufferFree(m_xml); }
private:
    xmlBufferPtr m_xml;
};

struct data
{
  char trace_ascii; /* 1 or 0 */
};

static
int my_trace(CURL *handle, curl_infotype type,
             char *data, size_t size,
             void *userp)
{
    cout << string(data) << endl;
    return 0;
}

static int createAndSendStatusAPIRequest(const string& url, const string& username, const string& password, const string& inData, string& outData)
{
    CURLcode errCode = CURLE_OK;
    errCode = curl_global_init(CURL_GLOBAL_ALL);
    CURL_CHECK_ERROR_WITHOUT_CLEANUP(curl_global_init, errCode, -1)

    CURL* curl = curl_easy_init();
    if(!curl)
    {
        cout << "Curl initialzation failed" <<endl;
        return -1;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    struct curl_slist *headers = nullptr;
    headers = curl_slist_append(headers, "Expect:");
    //headers = curl_slist_append(headers, "Content-Type: text/xml; charset=utf-8");
    headers = curl_slist_append(headers, "Content-Type: application/soap+xml");
    headers = curl_slist_append(headers, "SOAPAction:  \"http://videoos.net/2/XProtectCSRecorderStatus2/StartStatusSession\"");
    headers = curl_slist_append(headers, "Accept: text/plain"); // Example output easier to read as plain text.
    errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    if (!inData.empty())
    {
        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, inData.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, inData.size());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
        //cout << "Posting XML: " << inData << endl;
    }

    errCode = curl_easy_setopt(curl, CURLOPT_HTTPAUTH, (long)CURLAUTH_BASIC);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_USERNAME, username.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_PASSWORD, password.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 1L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    struct data config;
    errCode = curl_easy_setopt(curl, CURLOPT_DEBUGFUNCTION, my_trace);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_DEBUGDATA, &config);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_VERBOSE, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Response information.
    long httpCode(0);
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
    errCode = curl_easy_perform(curl);
    CURL_CHECK_ERROR(curl_easy_perform, errCode, -1)

    errCode = curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);
    CURL_CHECK_ERROR(curl_easy_getinfo, errCode, -1)

    curl_easy_cleanup(curl);

    if (httpCode == 200)
    {
        //cout << "\nGot successful response from " << url << endl << httpCode << *httpData.get() << endl;
        outData = *httpData.get();
    }
    else
    {
        cout << "Couldn't GET from " << url << " - exiting " << "Error code: "<< httpCode << endl;
        return -1;
    }

    return 0;
}

static string compose_soap_auth()
{
  uuid_t uuid;
  uuid_generate_random ( uuid );
  char uid[37];
  uuid_unparse ( uuid, uid );

  int rc;
  xmlTextWriterPtr writer;
  xmlBufferPtr xmlBuf;
  string retString;

  xmlBuf = xmlBufferCreate();
  if (xmlBuf == nullptr)
  {
    cout << "testXmlwriterMemory: Error creating the xml buffer" << endl;
    return retString;
  }

  writer = xmlNewTextWriterMemory(xmlBuf, 0);
  if (writer == nullptr)
  {
      cout << "testXmlwriterMemory: Error creating the xml writer\n" << endl;
      goto cleanup;
  }

  rc = xmlTextWriterStartDocument(writer, nullptr, ENCODING, nullptr);
  _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
  //_xmlTextWriterWriteElement_(writer, ConvertInput("soap:Envelope", ENCODING), ConvertInput("xmlns:xsi=\"http://www.w3.org/2001/XMLSchema-instance\"", ENCODING));
  rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsi", BAD_CAST "http://www.w3.org/2001/XMLSchema-instance");
  if (rc < 0)
  {
    cout <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    goto cleanup;
  }
  rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsd", BAD_CAST "http://www.w3.org/2001/XMLSchema");
  if (rc < 0)
  {
    cout <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    goto cleanup;
  }
  rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://schemas.xmlsoap.org/soap/envelope/");
  if (rc < 0)
  {
    cout <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    goto cleanup;
  }

  _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");
  _xmlTextWriterStartElement_(writer, BAD_CAST "Login");
  rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://videoos.net/2/XProtectCSServerCommand");
  if (rc < 0)
  {
    cout <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    goto cleanup;
  }
  _xmlTextWriterWriteElement_(writer, BAD_CAST "instanceId", BAD_CAST uid);
  _xmlTextWriterWriteElement_(writer, BAD_CAST "currentToken", BAD_CAST uid);
  _xmlTextWriterEndElement_(writer); // "soap:Envelope"
  _xmlTextWriterEndElement_(writer); // "soap:Body"
  _xmlTextWriterEndDocument_(writer); // "Login"
  xmlFreeTextWriter(writer);


  retString = ((char *)xmlBuf->content);

  cleanup:
    xmlBufferFree(xmlBuf);

  return retString;
}

static int createAndSendRequest(const string & url, SoapAuthType type, 
                                            const string& username, const string& password,
                                            const string& inData, string& outData)
{
    CURLcode errCode = CURLE_OK;
    errCode = curl_global_init(CURL_GLOBAL_ALL);
    CURL_CHECK_ERROR_WITHOUT_CLEANUP(curl_global_init, errCode, -1)

    CURL* curl = curl_easy_init();
    if(!curl)
    {
        cout << "Curl initialzation failed" <<endl;
        return -1;
    }

    // Set remote URL.
    errCode = curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    if (!inData.empty())
    {
        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDS, inData.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, inData.size());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }
    if (type == SOAP_AUTH_BASIC)
    {
        errCode = curl_easy_setopt(curl, CURLOPT_HTTPAUTH, (long)CURLAUTH_BASIC);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_USERNAME, username.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_PASSWORD, password.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }
    else if(type == SOAP_AUTH_NTLM)
    {
        errCode = curl_easy_setopt(curl, CURLOPT_HTTPAUTH, (long)CURLAUTH_NTLM);
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_USERNAME, username.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

        errCode = curl_easy_setopt(curl, CURLOPT_PASSWORD, password.c_str());
        CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)
    }

    struct curl_slist *headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: text/xml; charset=utf-8");
    headers = curl_slist_append(headers, "SOAPAction:  \"http://videoos.net/2/XProtectCSServerCommand/IServerCommandService/Login\"");
    headers = curl_slist_append(headers, "Accept: text/plain"); // Example output easier to read as plain text.
    errCode = curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Make the example URL work even if your CA bundle is missing.
    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYPEER, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_SSL_VERIFYHOST, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    struct data config;
    errCode = curl_easy_setopt(curl, CURLOPT_DEBUGFUNCTION, my_trace);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_DEBUGDATA, &config);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    errCode = curl_easy_setopt(curl, CURLOPT_VERBOSE, 0L);
    CURL_CHECK_ERROR(curl_easy_setopt, errCode, -1)

    // Response information.
    long httpCode(0);
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
    errCode = curl_easy_perform(curl);
    CURL_CHECK_ERROR(curl_easy_perform, errCode, -1)

    errCode = curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &httpCode);
    CURL_CHECK_ERROR(curl_easy_getinfo, errCode, -1)

    curl_easy_cleanup(curl);

    if (httpCode == 200)
    {
        //cout << "\nGot successful response from " << url << endl << httpCode << *httpData.get() << endl;
        outData = *httpData.get();
    }
    else
    {
        LOG(error) << "Couldn't GET from " << url << " - exiting " << "Error code: "<< httpCode << endl;
        return -1;
    }

    return 0;
}


static string composeStopStatusSessionXML(const string& token, const string& statusSessionId)
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

    rc = xmlTextWriterStartDocument(writer, nullptr, ENCODING, nullptr);
    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsi", BAD_CAST "http://www.w3.org/2001/XMLSchema-instance");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsd", BAD_CAST "http://www.w3.org/2001/XMLSchema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }


    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");

    _xmlTextWriterStartElement_(writer, BAD_CAST "StopStatusSession");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://videoos.net/2/XProtectCSRecorderStatus2");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }
    _xmlTextWriterWriteElement_(writer, BAD_CAST "token", BAD_CAST token.c_str());
    _xmlTextWriterWriteElement_(writer, BAD_CAST "statusSessionId", BAD_CAST statusSessionId.c_str());
    _xmlTextWriterEndElement_(writer); // "StopStatusSession"
    _xmlTextWriterEndElement_(writer); // "soap:Body"
    _xmlTextWriterEndDocument_(writer); // "Envelope"
    xmlFreeTextWriter(writer);

    retString = ((char *)xmlBuf->content);

    //cout << retString << endl;
    return retString;
}

static string composeGetStatusXML(const string& token, const string& statusSessionId)
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

    rc = xmlTextWriterStartDocument(writer, nullptr, ENCODING, nullptr);
    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsi", BAD_CAST "http://www.w3.org/2001/XMLSchema-instance");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsd", BAD_CAST "http://www.w3.org/2001/XMLSchema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }


    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");

    _xmlTextWriterStartElement_(writer, BAD_CAST "GetStatus");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://videoos.net/2/XProtectCSRecorderStatus2");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }
    _xmlTextWriterWriteElement_(writer, BAD_CAST "token", BAD_CAST token.c_str());
    _xmlTextWriterWriteElement_(writer, BAD_CAST "statusSessionId", BAD_CAST statusSessionId.c_str());
    _xmlTextWriterWriteElement_(writer, BAD_CAST "millisecondsTimeout", BAD_CAST "1000");
    _xmlTextWriterEndElement_(writer); // "GetStatus"
    _xmlTextWriterEndElement_(writer); // "soap:Body"
    _xmlTextWriterEndDocument_(writer); // "Envelope"
    xmlFreeTextWriter(writer);

    retString = ((char *)xmlBuf->content);

    //cout << retString << endl;
    return retString;
}

static string composeGetCurrentStatusXML(const string& token, const vector<string>& camera_ids )
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

    rc = xmlTextWriterStartDocument(writer, nullptr, ENCODING, nullptr);
    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsi", BAD_CAST "http://www.w3.org/2001/XMLSchema-instance");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsd", BAD_CAST "http://www.w3.org/2001/XMLSchema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }


    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");

    _xmlTextWriterStartElement_(writer, BAD_CAST "GetCurrentDeviceStatus");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://videoos.net/2/XProtectCSRecorderStatus2");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }
    _xmlTextWriterWriteElement_(writer, BAD_CAST "token", BAD_CAST token.c_str());
    _xmlTextWriterStartElement_(writer, BAD_CAST "deviceIds");

    std::for_each(camera_ids.begin(), camera_ids.end(), [writer](const string& camera_id)
    {
        _xmlTextWriterWriteElement_(writer, BAD_CAST "guid", BAD_CAST camera_id.c_str());
    });

    _xmlTextWriterEndElement_(writer); // "deviceIds"

    _xmlTextWriterEndElement_(writer); // "GetCurrentDeviceStatus"
    _xmlTextWriterEndElement_(writer); // "soap:Body"
    _xmlTextWriterEndDocument_(writer); // "Envelope"
    xmlFreeTextWriter(writer);

    retString = ((char *)xmlBuf->content);

    //cout << retString << endl;
    return retString;
}

static string composeSubscribeEventStatusXML(const string& token, const string& statusSessionId)
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

    rc = xmlTextWriterStartDocument(writer, nullptr, ENCODING, nullptr);
    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsi", BAD_CAST "http://www.w3.org/2001/XMLSchema-instance");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsd", BAD_CAST "http://www.w3.org/2001/XMLSchema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }


    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");

    _xmlTextWriterStartElement_(writer, BAD_CAST "SubscribeEventStatus");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://videoos.net/2/XProtectCSRecorderStatus2");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }
    _xmlTextWriterWriteElement_(writer, BAD_CAST "token", BAD_CAST token.c_str());
    _xmlTextWriterWriteElement_(writer, BAD_CAST "statusSessionId", BAD_CAST statusSessionId.c_str());
    _xmlTextWriterStartElement_(writer, BAD_CAST "eventIds");
    _xmlTextWriterWriteElement_(writer, BAD_CAST "guid", BAD_CAST "a334af1c-4b4b-4957-9e5f-ab8ca07feab6");
    _xmlTextWriterWriteElement_(writer, BAD_CAST "guid", BAD_CAST "dd3e6464-7dc0-405a-a92f-6150587563e8");
    _xmlTextWriterWriteElement_(writer, BAD_CAST "guid", BAD_CAST "0ee90664-2924-42a0-a816-4129d0ecabdc");
    _xmlTextWriterEndElement_(writer); // "eventIds"
    _xmlTextWriterEndElement_(writer); // "SubscribeEventStatus"
    _xmlTextWriterEndElement_(writer); // "soap:Body"
    _xmlTextWriterEndDocument_(writer); // "Envelope"
    xmlFreeTextWriter(writer);

    retString = ((char *)xmlBuf->content);

    //cout << retString << endl;
    return retString;
}


static string composeStartStatusSessionXML(const string& token)
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

    rc = xmlTextWriterStartDocument(writer, nullptr, ENCODING, nullptr);
    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Envelope");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:soap", BAD_CAST "http://www.w3.org/2003/05/soap-envelope");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsi", BAD_CAST "http://www.w3.org/2001/XMLSchema-instance");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }

    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns:xsd", BAD_CAST "http://www.w3.org/2001/XMLSchema");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }


    _xmlTextWriterStartElement_(writer, BAD_CAST "soap:Body");

    _xmlTextWriterStartElement_(writer, BAD_CAST "StartStatusSession");
    rc = _xmlTextWriterWriteAttribute_(writer, BAD_CAST "xmlns", BAD_CAST "http://videoos.net/2/XProtectCSRecorderStatus2");
    if (rc < 0)
    {
        LOG(error) <<"testXmlwriterMemory: Error at xmlTextWriterWriteAttribute" << endl;
    }
    _xmlTextWriterWriteElement_(writer, BAD_CAST "token", BAD_CAST token.c_str());

    _xmlTextWriterEndElement_(writer); // "StartStatusSession"
    _xmlTextWriterEndElement_(writer); // "soap:Body"
    _xmlTextWriterEndDocument_(writer); // "Envelope"
    xmlFreeTextWriter(writer);

    retString = ((char *)xmlBuf->content);
    return retString;
}

static xmlNodePtr findNode (xmlDocPtr doc, xmlNodePtr cur, const char *inKey)
{
    xmlNodePtr outCur = nullptr;
    cur = cur->xmlChildrenNode;
    while (cur != nullptr)
    {
        if ((!xmlStrcmp(cur->name, (const xmlChar *)inKey)))
        {
            return cur;
        }
        else
        {
            outCur = findNode(doc, cur, inKey);
            if (outCur && (!xmlStrcmp(outCur->name, (const xmlChar *)inKey)))
            {
                break;
            }
        }
        cur = cur->next;
    }
    return outCur;
}

static string getNodeValue (xmlDocPtr doc, xmlNodePtr cur)
{
    string outKey;
    xmlChar *key = xmlNodeListGetString(doc, cur->xmlChildrenNode, 1);
    if (key)
    {
        outKey = (char *)key;
        xmlFree(key);
    }
    return outKey;
}

static string getToken(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cursor;
    string token;

    if (xmlData.empty())
    {
        cout << "xml data is empty" <<endl;
        return token;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cursor = xmlDocGetRootElement(doc);
    if (cursor == nullptr)
    {
        cout << "xml data does not contain camera info" <<endl;
        return token;
    }

    {
        xmlNodePtr cur = findNode(doc, cursor, "LoginResult");
        if (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "Token");
            if (cur_)
            {
              token = getNodeValue(doc, cur_);
            }
        }
    }
    return token;
}

static string getStartStatusSessionResult(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cursor;
    string result;

    if (xmlData.empty())
    {
        cout << "xml data is empty" <<endl;
        return result;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cursor = xmlDocGetRootElement(doc);
    if (cursor == nullptr)
    {
        cout << "xml data does not contain camera info" <<endl;
        return result;
    }

    {
        xmlNodePtr cur = findNode(doc, cursor, "StartStatusSessionResponse");
        if (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "StartStatusSessionResult");
            if (cur_)
            {
              result = getNodeValue(doc, cur_);
            }
        }
    }
    return result;
}

static string getCurrentStatusResult(const string& xmlData, vector<SensorStatus>& status)
{
    xmlDocPtr    doc;
    xmlNodePtr   cursor;
    string result;

    if (xmlData.empty())
    {
        cout << "xml data is empty" <<endl;
        return result;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cursor = xmlDocGetRootElement(doc);
    if (cursor == nullptr)
    {
        cout << "xml data does not contain camera info" <<endl;
        return result;
    }

    {
        xmlNodePtr cur = findNode(doc, cursor, "CameraDeviceStatus");
        while (cur)
        {
            SensorStatus event;
            xmlNodePtr cur_ = findNode(doc, cur, "Time");
            if (cur_)
            {
                event.timeStamp = getNodeValue(doc, cur_);
            }
            cur_ = findNode(doc, cur, "DeviceId");
            if (cur_)
            {
                event.sensorId = getNodeValue(doc, cur_);
            }
            cur_ = findNode(doc, cur, "ErrorNoConnection");
            if (cur_)
            {
                result = getNodeValue(doc, cur_);
                if (result == "false")
                {
                    event.event = SensorStatusOnline;
                }
                else if (result == "true")
                {
                    event.event = SensorStatusOffline;
                }
                else
                {
                    event.event = SensorStatusUnknown;
                }
            }
            status.push_back(event);
            cur = cur->next;
        }
    }
    return result;
}

map<string, string> getEventResult(const string& xmlData)
{
    xmlDocPtr    doc;
    xmlNodePtr   cursor;
    map<string, string> result;

    if (xmlData.empty())
    {
         LOG(error) << "xml data is empty" <<endl;
        return result;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cursor = xmlDocGetRootElement(doc);
    if (cursor == nullptr)
    {
        LOG(error) << "xml data does not contain camera info" <<endl;
        return result;
    }
    {
        xmlNodePtr cur = findNode(doc, cursor, "GetStatusResult");
        while (cur)
        {
            xmlNodePtr cur_ = findNode(doc, cur, "EventStatusArray");
            if (cur_)
            {
                xmlNodePtr cur__ = findNode(doc, cur_, "EventStatus");
                if (cur__)
                {
                    xmlNodePtr cur___ = findNode(doc, cur__, "Time");
                    if (cur___)
                    {
                        result["Time"] = getNodeValue(doc, cur___);
                    }
                    cur___ = findNode(doc, cur__, "EventId");
                    if (cur___)
                    {
                        result["EventId"] = getNodeValue(doc, cur___);
                    }
                    cur___ = findNode(doc, cur__, "SourceId");
                    if (cur___)
                    {
                        result["SourceId"] = getNodeValue(doc, cur___);
                    }
                }
            }
            cur = cur->next;
        }
    }
    return result;
}

static SensorStatusEvent resolveEvent(const string& guid)
{
    if ( (guid == "a334af1c-4b4b-4957-9e5f-ab8ca07feab6") || (guid == "0ee90664-2924-42a0-a816-4129d0ecabdc"))
    {
        return SensorStatusOffline;
    }
    else if (guid == "dd3e6464-7dc0-405a-a92f-6150587563e8")
    {
        return SensorStatusOnline;
    }
    else
    {
        return SensorStatusUnknown;
    }
}

static int refreshToken(const string& baseurl, const string& username, const string& password, string& token)
{
    string xmlData;
    string url = baseurl +  string(":") + string("443") + string("/ManagementServer/ServerCommandService.svc");
    url.replace(0, HTTP.length(), HTTPS);
    string data = compose_soap_auth();
    int ret = createAndSendRequest(url, SOAP_AUTH_BASIC, username, password, data, xmlData);
    if (ret == 0)
    {
        token = getToken(xmlData);
    }
    return ret;
}

int MilestoneVmsVendor::connect()
{
    LOG(info) << __METHOD_NAME__ << endl;
    int ret = refreshToken(m_adaptorInfo.m_url, m_adaptorInfo.m_user, m_adaptorInfo.m_password, m_token);
    if (ret == 0 && m_deviceEventCB != nullptr && m_cameraEvent == nullptr)
    {
       //m_cameraEvent.reset(new MSCameraEvents(m_deviceManager, m_token, m_deviceEventCB));
    }
    return ret;
}

int MilestoneVmsVendor::getSensorStreamInfo(vector<shared_ptr<SensorInfo>>& sensors)
{
    LOG(info) << __FUNCTION__ << endl;
    int ret = -1;
    const string & url = m_adaptorInfo.m_url + string(":") + string("80") + string ("/rcserver/systeminfo.xml");
    string xmlData;
    const string username = ""; //nv_user
    const string password = ""; //nv_password
    ret = createAndSendRequest(url, SOAP_AUTH_NTLM, username, password, "", xmlData);
    if (ret == 0)
    {
        ret = parseCameraInfo(m_adaptorInfo.m_url, xmlData, sensors);
    }
    return ret;
}

int MilestoneVmsVendor::getSensorStreamInfo(shared_ptr<SensorInfo>& sensor)
{
    return 0;
}

int MilestoneVmsVendor::getSensorStatus(const string& cameraId, SensorStatus& status)
{
    assert(m_cameraEvent != nullptr);
    if (m_cameraEvent)
    {
        return m_cameraEvent->getCurrentSensorStatus(cameraId, status);
    }
    return -1;
}

int MilestoneVmsVendor::getSensorStatus(const vector<string>& camera_ids, vector<SensorStatus>& status)
{
    if (m_cameraEvent)
    {
        return m_cameraEvent->getCurrentSensorStatus(camera_ids, status);
    }
    return -1;
}

bool MilestoneVmsVendor::isServerOnline(const string & url)
{
    return true;
}

static void parseNode (xmlDocPtr doc, xmlNodePtr cur, const string& inkey, string& outValue)
{
    cur = cur->xmlChildrenNode;
    while (cur != nullptr)
    {
        if ((!xmlStrcmp(cur->name, (const xmlChar *)inkey.c_str())))
        {
            outValue = getNodeValue(doc, cur);
        }
        cur = cur->next;
    }
    return;
}

int MilestoneVmsVendor::parseCameraInfo(const string& server_url, const string& xmlData,
                            vector<shared_ptr<SensorInfo>>& sensors)
{
    xmlDocPtr    doc;
    xmlNodePtr   cur;

    if (xmlData.empty())
    {
        cout << "xml data is empty" <<endl;
        return -1;
    }

    doc = xmlParseDoc(BAD_CAST xmlData.c_str());
    cur = xmlDocGetRootElement(doc);
    cur = findNode(doc, cur, "cameras");
    if (!cur)
    {
        cout << "xml data does not contain camera info" <<endl;
        return -1;
    }
    cur = findNode(doc, cur, "camera");
    while (cur != nullptr)
    {
        if (cur != nullptr)
        {
            shared_ptr<SensorInfo> sensor(new SensorInfo);
            sensor->user = m_adaptorInfo.m_user;
            sensor->password = m_adaptorInfo.m_password;
            sensor->type = SENSOR_TYPE_RTSP;  // Set sensor type for Milestone VMS cameras
            sensor->updateSensorStatus(SensorStatusOnline);  // Initialize sensor status as online
            shared_ptr<StreamInfo> stream(new StreamInfo);
            xmlAttr* attribute = cur->properties;
            if (attribute && attribute->name && attribute->children)
            {
                string value = (char *)xmlNodeListGetString(doc, attribute->children, 1);
                sensor->name = value;
            }
            string id;
            parseNode(doc, cur, "guid", id);
            stream->live_url = server_url;
            stream->live_url.replace(0, RTSP.length(), RTSP);
            stream->live_url += string(":") + string("554") + string("/live/") + id;
            stream->replay_url = server_url;
            stream->replay_url.replace(0, RTSP.length(), RTSP);
            stream->replay_url += string(":") + string("554") + string("/vod/") + id;
            stream->settings.encoderValues.encoding = "h264";
            stream->id = id;
            sensor->id = id;
            stream->isMainStream = true;  // Mark as main stream so sensor details get inserted into DB
            const std::vector<string> device_id_tokens = splitString(sensor->id, "-");
            if (device_id_tokens.size() > 0)
            {
                sensor->name = sensor->name + string("_") + device_id_tokens[device_id_tokens.size() - 1];
                stream->name = sensor->name;
            }
             if(sensor->user.empty() == false && sensor->password.empty() == false)
            {
                string token("//");
                const string subString = sensor->user + string(":") + sensor->password + string("@");
                insertString( stream->live_url, token, subString);
                insertString( stream->replay_url, token, subString);
            }
            sensor->streams.push_back(move(stream));
            sensor->printInfo();
            sensor->updateHttpErrorStatus(translateVmsErrorCodeToCameraHttpErrorCode(NoError));
            sensors.push_back(sensor);
            cur = cur->next;
        }
    }
    xmlFreeDoc(doc);
    return 0;
}

#if 0
void MilestoneVmsVendor::parseCameraRecordingInfo(const string& server_url, const Json::Value& json, vector<RecordedTimePeriods*>& rec_info)
{
    Json::Value reply = json["reply"][0];
    Json::Value periods = reply["periods"];
    for (int i = 0; i < periods.size(); i++)
    {
        RecordedTimePeriods* info = new RecordedTimePeriods;
        info->duration = stol(periods[i]["durationMs"].asString());;
        info->startTime = stol(periods[i]["startTimeMs"].asString());
        rec_info.push_back(info);
    }
}
#endif

void MSCameraEvents::cameraEventTask()
{
    LOG(verbose) << __FUNCTION__ << endl;
    SensorStatus status;
    subscribeToStatusSession();
    while (getSensorStatus(status) == 0 && m_exit == false)
    {
//#define DEBUG_EVENT
#ifdef DEBUG_EVENT
        cout << "Please enter an event value(0:camera off, 1 :camera on): ";
        int i = 0;
        cin >> i;
        status.cameraId = "befc49c2-0d42-4754-a4d5-f3b4ea53ee5d";
        status.serverId = m_deviceManager->id;
        status.timeStamp = getCurrentTime();
        if (i == 0)
        {
            status.event = CameraStatusOffline;
        }
        else if (i == 1)
        {
            status.event = CameraStatusOnline;
        }
#endif
        if (status.event != SensorStatusUnknown)
        {
            if (m_callback)
            {
                status.type = TYPE_MMS;
                m_callback->onDeviceEvent(status);
            }
        }
    }
    unSubscribeFromStatusSession();
    LOG(info) << "Exiting cameraEventTask ..." <<endl;
}

int MSCameraEvents::getCurrentSensorStatus(const string& camera_id, SensorStatus& status)
{
    int ret = -1;
    string xmlData;
    vector<string> camera_ids;
    camera_ids.push_back(camera_id);
    vector<SensorStatus> status_arr;
    string xml = composeGetCurrentStatusXML(m_token, camera_ids);
    ret = createAndSendStatusAPIRequest(m_url, m_deviceManager->user, m_deviceManager->password, xml, xmlData);
    if (ret == 0)
    {
        getCurrentStatusResult(xmlData, status_arr);
        status = status_arr[0];
    }
    return ret;
}

int MSCameraEvents::getCurrentSensorStatus(const vector<string>& camera_ids, vector<SensorStatus>& status)
{
    int ret = -1;
    string xmlData;
    string xml = composeGetCurrentStatusXML(m_token, camera_ids);
    ret = createAndSendStatusAPIRequest(m_url, m_deviceManager->user, m_deviceManager->password, xml, xmlData);
    if (ret == 0)
    {
        getCurrentStatusResult(xmlData, status);
    }
    return ret;
}

int MSCameraEvents::subscribeToStatusSession()
{
    int ret = -1;
    string xmlData;
    string xml;

    string token = generate_uuid();
    xml = composeStartStatusSessionXML(m_token);
    ret = createAndSendStatusAPIRequest(m_url, m_deviceManager->user, m_deviceManager->password, xml, xmlData);
    if (ret == -1)
    {
        LOG(error) << "StartStatusSession request failed" << endl;
        return ret;
    }
    m_statusSessionId = getStartStatusSessionResult(xmlData);
    xml = composeSubscribeEventStatusXML(m_token, m_statusSessionId);
    ret = createAndSendStatusAPIRequest(m_url, m_deviceManager->user, m_deviceManager->password, xml, xmlData);
    if (ret == -1)
    {
        LOG(error) << "SubscribeEventStatus request failed" << endl;
    }
    return ret;
}

int MSCameraEvents::getSensorStatus(SensorStatus& status)
{
    string xmlData;
    static string xml = composeGetStatusXML(m_token, m_statusSessionId);
    int ret = -1;
    do
    {
        ret = createAndSendStatusAPIRequest(m_url, m_deviceManager->user, m_deviceManager->password, xml, xmlData);
        if (ret == -1)
        {
            LOG(error) << "GetStatus request failed, Try to refresh token" << endl;
            ret = refreshToken(m_deviceManager->url, m_deviceManager->user, m_deviceManager->password, m_token);
            if (ret == 0)
            {
                ret = subscribeToStatusSession();
            }
            if (ret == 0)
            {
                xml = composeGetStatusXML(m_token, m_statusSessionId);
            }
        }
        else
        {
            break;
        }
    } while( ret == 0);
    LOG(verbose) << xmlData << endl;
    map<string, string> result = getEventResult(xmlData);
    status.serverId = m_deviceManager->id;
    status.sensorId = result["SourceId"];
    status.timeStamp = result["Time"];
    status.event = resolveEvent(result["EventId"]);
    LOG(verbose) << "Time: " << result["Time"] << endl;
    LOG(verbose) << "EventId: " << status.event << endl;
    LOG(verbose) << "CameraId: " << result["SourceId"] << endl;
    return ret;
}

int MSCameraEvents::unSubscribeFromStatusSession()
{
    string xmlData;
    refreshToken(m_deviceManager->url, m_deviceManager->user, m_deviceManager->password, m_token);
    string xml = composeStopStatusSessionXML(m_token, m_statusSessionId);
    int ret = createAndSendStatusAPIRequest(m_url, m_deviceManager->user, m_deviceManager->password, xml, xmlData);
    if (ret == -1)
    {
        LOG(error) << "StopStatusSession request failed" << endl;
    }
    return ret;
}

