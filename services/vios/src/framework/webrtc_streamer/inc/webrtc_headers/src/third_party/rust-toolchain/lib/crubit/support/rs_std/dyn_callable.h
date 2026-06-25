// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_DYN_CALLABLE_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_DYN_CALLABLE_H_

#include <cstddef>
#include <cstring>
#include <exception>
#include <type_traits>
#include <utility>

#include "absl/base/nullability.h"
#include "absl/functional/any_invocable.h"
#include "support/bridge.h"

namespace rs_std {

template <class Sig>
class DynCallable;

namespace internal_dyn_callable {

template <class Sig>
struct DynCallableAbi;

// NOLINTBEGIN(abseil-no-internal-dependencies)
using absl::internal_any_invocable::EmptyManager;
using absl::internal_any_invocable::ForwardedParameterType;
using absl::internal_any_invocable::FunctionToCall;
using absl::internal_any_invocable::ManagerType;
using absl::internal_any_invocable::TypeErasedState;
// NOLINTEND(abseil-no-internal-dependencies)

// Storage type for managing relocation and destruction of the
// `Box<dyn Trait>` received from Rust.
class ManagedCallable {
 protected:
  explicit ManagedCallable(TypeErasedState state,
                           ManagerType* absl_nonnull manager)
      : storage_(state), manager_(manager) {}

  explicit ManagedCallable() : manager_(EmptyManager) {}

  ~ManagedCallable() {
    manager_(FunctionToCall::dispose, &this->storage_, &this->storage_);
  }

  ManagedCallable(ManagedCallable&) = delete;
  ManagedCallable& operator=(ManagedCallable&) = delete;

  ManagedCallable(ManagedCallable&& other) {
    *this = std::forward<ManagedCallable>(other);
  }

  ManagedCallable& operator=(ManagedCallable&& other) {
    // Dispose this.
    manager_(FunctionToCall::dispose, &this->storage_, &this->storage_);

    // Move other into this.
    other.manager_(FunctionToCall::relocate_from_to, /*from=*/&other.storage_,
                   /*to=*/&this->storage_);
    manager_ = other.manager_;

    // Remove the manager from other so it doesn't do anything on its
    // now-invalid state.
    other.manager_ = EmptyManager;

    return *this;
  }

