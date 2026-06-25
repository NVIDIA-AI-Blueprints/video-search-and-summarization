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

#ifndef LIBGAV1_SRC_UTILS_UNBOUNDED_QUEUE_H_
#define LIBGAV1_SRC_UTILS_UNBOUNDED_QUEUE_H_

#include <cassert>
#include <cstddef>
#include <memory>
#include <new>
#include <utility>

#include "src/utils/compiler_attributes.h"
#include "src/utils/memory.h"

namespace libgav1 {

// A FIFO queue of an unbounded capacity.
//
// This implementation uses the general approach used in std::deque
// implementations. See, for example,
// https://stackoverflow.com/questions/6292332/what-really-is-a-deque-in-stl
//
// It is much simpler because it just needs to support the queue interface.
// The blocks are chained into a circular list, not managed by a "map". It
// does not shrink the internal buffer.
//
// An alternative implementation approach is a resizable circular array. See,
// for example, ResizingArrayQueue.java in https://algs4.cs.princeton.edu/code/
// and base::circular_deque in Chromium's base/containers library.
template <typename T>
class UnboundedQueue {
 public:
  UnboundedQueue() = default;

  // Move only.
  UnboundedQueue(UnboundedQueue&& other)
      : first_block_(other.first_block_),
        front_(other.front_),
        last_block_(other.last_block_),
        back_(other.back_) {
    other.first_block_ = nullptr;
    other.front_ = 0;
    other.last_block_ = nullptr;
    other.back_ = 0;
  }
  UnboundedQueue& operator=(UnboundedQueue&& other) {
    if (this != &other) {
      Destroy();
      first_block_ = other.first_block_;
      front_ = other.front_;
      last_block_ = other.last_block_;
      back_ = other.back_;
      other.first_block_ = nullptr;
      other.front_ = 0;
      other.last_block_ = nullptr;
      other.back_ = 0;
    }
    return *this;
  }

  ~UnboundedQueue() { Destroy(); }

  // Allocates two Blocks upfront because most access patterns require at
  // least two Blocks. Returns false if the allocation of the Blocks failed.
  LIBGAV1_MUST_USE_RESULT bool Init() {
    std::unique_ptr<Block> new_block0(new (std::nothrow) Block);
    std::unique_ptr<Block> new_block1(new (std::nothrow) Block);
    if (new_block0 == nullptr || new_block1 == nullptr) return false;
    first_block_ = last_block_ = new_block0.release();
    new_block1->next = first_block_;
    last_block_->next = new_block1.release();
    return true;
  }

  // Checks if the queue has room for a new element. If the queue is full,
  // tries to grow it. Returns false if the queue is full and the attempt to
  // grow it failed.
  //
  // NOTE: GrowIfNeeded() must be called before each call to Push(). This
  // inconvenient design is necessary to guarantee a successful Push() call.
  //
  // Push(T&& value) is often called with the argument std::move(value). The
  // moved-from object |value| won't be usable afterwards, so it would be
  // problematic if Push(T&& value) failed and we lost access to the original
  // |value| object.
  LIBGAV1_MUST_USE_RESULT bool GrowIfNeeded() {
    assert(last_block_ != nullptr);
    if (back_ == kBlockCapacity) {
      if (last_block_->next == first_block_) {
        // All Blocks are in use.
        std::unique_ptr<Block> new_block(new (std::nothrow) Block);
        if (new_block == nullptr) return false;
        new_block->next = first_block_;
        last_block_->next = new_block.release();
      }
      last_block_ = last_block_->next;
      back_ = 0;
    }
    return true;
  }

  // Pushes the element |value| to the end of the queue. It is an error to call
  // Push() when the queue is full.
  void Push(const T& value) {
    assert(last_block_ != nullptr);
    assert(back_ < kBlockCapacity);
    T* elements = reinterpret_cast<T*>(last_block_->buffer);
    new (&elements[back_++]) T(value);
  }

