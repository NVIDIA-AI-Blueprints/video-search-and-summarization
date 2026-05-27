#ifndef AWS_S3_EXPORTS_H
#define AWS_S3_EXPORTS_H

/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */

#if defined(AWS_CRT_USE_WINDOWS_DLL_SEMANTICS) || defined(_WIN32)
#    ifdef AWS_S3_USE_IMPORT_EXPORT
#        ifdef AWS_S3_EXPORTS
#            define AWS_S3_API __declspec(dllexport)
#        else
#            define AWS_S3_API __declspec(dllimport)
#        endif /* AWS_S3_EXPORTS */
#    else
#        define AWS_S3_API
#    endif /*USE_IMPORT_EXPORT */

#else
#    if defined(AWS_S3_USE_IMPORT_EXPORT) && defined(AWS_S3_EXPORTS)
#        define AWS_S3_API __attribute__((visibility("default")))
#    else
#        define AWS_S3_API
#    endif

#endif /* defined(AWS_CRT_USE_WINDOWS_DLL_SEMANTICS) || defined(_WIN32) */

#endif /* AWS_S3_EXPORTS_H */