  TypeErasedState storage_;
  ManagerType* absl_nonnull manager_;
};

// The type for functions issuing the actual invocation of the object.
// A pointer to such a function is contained in each DynCallable instance.
template <class ReturnType, class... P>
using InvokerType = ReturnType(TypeErasedState*, ForwardedParameterType<P>...);

// Partially specialized class that wraps ManagedCallable and handles
// operator().
template <class Sig>
class Impl {};

// Raises a fatal error when the DynCallable is invoked after a move.
template <class ReturnType, class... P>
inline ReturnType InvokedAfterMove(TypeErasedState*,
                                   ForwardedParameterType<P>...) {
  std::terminate();
}

#define CRUBIT_INTERNAL_RUST_ANY_CALLABLE_IMPL(qual)                           \
  template <class ReturnType, class... P>                                      \
  class Impl<ReturnType(P...) qual> : public ManagedCallable {                 \
   protected:                                                                  \
    friend struct DynCallableAbi<ReturnType(P...) qual>;                       \
    using InvokerType = InvokerType<ReturnType, P...>;                         \
                                                                               \
    explicit Impl(TypeErasedState state, ManagerType* absl_nonnull manager,    \
                  InvokerType* invoker)                                        \
        : ManagedCallable(state, manager), invoker_(invoker) {}                \
                                                                               \
   public:                                                                     \
    Impl() = default;                                                          \
    Impl(const Impl& other) = delete;                                          \
    Impl& operator=(const Impl& other) = delete;                               \
    Impl(Impl&& other) { *this = std::forward<Impl>(other); }                  \
    Impl& operator=(Impl&& other) {                                            \
      ManagedCallable::operator=(std::move(other));                            \
      invoker_ = other.invoker_;                                               \
      other.invoker_ = nullptr;                                                \
      return *this;                                                            \
    }                                                                          \
                                                                               \
    ReturnType operator()(P... args) qual {                                    \
      using QualifiedTestType = int qual;                                      \
                                                                               \
      InvokerType* invoker_copy = invoker_;                                    \
      if constexpr (std::is_rvalue_reference_v<QualifiedTestType>) {           \
        invoker_ = InvokedAfterMove<ReturnType, P...>;                         \
        manager_ = EmptyManager;                                               \
      }                                                                        \
      if constexpr (std::is_const_v<QualifiedTestType>) {                      \
        return invoker_copy(&const_cast<Impl*>(this)->storage_,                \
                            std::forward<ForwardedParameterType<P>>(args)...); \
      } else {                                                                 \
        return invoker_copy(&this->storage_,                                   \
                            std::forward<ForwardedParameterType<P>>(args)...); \
      }                                                                        \
    }                                                                          \
                                                                               \
    bool HasValue() const { return invoker_ != nullptr; }                      \
                                                                               \
   protected:                                                                  \
    InvokerType* invoker_ = nullptr;                                           \
  };

CRUBIT_INTERNAL_RUST_ANY_CALLABLE_IMPL(const)
CRUBIT_INTERNAL_RUST_ANY_CALLABLE_IMPL()
CRUBIT_INTERNAL_RUST_ANY_CALLABLE_IMPL(&&)

#undef CRUBIT_INTERNAL_RUST_ANY_CALLABLE_IMPL

// The ABI contract for `DynCallableAbi<F>` varies between Rust -> C++, and C++
// -> Rust.
//
// When sending from Rust to C++, the value is encoded as `Box<dyn F>`.
//
// When sending from C++ to Rust, the value is encoded as a bool indicating
// whether the value is present. If present, the bool is followed by the
// `Box<dyn F>`.
template <class Sig>
struct DynCallableAbi {
  using Value = DynCallable<Sig>;

  static constexpr size_t kSize = 24;

  void Encode(Value value, crubit::Encoder& encoder) && {
    // Encode whether true if the value is present, false if it's in the
    // moved-from state.
    crubit::TransmuteAbi<bool>().Encode(static_cast<bool>(value), encoder);
    if (value) {
      // If present, encode the state.
      crubit::TransmuteAbi<TypeErasedState>().Encode(value.storage_, encoder);
    }
    // Remove the manager since the value is moved-from.
    value.manager_ = EmptyManager;
  }

  Value Decode(crubit::Decoder& decoder) && {
    auto state = crubit::TransmuteAbi<TypeErasedState>().Decode(decoder);
    return DynCallable<Sig>(state, manager, invoker);
  }

  explicit DynCallableAbi(ManagerType* absl_nonnull manager,
                          Value::Impl::InvokerType* invoker)
      : manager(manager), invoker(invoker) {}

  ManagerType* absl_nonnull manager;
  Value::Impl::InvokerType* invoker;
};

}  // namespace internal_dyn_callable

// rs_std::DynCallable
//
// `rs_std::DynCallable` is a move-only functional wrapper type, like
// `absl::AnyInvocable`. It assumes ownership of a Rust `Box<dyn Trait>`,
// where `Trait` is one of `Fn`, `FnMut`, or `FnOnce`.
//
// It is similar to `absl::AnyInvocable` in the following ways:
// * It is move-only and cannot be copied, because it owns the callable.
// * It can be default constructed to an empty state for delayed initialization,
//   but invoking it results in program termination. You can check for emptiness
//   using `operator bool()`.
// * It respects `const` qualifiers and rvalue-reference qualifiers as part of
//   `Sig`, applying them to `operator()`. That is, `Fn()` maps to
//   `void() const`, and `FnOnce()` maps to `void() &&`.
// * It is cheaply convertible to `absl::AnyInvocable` because it uses the same
//   underlying representation, only with stricter invariants.
//
// Unlike `absl::AnyInvocable`, it may only store Rust callables, and so
// non-empty `DynCallable`s can only be obtained by exposing a C++ function
// with a `DynCallable` parameter to Rust via Crubit. Passing a moved-from or
// default constructed `DynCallable` back to Rust will result in a Rust
// `Box<dyn Trait>` that panics when called.
template <class Sig>
class DynCallable : private internal_dyn_callable::Impl<Sig> {
 private:
  using Impl = internal_dyn_callable::Impl<Sig>;

