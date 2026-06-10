#pragma once
/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */

#include <type_traits>

namespace Aws
{
    namespace Crt
    {
        /**
         * A type trait for determining if the first template parameter is a template specialization of the second
         * template parameter. Based on p2098 (https://www.open-std.org/jtc1/sc22/wg21/docs/papers/2020/p2098r1.pdf).
         *
         * @note Known limitations: does not support template classes with non-type template parameter, e.g. std::array.
         */
        template <typename T, template <typename...> class Primary> struct IsSpecializationOf : std::false_type
        {
        };

        /* Specialization for the case when the first template parameter is a template specialization of the second
         * template parameter. */
        template <template <typename...> class Primary, typename... Args>
        struct IsSpecializationOf<Primary<Args...>, Primary> : std::true_type
        {
        };
    } // namespace Crt
} // namespace Aws
