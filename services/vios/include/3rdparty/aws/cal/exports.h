#ifndef AWS_CAL_EXPORTS_H
#define AWS_CAL_EXPORTS_H
/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */
#if defined(AWS_CRT_USE_WINDOWS_DLL_SEMANTICS) || defined(_WIN32)
#    ifdef AWS_CAL_USE_IMPORT_EXPORT
#        ifdef AWS_CAL_EXPORTS
#            define AWS_CAL_API __declspec(dllexport)
#        else
#            define AWS_CAL_API __declspec(dllimport)
#        endif /* AWS_CAL_EXPORTS */
#    else
#        define AWS_CAL_API
#    endif /* AWS_CAL_USE_IMPORT_EXPORT */

#else /* defined (AWS_CRT_USE_WINDOWS_DLL_SEMANTICS) || defined (_WIN32) */

#    if defined(AWS_CAL_USE_IMPORT_EXPORT) && defined(AWS_CAL_EXPORTS)
#        define AWS_CAL_API __attribute__((visibility("default")))
#    else
#        define AWS_CAL_API
#    endif

#endif /* defined (AWS_CRT_USE_WINDOWS_DLL_SEMANTICS) || defined (_WIN32) */

#endif /* AWS_CAL_EXPORTS_H */
