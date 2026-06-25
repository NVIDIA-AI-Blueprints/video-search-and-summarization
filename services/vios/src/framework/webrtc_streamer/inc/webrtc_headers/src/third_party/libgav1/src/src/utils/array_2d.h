/*
 * Copyright 2019 The libgav1 Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef LIBGAV1_SRC_UTILS_ARRAY_2D_H_
#define LIBGAV1_SRC_UTILS_ARRAY_2D_H_

#include <cassert>
#include <cstddef>
#include <cstring>
#include <memory>
#include <new>
#include <type_traits>

#include "src/utils/compiler_attributes.h"

namespace libgav1 {

// Exposes a 1D allocated memory buffer as a 2D array.
template <typename T>
class Array2DView {
 public:
  Array2DView() = default;
  Array2DView(int rows, int columns, T* const data) {
    Reset(rows, columns, data);
  }

  // Copyable and Movable.
  Array2DView(const Array2DView& rhs) = default;
  Array2DView& operator=(const Array2DView& rhs) = default;

  void Reset(int rows, int columns, T* const data) {
    rows_ = rows;
    columns_ = columns;
    data_ = data;
  }

  int rows() const { return rows_; }
  int columns() const { return columns_; }

  T* operator[](int row) { return const_cast<T*>(GetRow(row)); }

  const T* operator[](int row) const { return GetRow(row); }

 private:
  const T* GetRow(int row) const {
    assert(row < rows_);
    const ptrdiff_t offset = static_cast<ptrdiff_t>(row) * columns_;
    return data_ + offset;
  }

  int rows_ = 0;
  int columns_ = 0;
  T* data_ = nullptr;
};

// Allocates and owns the contiguous memory and exposes an Array2DView of
// dimension |rows| x |columns|.
template <typename T>
class Array2D {
 public:
  Array2D() = default;

  // Copyable and Movable.
  Array2D(const Array2D& rhs) = default;
  Array2D& operator=(const Array2D& rhs) = default;

  LIBGAV1_MUST_USE_RESULT bool Reset(int rows, int columns,
                                     bool zero_initialize = true) {
    size_ = rows * columns;
    // If T is not a trivial type, we should always reallocate the data_
    // buffer, so that the destructors of any existing objects are invoked.
    if (!std::is_trivial<T>::value || allocated_size_ < size_) {
      // Note: This invokes the global operator new if T is a non-class type,
      // such as integer or enum types, or a class type that is not derived
      // from libgav1::Allocable, such as std::unique_ptr. If we enforce a
      // maximum allocation size or keep track of our own heap memory
      // consumption, we will need to handle the allocations here that use the
      // global operator new.
      if (zero_initialize) {
        data_.reset(new (std::nothrow) T[size_]());
      } else {
        data_.reset(new (std::nothrow) T[size_]);
      }
      if (data_ == nullptr) {
        allocated_size_ = 0;
        return false;
      }
      allocated_size_ = size_;
    } else if (zero_initialize) {
      // Cast the data_ pointer to void* to avoid the GCC -Wclass-memaccess
      // warning. The memset is safe because T is a trivial type.
      void* dest = data_.get();
      memset(dest, 0, sizeof(T) * size_);
    }
    data_view_.Reset(rows, columns, data_.get());
    return true;
  }

  int rows() const { return data_view_.rows(); }
  int columns() const { return data_view_.columns(); }
  size_t size() const { return size_; }
  T* data() { return data_.get(); }
  const T* data() const { return data_.get(); }

  T* operator[](int row) { return data_view_[row]; }

  const T* operator[](int row) const { return data_view_[row]; }

 private:
  std::unique_ptr<T[]> data_;
  size_t allocated_size_ = 0;
  size_t size_ = 0;
  Array2DView<T> data_view_;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_ARRAY_2D_H_
