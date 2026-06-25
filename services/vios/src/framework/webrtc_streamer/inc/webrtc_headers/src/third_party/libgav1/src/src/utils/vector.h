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

// libgav1::Vector implementation

#ifndef LIBGAV1_SRC_UTILS_VECTOR_H_
#define LIBGAV1_SRC_UTILS_VECTOR_H_

#include <cassert>
#include <cstddef>
#include <cstdlib>
#include <cstring>
#include <iterator>
#include <new>
#include <type_traits>
#include <utility>

#include "src/utils/compiler_attributes.h"

namespace libgav1 {
namespace internal {

static constexpr size_t kMinVectorAllocation = 16;

// Returns the smallest power of two greater or equal to 'value'.
inline size_t NextPow2(size_t value) {
  if (value == 0) return 0;
  --value;
  for (size_t i = 1; i < sizeof(size_t) * 8; i *= 2) value |= value >> i;
  return value + 1;
}

// Returns the smallest capacity greater or equal to 'value'.
inline size_t NextCapacity(size_t value) {
  if (value == 0) return 0;
  if (value <= kMinVectorAllocation) return kMinVectorAllocation;
  return NextPow2(value);
}

//------------------------------------------------------------------------------
// Data structure equivalent to std::vector but returning false and to its last
// valid state on memory allocation failure.
// std::vector with a custom allocator does not fill this need without
// exceptions.

template <typename T>
class VectorBase {
 public:
  using iterator = T*;
  using const_iterator = const T*;

  VectorBase() noexcept = default;
  // Move only.
  VectorBase(const VectorBase&) = delete;
  VectorBase& operator=(const VectorBase&) = delete;
  VectorBase(VectorBase&& other) noexcept
      : items_(other.items_),
        capacity_(other.capacity_),
        num_items_(other.num_items_) {
    other.items_ = nullptr;
    other.capacity_ = 0;
    other.num_items_ = 0;
  }
  VectorBase& operator=(VectorBase&& other) noexcept {
    if (this != &other) {
      clear();
      free(items_);
      items_ = other.items_;
      capacity_ = other.capacity_;
      num_items_ = other.num_items_;
      other.items_ = nullptr;
      other.capacity_ = 0;
      other.num_items_ = 0;
    }
    return *this;
  }
  ~VectorBase() {
    clear();
    free(items_);
  }

  // Reallocates just enough memory if needed so that 'new_cap' items can fit.
  LIBGAV1_MUST_USE_RESULT bool reserve(size_t new_cap) {
    if (capacity_ < new_cap) {
      T* const new_items = static_cast<T*>(malloc(new_cap * sizeof(T)));
      if (new_items == nullptr) return false;
      if (num_items_ > 0) {
        if (std::is_trivial<T>::value) {
          // Cast |new_items| and |items_| to void* to avoid the GCC
          // -Wclass-memaccess warning and additionally the
          // bugprone-undefined-memory-manipulation clang-tidy warning. The
          // memcpy is safe because T is a trivial type.
          memcpy(static_cast<void*>(new_items),
                 static_cast<const void*>(items_), num_items_ * sizeof(T));
        } else {
          for (size_t i = 0; i < num_items_; ++i) {
            new (&new_items[i]) T(std::move(items_[i]));
            items_[i].~T();
          }
        }
      }
      free(items_);
      items_ = new_items;
      capacity_ = new_cap;
    }
    return true;
  }

  // Reallocates less memory so that only the existing items can fit.
  bool shrink_to_fit() {
    if (capacity_ == num_items_) return true;
    if (num_items_ == 0) {
      free(items_);
      items_ = nullptr;
      capacity_ = 0;
      return true;
    }
    const size_t previous_capacity = capacity_;
    capacity_ = 0;  // Force reserve() to allocate and copy.
    if (reserve(num_items_)) return true;
    capacity_ = previous_capacity;
    return false;
  }

  // Constructs a new item by copy constructor. May reallocate if
  // 'resize_if_needed'.
  LIBGAV1_MUST_USE_RESULT bool push_back(const T& value,
                                         bool resize_if_needed = true) {
    if (num_items_ >= capacity_ &&
        (!resize_if_needed ||
         !reserve(internal::NextCapacity(num_items_ + 1)))) {
      return false;
    }
    new (&items_[num_items_]) T(value);
    ++num_items_;
    return true;
  }

  // Constructs a new item by copy constructor. reserve() must have been called
  // with a sufficient capacity.
  //
  // WARNING: No error checking is performed.
  void push_back_unchecked(const T& value) {
    assert(num_items_ < capacity_);
    new (&items_[num_items_]) T(value);
    ++num_items_;
  }

  // Constructs a new item by move constructor. May reallocate if
  // 'resize_if_needed'.
  LIBGAV1_MUST_USE_RESULT bool push_back(T&& value,
                                         bool resize_if_needed = true) {
    if (num_items_ >= capacity_ &&
        (!resize_if_needed ||
         !reserve(internal::NextCapacity(num_items_ + 1)))) {
      return false;
    }
    new (&items_[num_items_]) T(std::move(value));
    ++num_items_;
    return true;
  }

  // Constructs a new item by move constructor. reserve() must have been called
  // with a sufficient capacity.
  //
  // WARNING: No error checking is performed.
  void push_back_unchecked(T&& value) {
    assert(num_items_ < capacity_);
    new (&items_[num_items_]) T(std::move(value));
    ++num_items_;
  }