  void Push(T&& value) {
    assert(last_block_ != nullptr);
    assert(back_ < kBlockCapacity);
    T* elements = reinterpret_cast<T*>(last_block_->buffer);
    new (&elements[back_++]) T(std::move(value));
  }

  // Returns the element at the front of the queue. It is an error to call
  // Front() when the queue is empty.
  T& Front() {
    assert(!Empty());
    T* elements = reinterpret_cast<T*>(first_block_->buffer);
    return elements[front_];
  }

  const T& Front() const {
    assert(!Empty());
    T* elements = reinterpret_cast<T*>(first_block_->buffer);
    return elements[front_];
  }

  // Removes the element at the front of the queue from the queue. It is an
  // error to call Pop() when the queue is empty.
  void Pop() {
    assert(!Empty());
    T* elements = reinterpret_cast<T*>(first_block_->buffer);
    elements[front_++].~T();
    if (front_ == kBlockCapacity) {
      // The first block has become empty.
      front_ = 0;
      if (first_block_ == last_block_) {
        // Only one Block is in use. Simply reset back_.
        back_ = 0;
      } else {
        first_block_ = first_block_->next;
      }
    }
  }

  // Returns true if the queue is empty.
  bool Empty() const { return first_block_ == last_block_ && front_ == back_; }

 private:
  // kBlockCapacity is the maximum number of elements each Block can hold.
  // sizeof(void*) is subtracted from 2048 to account for the |next| pointer in
  // the Block struct.
  //
  // In Linux x86_64, sizeof(std::function<void()>) is 32, so each Block can
  // hold 63 std::function<void()> objects.
  //
  // NOTE: The corresponding value in <deque> in libc++ revision
  // 245b5ba3448b9d3f6de5962066557e253a6bc9a4 is:
  //   template <class _ValueType, class _DiffType>
  //   struct __deque_block_size {
  //     static const _DiffType value =
  //         sizeof(_ValueType) < 256 ? 4096 / sizeof(_ValueType) : 16;
  //   };
  //
  // Note that 4096 / 256 = 16, so apparently this expression is intended to
  // ensure the block size is at least 4096 bytes and each block can hold at
  // least 16 elements.
  static constexpr size_t kBlockCapacity =
      (sizeof(T) < 128) ? (2048 - sizeof(void*)) / sizeof(T) : 16;

  struct Block : public Allocable {
    alignas(T) char buffer[kBlockCapacity * sizeof(T)];
    Block* next;
  };

  void Destroy() {
    if (first_block_ == nullptr) return;  // An uninitialized queue.

    // First free the unused blocks, which are located after last_block and
    // before first_block_.
    Block* block = last_block_->next;
    // Cut the circular list open after last_block_.
    last_block_->next = nullptr;
    while (block != first_block_) {
      Block* next = block->next;
      delete block;
      block = next;
    }

    // Then free the used blocks. Destruct the elements in the used blocks.
    while (block != nullptr) {
      const size_t begin = (block == first_block_) ? front_ : 0;
      const size_t end = (block == last_block_) ? back_ : kBlockCapacity;
      T* elements = reinterpret_cast<T*>(block->buffer);
      for (size_t i = begin; i < end; ++i) {
        elements[i].~T();
      }
      Block* next = block->next;
      delete block;
      block = next;
    }
  }

  // Blocks are chained in a circular singly-linked list. If the list of Blocks
  // is empty, both first_block_ and last_block_ are null pointers. If the list
  // is nonempty, first_block_ points to the first used Block and last_block_
  // points to the last used Block.
  //
  // Invariant: If Init() is called and succeeds, the queue is always nonempty.
  // This allows all methods (except the destructor) to avoid null pointer
  // checks for first_block_ and last_block_.
  Block* first_block_ = nullptr;
  // The index of the element in first_block_ to be removed by Pop().
  size_t front_ = 0;
  Block* last_block_ = nullptr;
  // The index in last_block_ where the new element is inserted by Push().
  size_t back_ = 0;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_UNBOUNDED_QUEUE_H_
