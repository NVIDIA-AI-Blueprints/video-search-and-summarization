#pragma once
/**
 * Copyright Amazon.com, Inc. or its affiliates. All Rights Reserved.
 * SPDX-License-Identifier: Apache-2.0.
 */
#include <aws/crt/TypeTraits.h>
#include <aws/crt/Utility.h>
#include <utility>

namespace Aws
{
    namespace Crt
    {
        /**
         * Custom implementation of an Option type.  std::optional requires C++17
         * @tparam T type of the optional value
         */
        template <typename T> class Optional
        {
          public:
            using ValueType = T;

            Optional() : m_value(nullptr) {}
            Optional(const T &val)
            {
                new (m_storage) T(val);
                m_value = reinterpret_cast<T *>(m_storage);
            }

            Optional(T &&val)
            {
                new (m_storage) T(std::forward<T>(val));
                m_value = reinterpret_cast<T *>(m_storage);
            }

            ~Optional()
            {
                if (m_value)
                {
                    m_value->~T();
                }
            }

            /**
             * Assignment operator for a case when the parameter type is not Optional.
             */
            template <
                typename U = T,
                typename std::enable_if<
                    !IsSpecializationOf<typename std::decay<U>::type, Aws::Crt::Optional>::value,
                    bool>::type = true>
            Optional &operator=(U &&u)
            {
                if (m_value)
                {
                    *m_value = std::forward<U>(u);
                    return *this;
                }

                new (m_storage) T(std::forward<U>(u));
                m_value = reinterpret_cast<T *>(m_storage);

                return *this;
            }

            Optional(const Optional<T> &other)
            {
                if (other.m_value)
                {
                    new (m_storage) T(*other.m_value);
                    m_value = reinterpret_cast<T *>(m_storage);
                }
                else
                {
                    m_value = nullptr;
                }
            }

            Optional(Optional<T> &&other)
            {
                if (other.m_value)
                {
                    new (m_storage) T(std::forward<T>(*other.m_value));
                    m_value = reinterpret_cast<T *>(m_storage);
                }
                else
                {
                    m_value = nullptr;
                }
            }

            template <typename... Args> explicit Optional(Aws::Crt::InPlaceT, Args &&...args)
            {
                new (m_storage) T(std::forward<Args>(args)...);
                m_value = reinterpret_cast<T *>(m_storage);
            }

            Optional<T> &operator=(const Optional &other) { return assign(other); }

            template <typename U = T> Optional<T> &operator=(const Optional<U> &other) { return assign(other); }

            template <typename U = T> Optional<T> &operator=(Optional<U> &&other) { return assign(std::move(other)); }

            template <typename... Args> T &emplace(Args &&...args)
            {
                reset();

                new (m_storage) T(std::forward<Args>(args)...);
                m_value = reinterpret_cast<T *>(m_storage);

                return *m_value;
            }

            const T *operator->() const { return m_value; }
            T *operator->() { return m_value; }
            const T &operator*() const & { return *m_value; }
            T &operator*() & { return *m_value; }
            const T &&operator*() const && { return std::move(*m_value); }
            T &&operator*() && { return std::move(*m_value); }

            explicit operator bool() const noexcept { return m_value != nullptr; }
            bool has_value() const noexcept { return m_value != nullptr; }

            T &value() & { return *m_value; }
            const T &value() const & { return *m_value; }

            T &&value() && { return std::move(*m_value); }
            const T &&value() const && { return std::move(*m_value); }

            void reset()
            {
                if (m_value)
                {
                    m_value->~T();
                    m_value = nullptr;
                }
            }

          private:
            template <typename Op> Optional &assign(Op &&other)
            {
                // U is an underlying type of the Optional type passed to this function. Depending on constness of Op,
                // U will be either value or const ref.
                // NOTE: std::is_const<const C&> == false, that's why std::remove_reference is needed here.
                using U = typename std::conditional<
                    std::is_const<typename std::remove_reference<Op>::type>::value,
                    const typename std::decay<Op>::type::ValueType &,
                    typename std::decay<Op>::type::ValueType>::type;

                if ((void *)this == (void *)&other)
                {
                    return *this;
                }

                if (m_value)
                {
                    // Optional<U> is a completely different class from the C++ specifics pov. So, we can use only
                    // public members of `other`.
                    if (other.has_value())
                    {
                        *m_value = std::forward<U>(other.value());
                    }
                    else
                    {
                        m_value->~T();
                        m_value = nullptr;
                    }

                    return *this;
                }

                if (other.has_value())
                {
                    new (m_storage) T(std::forward<U>(other.value()));
                    m_value = reinterpret_cast<T *>(m_storage);
                }

                return *this;
            }

            alignas(T) char m_storage[sizeof(T)];
            T *m_value;
        };
    } // namespace Crt
} // namespace Aws
