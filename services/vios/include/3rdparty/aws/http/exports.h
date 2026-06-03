#ifndef AWS_HTTP_EXPORTS_H
#define AWS_HTTP_EXPORTS_H

/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */

#if defined(AWS_CRT_USE_WINDOWS_DLL_SEMANTICS) || defined(_WIN32)
#    ifdef AWS_HTTP_USE_IMPORT_EXPORT
#        ifdef AWS_HTTP_EXPORTS
#            define AWS_HTTP_API __declspec(dllexport)
#        else
#            define AWS_HTTP_API __declspec(dllimport)
#        endif /* AWS_HTTP_EXPORTS */
#    else
#        define AWS_HTTP_API
#    endif /* USE_IMPORT_EXPORT */

#else
#    if defined(AWS_HTTP_USE_IMPORT_EXPORT) && defined(AWS_HTTP_EXPORTS)
#        define AWS_HTTP_API __attribute__((visibility("default")))
#    else
#        define AWS_HTTP_API
#    endif

#endif /* defined(AWS_CRT_USE_WINDOWS_DLL_SEMANTICS) || defined(_WIN32) */

#endif /* AWS_HTTP_EXPORTS_H */