  // Constructs a new item in place by forwarding the arguments args... to the
  // constructor. May reallocate.
  template <typename... Args>
  LIBGAV1_MUST_USE_RESULT bool emplace_back(Args&&... args) {
    if (num_items_ >= capacity_ &&
        !reserve(internal::NextCapacity(num_items_ + 1))) {
      return false;
    }
    new (&items_[num_items_]) T(std::forward<Args>(args)...);
    ++num_items_;
    return true;
  }

  // Destructs the last item.
  void pop_back() {
    --num_items_;
    items_[num_items_].~T();
  }

  // Destructs the item at 'pos'.
  void erase(iterator pos) { erase(pos, pos + 1); }

  // Destructs the items in [first,last).
  void erase(iterator first, iterator last) {
    for (iterator it = first; it != last; ++it) it->~T();
    if (last != end()) {
      if (std::is_trivial<T>::value) {
        // Cast |first| and |last| to void* to avoid the GCC
        // -Wclass-memaccess warning and additionally the
        // bugprone-undefined-memory-manipulation clang-tidy warning. The
        // memmove is safe because T is a trivial type.
        memmove(static_cast<void*>(first), static_cast<const void*>(last),
                (end() - last) * sizeof(T));
      } else {
        for (iterator it_src = last, it_dst = first; it_src != end();
             ++it_src, ++it_dst) {
          new (it_dst) T(std::move(*it_src));
          it_src->~T();
        }
      }
    }
    num_items_ -= std::distance(first, last);
  }

  // Destructs all the items.
  void clear() { erase(begin(), end()); }

  // Destroys (including deallocating) all the items.
  void reset() {
    clear();
    if (!shrink_to_fit()) assert(false);
  }

  // Accessors
  bool empty() const { return (num_items_ == 0); }
  size_t size() const { return num_items_; }
  size_t capacity() const { return capacity_; }

  T* data() { return items_; }
  T& front() { return items_[0]; }
  T& back() { return items_[num_items_ - 1]; }
  T& operator[](size_t i) { return items_[i]; }
  T& at(size_t i) { return items_[i]; }
  const T* data() const { return items_; }
  const T& front() const { return items_[0]; }
  const T& back() const { return items_[num_items_ - 1]; }
  const T& operator[](size_t i) const { return items_[i]; }
  const T& at(size_t i) const { return items_[i]; }

  iterator begin() { return &items_[0]; }
  const_iterator begin() const { return &items_[0]; }
  iterator end() { return &items_[num_items_]; }
  const_iterator end() const { return &items_[num_items_]; }

  void swap(VectorBase& b) {
    // Although not necessary here, adding "using std::swap;" and then calling
    // swap() without namespace qualification is recommended. See Effective
    // C++, Item 25.
    using std::swap;
    swap(items_, b.items_);
    swap(capacity_, b.capacity_);
    swap(num_items_, b.num_items_);
  }

 protected:
  T* items_ = nullptr;
  size_t capacity_ = 0;
  size_t num_items_ = 0;
};

}  // namespace internal

//------------------------------------------------------------------------------

// Vector class that does *NOT* construct the content on resize().
// Should be reserved to plain old data.
template <typename T>
class VectorNoCtor : public internal::VectorBase<T> {
 public:
  // Creates or destructs items so that 'new_num_items' exist.
  // Allocated memory grows every power-of-two items.
  LIBGAV1_MUST_USE_RESULT bool resize(size_t new_num_items) {
    using super = internal::VectorBase<T>;
    if (super::num_items_ < new_num_items) {
      if (super::capacity_ < new_num_items) {
        if (!super::reserve(internal::NextCapacity(new_num_items))) {
          return false;
        }
      }
      super::num_items_ = new_num_items;
    } else {
      while (super::num_items_ > new_num_items) {
        --super::num_items_;
        super::items_[super::num_items_].~T();
      }
    }
    return true;
  }
};

// This generic vector class will call the constructors.
template <typename T>
class Vector : public internal::VectorBase<T> {
 public:
  // Constructs or destructs items so that 'new_num_items' exist.
  // Allocated memory grows every power-of-two items.
  LIBGAV1_MUST_USE_RESULT bool resize(size_t new_num_items) {
    using super = internal::VectorBase<T>;
    if (super::num_items_ < new_num_items) {
      if (super::capacity_ < new_num_items) {
        if (!super::reserve(internal::NextCapacity(new_num_items))) {
          return false;
        }
      }
      while (super::num_items_ < new_num_items) {
        new (&super::items_[super::num_items_]) T();
        ++super::num_items_;
      }
    } else {
      while (super::num_items_ > new_num_items) {
        --super::num_items_;
        super::items_[super::num_items_].~T();
      }
    }
    return true;
  }
};

//------------------------------------------------------------------------------

// Define non-member swap() functions in the namespace in which VectorNoCtor
// and Vector are implemented. See Effective C++, Item 25.

template <typename T>
void swap(VectorNoCtor<T>& a, VectorNoCtor<T>& b) {
  a.swap(b);
}

template <typename T>
void swap(Vector<T>& a, Vector<T>& b) {
  a.swap(b);
}

//------------------------------------------------------------------------------

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_VECTOR_H_
