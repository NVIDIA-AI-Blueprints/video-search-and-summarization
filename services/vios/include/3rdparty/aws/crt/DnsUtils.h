#pragma once
/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */
#include <aws/crt/Exports.h>

namespace Aws
{
    namespace Crt
    {
        namespace DnsUtils
        {
            AWS_CRT_CPP_API bool IsValidIpV6(const char *host, bool is_uri_encoded);
        } // namespace DnsUtils
    } // namespace Crt
} // namespace Aws