  // DynCallableAbi<Sig> is a friend so it can access the DynCallable
  // constructor below.
  friend struct internal_dyn_callable::DynCallableAbi<Sig>;

  // Private constructor, only intended to be called by DynCallableAbi<Sig>.
  //
  // * `state` is a two pointers containing the `Box<dyn Trait>` from Rust.
  // * `manager` is a function pointer with the `ManagerType` signature, and
  //   knows how to move and dispose `state`.
  // * `invoker` is a C++ function pointer that knows how to invoke the
  //   `Box<dyn Trait>`.
  explicit DynCallable(internal_dyn_callable::TypeErasedState state,
                       internal_dyn_callable::ManagerType* absl_nonnull manager,
                       Impl::InvokerType* invoker)
      : Impl(state, manager, invoker) {}

 public:
  // Constructors

  // Constructs the `DynCallable` in an empty state.
  // Invoking it results in undefined behavior.
  DynCallable() noexcept = default;
  DynCallable(std::nullptr_t) noexcept {}  // NOLINT

  // DynCallable is not copyable.
  DynCallable(DynCallable& other) = delete;
  DynCallable& operator=(DynCallable& other) = delete;

  DynCallable(DynCallable&& other) = default;
  DynCallable& operator=(DynCallable&& other) = default;

  // Destructor

  // If not empty, destroys the target.
  ~DynCallable() = default;

  // rs_std::DynCallable::swap()
  //
  // Exchanges the targets of `*this` and `other`.
  void swap(DynCallable& other) noexcept { std::swap(*this, other); }

  // rs_std::DynCallable::operator bool()
  //
  // Returns `true` if `*this` is not empty.
  //
  // Invoking an empty `DynCallable` results in undefined behavior.
  explicit operator bool() const noexcept { return this->HasValue(); }

  using Impl::operator();

  // NOLINTNEXTLINE(google-explicit-constructor)
  operator absl::AnyInvocable<Sig>() && {
    auto* manager = this->manager_;
    auto* invoker = this->invoker_;
    this->manager_ = internal_dyn_callable::EmptyManager;
    this->invoker_ = nullptr;
    return absl::AnyInvocable<Sig>(&this->storage_, manager, invoker);
  }

  // Returns `true` if `f` is empty.
  friend bool operator==(const DynCallable& f, std::nullptr_t) noexcept {
    return !f.HasValue();
  }

  // Returns `true` if `f` is empty.
  friend bool operator==(std::nullptr_t, const DynCallable& f) noexcept {
    return !f.HasValue();
  }

  // Returns `false` if `f` is empty.
  friend bool operator!=(const DynCallable& f, std::nullptr_t) noexcept {
    return f.HasValue();
  }

  // Returns `false` if `f` is empty.
  friend bool operator!=(std::nullptr_t, const DynCallable& f) noexcept {
    return f.HasValue();
  }

  // swap()
  //
  // Exchanges the targets of `f1` and `f2`.
  friend void swap(DynCallable& f1, DynCallable& f2) noexcept { f1.swap(f2); }
};

}  // namespace rs_std

#endif  // THIRD_PARTY_CRUBIT_SUPPORT_RS_STD_DYN_CALLABLE_H_
