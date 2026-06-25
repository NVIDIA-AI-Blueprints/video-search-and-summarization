// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

// In-place construction of Rust-movable types which might not be C++-movable.

#ifndef CRUBIT_SUPPORT_INTERNAL_RETURN_VALUE_SLOT_H_
#define CRUBIT_SUPPORT_INTERNAL_RETURN_VALUE_SLOT_H_

#include <array>
#include <cstddef>
#include <memory>
#include <utility>

namespace crubit {

// A type tag for constructors to move-construct via a trivial relocation
// operation, or a Rust move, rather than by running the actual logic of a move
// constructor.
//
// A constructor which accepts `(UnsafeRelocateTag, T&& x)` will relocate `x`
// into the new object, leaving `x` in an uninitialized state. The caller must
// not run the destructor of `x` (or otherwise use it) without first
// reinitializing it.
//
// This can be used, for example, to initialize a value on the stack, and then
// move it into a return value without performing a C++ move operation.
struct UnsafeRelocateTag {};

// `Slot<T>` provides a slot that can store a relocatable return value.
// This class is used to return non-`#[repr(C)]` structs from Rust
// into C++ in a way that is compatible with the ABI of `extern "C"` Rust
// thunks.
//
// An example will help to illustrate the purpose of this class:
//
//     ```rs
//     pub struct SomeStruct(...);
//     pub fn foo(arg1: i32, arg2: i32) -> SomeStruct { unimplemented!() }
//     ```
//
// The generated C++ header will look like this:
//
//     ```cc
//              inline SomeStruct foo(int32_t arg1, int32_t arg2) {
//     /* 1 */    crubit::Slot<SomeStruct> __ret;
//     /* 2 */    __rust_thunk_for_foo(arg1, arg2, __ret.GetSlotPtr());
//     /* 3 */    return __ret.AssumeInitAndTakeValue();
//              }
//     }
// ```
//
// `Slot` helps to coordinate when C++ constructors and destructors
// run in the example above:
// - `SomeStruct`'s constructor should *not* run on line 1.
// - Rust thunk can populates the return slot on line 2.
//   The Rust thunk may panic without populating the return slot - in this
//   case nothing should operate on the uninitialized `SomeStruct` value
//   (this is accomplished by Slot having an empty/no-op destructor)
// - `SomeStruct`'s move constructor will run on line 3 (moving the return value
//   out of `Slot::value_`, and then destructing the moved-away
//   `Slot::value_`).
//
// Behavior of `Slot<T>` in steps 1 and 2 is identical to
// `MaybeUninit<T>` in Rust, but the behavior on line 3 is a bit different:
// there is an extra call to a move constructor in C++, but there are no move
// constructors in Rust.
template <typename T>
class Slot {
 public:
  // Creates `Slot` in an uninitialized state.
  Slot() {
    // Leaving `value_` uninitialized / not invoking any constructor of `T`.
  }

  // Creates `Slot` with the given value.
  explicit constexpr Slot(T&& x) : value_(std::move(x)) {}

  // Gets a pointer to the slot where the return value may be written.
  //
  // SAFETY REQUIREMENTS:
  // - Caller should not read from the returned pointer before the value has
  //   been initialized.
  // - Caller should only write to the returned pointer while the
  //   `Slot` is in an uninitialized state (i.e. care should be taken
  //   to avoid writing to the slot twice, potentially overwriting a value
  //   without calling its destructor).
  T* Get() { return &value_; }

  // Destructively takes and returns the contained value, using relocation if
  // possible, or moves if not.
  //
  // Leaves the value contained in `Slot` uninitialized.
  //
  // SAFETY REQUIREMENTS: The contained value is initialized.
  T AssumeInitAndTakeValue() && {
    if constexpr (requires(T x) { T(UnsafeRelocateTag{}, std::move(x)); }) {
      return T(UnsafeRelocateTag{}, std::move(value_));
    } else {
      T return_value(std::move(value_));
      std::destroy_at(&value_);
      return return_value;
    }
  }

  // SAFETY REQUIREMENTS: The value contained in `other` must be initialized
  // (but may be moved-from).
  Slot(Slot&& other) { value_ = std::move(other.value_); }

  // Does not destroy the contained value.
  //
  // Before `~Slot()` is invoked, the contained value should be
  // destroyed by the user (typically, by calling `AssumeInitAndTakeValue`).  If
  // the contained value is left initialized by the time `~Slot()`
  // runs, the value is leaked.
  ~Slot() {
    // Not destroying or otherwise using `value_`.
  }

  Slot(const Slot&) = delete;
  Slot& operator=(const Slot&) = delete;
  Slot& operator=(Slot&&) = delete;

 private:
  // Use a union to allow us full manual control of the initialization and
  // destruction of the value.
  union {
    T value_;
  };
};

template <typename T>
Slot(T) -> Slot<T>;

template <typename T, std::size_t N, std::size_t... I>
constexpr std::array<T, N> unsafe_move_array(T* ptr,
                                             std::index_sequence<I...>) {
  return {{T(UnsafeRelocateTag{}, std::move(ptr[I]))...}};
}

// TODO: b/451981992 - This works for single-level arrays, but we'd like to
// support arbitrary composite types that include arrays in Slot.
template <typename UT, unsigned S>
class Slot<std::array<UT, S>> {
 public:
  using T = std::array<UT, S>;
  Slot() {}
  explicit constexpr Slot(T&& x) {
    if constexpr (requires(UT t) { UT(UnsafeRelocateTag{}, std::move(t)); }) {
      memcpy(value_.data(), x.data(), sizeof(UT) * S);
    } else {
      value_ = std::move(x);
    }
  }
  T* Get() { return &value_; }
  T AssumeInitAndTakeValue() && {
    if constexpr (requires(UT x) { UT(UnsafeRelocateTag{}, std::move(x)); }) {
      return unsafe_move_array<UT, S>(value_.data(),
                                      std::make_index_sequence<S>());
    } else {
      T return_value(std::move(value_));
      std::destroy_at(&value_);
      return return_value;
    }
  }
  Slot(Slot&& other) { value_ = std::move(other.value_); }
  ~Slot() {}

  Slot(const Slot&) = delete;
  Slot& operator=(const Slot&) = delete;
  Slot& operator=(Slot&&) = delete;

 private:
  union {
    T value_;
  };
};
}  // namespace crubit

#endif  // CRUBIT_SUPPORT_INTERNAL_RETURN_VALUE_SLOT_H_
