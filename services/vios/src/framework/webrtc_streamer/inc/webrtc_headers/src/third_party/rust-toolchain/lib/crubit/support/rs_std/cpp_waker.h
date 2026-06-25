// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_CPP_WAKER_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_CPP_WAKER_H_

namespace rs_std {

class CppWaker;

namespace internal {

// These extern "C" functions defined in `cpp_waker.cc` are used by
// `dyn_future.rs` to implement the Rust `Waker` API.
//
// These are internal implementation details and should not be used except
// from `cpp_waker.cc`.
extern "C" CppWaker* rs_std_cpp_waker_clone(CppWaker* in);
extern "C" void rs_std_cpp_waker_wake_and_destroy(CppWaker* in);
extern "C" void rs_std_cpp_waker_wake_by_ref(CppWaker* in);
extern "C" void rs_std_cpp_waker_drop(CppWaker* in);

}  // namespace internal

// A base class for C++ implementations of Rust `Waker` objects.
//
// All operations must be thread-safe.
class CppWaker {
 protected:
  // CppWaker instances cannot be destroyed via base class pointer.
  // Subclasses should manage their own destruction using `Destroy`.
  ~CppWaker() = default;

 private:
  // Called when `Waker::wake_by_ref` is called on the Rust `Waker` object.
  //
  // The `CppWaker` must remain valid for further calls after this function
  // returns.
  virtual void WakeByRef() = 0;

  // Called when `Waker::wake` is called on the Rust `Waker` object.
  //
  // The Rust version of this function consumes `self` by-value, so no further
  // calls to the `CppWaker` object are expected. If `Clone` is implemented via
  // a reference count, this function should decrement the reference count.
  virtual void WakeAndDestroy() = 0;

  // Called when the Rust `Waker` is cloned.
  //
  // Returns a `CppWaker` pointer that wakes the same task as `this`.
  // This may be implemented by incrementing a reference count and returning
  // `this`.
  virtual CppWaker* Clone() = 0;

  // Called when the Rust `Waker` is destroyed.
  //
  // Destroys this `CppWaker` object. If `Clone` is implemented via a reference
  // count, this function should decrement the reference count.
  virtual void Destroy() = 0;

  friend CppWaker* internal::rs_std_cpp_waker_clone(CppWaker* in);
  friend void internal::rs_std_cpp_waker_wake_and_destroy(CppWaker* in);
  friend void internal::rs_std_cpp_waker_wake_by_ref(CppWaker* in);
  friend void internal::rs_std_cpp_waker_drop(CppWaker* in);
};

}  // namespace rs_std

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_CPP_WAKER_H_
