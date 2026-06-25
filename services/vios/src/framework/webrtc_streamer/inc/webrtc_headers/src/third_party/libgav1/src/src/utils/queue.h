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

#ifndef LIBGAV1_SRC_UTILS_QUEUE_H_
#define LIBGAV1_SRC_UTILS_QUEUE_H_

#include <cassert>
#include <cstddef>
#include <memory>
#include <new>
#include <utility>

#include "src/utils/compiler_attributes.h"

namespace libgav1 {

// A FIFO queue of a fixed capacity.
//
// WARNING: No error checking is performed.
template <typename T>
class Queue {
 public:
  LIBGAV1_MUST_USE_RESULT bool Init(size_t capacity) {
    elements_.reset(new (std::nothrow) T[capacity]);
    if (elements_ == nullptr) return false;
    capacity_ = capacity;
    return true;
  }

  // Pushes the element |value| to the end of the queue. It is an error to call
  // Push() when the queue is full.
  void Push(T&& value) {
    assert(size_ < capacity_);
    elements_[end_++] = std::move(value);
    if (end_ == capacity_) end_ = 0;
    ++size_;
  }

  // Removes the element at the front of the queue. It is an error to call Pop()
  // when the queue is empty.
  void Pop() {
    assert(size_ != 0);
    const T element = std::move(elements_[begin_++]);
    static_cast<void>(element);
    if (begin_ == capacity_) begin_ = 0;
    --size_;
  }

  // Returns a reference to the element at the front of the queue. It is an
  // error to call Front() when the queue is empty.
  T& Front() {
    assert(size_ != 0);
    return elements_[begin_];
  }

  // Returns a reference to the element at the back of the queue. It is an error
  // to call Back() when the queue is empty.
  T& Back() {
    assert(size_ != 0);
    const size_t back = ((end_ == 0) ? capacity_ : end_) - 1;
    return elements_[back];
  }

  // Clears the queue.
  void Clear() {
    while (!Empty()) {
      Pop();
    }
  }

  // Returns true if the queue is empty.
  bool Empty() const { return size_ == 0; }

  // Returns true if the queue is full.
  bool Full() const { return size_ >= capacity_; }

  // Returns the number of elements in the queue.
  size_t Size() const { return size_; }

 private:
  // An array of |capacity| elements. Used as a circular array.
  std::unique_ptr<T[]> elements_;
  size_t capacity_ = 0;
  // The index of the element to be removed by Pop().
  size_t begin_ = 0;
  // The index where the new element is inserted by Push().
  size_t end_ = 0;
  size_t size_ = 0;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_QUEUE_H_
