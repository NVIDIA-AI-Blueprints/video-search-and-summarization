// This file is part of Eigen, a lightweight C++ template library
// for linear algebra.
//
// Copyright (C) 2014 Benoit Steiner <benoit.steiner.goog@gmail.com>
//
// This Source Code Form is subject to the terms of the Mozilla
// Public License v. 2.0. If a copy of the MPL was not distributed
// with this file, You can obtain one at http://mozilla.org/MPL/2.0/.
// SPDX-License-Identifier: MPL-2.0

#ifndef EIGEN_FIXEDSIZEVECTOR_H
#define EIGEN_FIXEDSIZEVECTOR_H

namespace Eigen {

/** \class MaxSizeVector
 * \ingroup Core_Module
 *
 * \brief The MaxSizeVector class.
 *
 * The %MaxSizeVector provides a subset of std::vector functionality.
 *
 * The goal is to provide basic std::vector operations when using
 * std::vector is not an option (e.g. on GPU or when compiling using
 * FMA/AVX, as this can cause either compilation failures or illegal
 * instruction failures).
 *
 * Beware: The constructors are not API compatible with these of
 * std::vector.
 */
template <typename T>
class MaxSizeVector {
  static const size_t alignment = internal::plain_enum_max(EIGEN_ALIGNOF(T), sizeof(void*));

 public:
  // Construct a new MaxSizeVector, reserve n elements.
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE explicit MaxSizeVector(size_t n)
      : m_reserve(n), m_size(0), m_data(static_cast<T*>(internal::handmade_aligned_malloc(n * sizeof(T), alignment))) {}

  // Construct a new MaxSizeVector, reserve and resize to n.
  // Copy the init value to all elements.
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE MaxSizeVector(size_t n, const T& init)
      : m_reserve(n), m_size(n), m_data(static_cast<T*>(internal::handmade_aligned_malloc(n * sizeof(T), alignment))) {
    size_t i = 0;
    EIGEN_TRY {
      for (; i < m_size; ++i) {
        new (&m_data[i]) T(init);
      }
    }
    EIGEN_CATCH(...) {
      // Construction failed, destruct in reverse order:
      for (; (i + 1) > 0; --i) {
        m_data[i - 1].~T();
      }
      internal::handmade_aligned_free(m_data);
      EIGEN_THROW;
    }
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE ~MaxSizeVector() {
    for (size_t i = m_size; i > 0; --i) {
      m_data[i - 1].~T();
    }
    internal::handmade_aligned_free(m_data);
  }

  void resize(size_t n) {
    eigen_assert(n <= m_reserve);
    for (; m_size < n; ++m_size) {
      new (&m_data[m_size]) T;
    }
    for (; m_size > n; --m_size) {
      m_data[m_size - 1].~T();
    }
    eigen_assert(m_size == n);
  }

  // Append new elements (up to reserved size).
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void push_back(const T& t) {
    eigen_assert(m_size < m_reserve);
    new (&m_data[m_size++]) T(t);
  }

  template <class X>
  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void emplace_back(const X& x) {
    eigen_assert(m_size < m_reserve);
    new (&m_data[m_size++]) T(x);
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE const T& operator[](size_t i) const {
    eigen_assert(i < m_size);
    return m_data[i];
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE T& operator[](size_t i) {
    eigen_assert(i < m_size);
    return m_data[i];
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE T& back() {
    eigen_assert(m_size > 0);
    return m_data[m_size - 1];
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE const T& back() const {
    eigen_assert(m_size > 0);
    return m_data[m_size - 1];
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE void pop_back() {
    eigen_assert(m_size > 0);
    m_data[--m_size].~T();
  }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE size_t size() const { return m_size; }

  EIGEN_DEVICE_FUNC EIGEN_STRONG_INLINE bool empty() const { return m_size == 0; }

  EIGEN_DEVICE_FUNC constexpr T* data() { return m_data; }

  EIGEN_DEVICE_FUNC constexpr const T* data() const { return m_data; }

  EIGEN_DEVICE_FUNC constexpr T* begin() { return m_data; }

  EIGEN_DEVICE_FUNC constexpr T* end() { return m_data + m_size; }

  EIGEN_DEVICE_FUNC constexpr const T* begin() const { return m_data; }

  EIGEN_DEVICE_FUNC constexpr const T* end() const { return m_data + m_size; }

 private:
  size_t m_reserve;
  size_t m_size;
  T* m_data;
};

}  // namespace Eigen

#endif  // EIGEN_FIXEDSIZEVECTOR_H
