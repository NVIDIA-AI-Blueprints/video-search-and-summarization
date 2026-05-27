/*
 * SPDX-FileCopyrightText: Copyright (c) 2020-2021 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#pragma once

#include <iostream>
#include "logger.h"

#define CURL_CHECK_ERROR(api_name, err_code, ret_value) \
    if( err_code ) \
    { \
        LOG(error) << "Curl api '" << #api_name << "' failed with error:" << err_code << endl; \
        curl_easy_cleanup(curl); \
        return ret_value; \
    }

#define CURL_CHECK_ERROR2(api_name, err_code) \
    if( err_code ) \
    { \
        LOG(error) << "Curl api '" << #api_name << "' failed with error:" << err_code << endl; \
        curl_easy_cleanup(curl); \
        return; \
    }

#define CURL_CHECK_ERROR_WITHOUT_CLEANUP(api_name, err_code, ret_value) \
    if( err_code ) \
    { \
        LOG(error) << "Curl api '" << #api_name << "' failed with error:" << err_code << endl; \
        return ret_value; \
    }

#define _xmlTextWriterStartElement_(writer, name) \
    { \
        int xmlErr = xmlTextWriterStartElement(writer, name); \
        if (xmlErr < 0) { \
            LOG(error) << "xmlTextWriterStartElement for '" << #name << "' failed with error:" << xmlErr << endl; \
        } \
    }

#define _xmlTextWriterWriteElement_(writer, name, content) \
    { \
        int xmlErr = xmlTextWriterWriteElement(writer, name, content); \
        if (xmlErr < 0) { \
            LOG(error) << "xmlTextWriterWriteElement for '" << #name << "' failed with error:" << xmlErr << endl; \
        } \
    }

#define _xmlTextWriterEndElement_(writer) \
    { \
        int xmlErr = xmlTextWriterEndElement(writer); \
        if (xmlErr < 0) { \
            LOG(error) << "xmlTextWriterEndElement failed with error:" << xmlErr << endl; \
        } \
    }

#define _xmlTextWriterWriteString_(writer, content) \
    { \
        int xmlErr = xmlTextWriterWriteString(writer, content); \
        if (xmlErr < 0) { \
            LOG(error) << "xmlTextWriterWriteString failed with error:" << xmlErr << endl; \
        } \
    }

#define _xmlTextWriterWriteAttribute_(writer, name, content) xmlTextWriterWriteAttribute(writer, name, content)
#define _xmlTextWriterStartDocument_(writer, version, encoding, standalone) xmlTextWriterStartDocument(writer, version, encoding, standalone)

#define _xmlTextWriterEndDocument_(writer) \
    { \
        int xmlErr = xmlTextWriterEndDocument(writer); \
        if (xmlErr < 0) { \
            LOG(error) << "xmlTextWriterEndDocument failed with error:" << xmlErr << endl; \
        } \
    }
