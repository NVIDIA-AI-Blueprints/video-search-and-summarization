// Part of the Crubit project, under the Apache License v2.0 with LLVM
// Exceptions. See /LICENSE for license information.
// SPDX-License-Identifier: Apache-2.0 WITH LLVM-exception

#ifndef THIRD_PARTY_CRUBIT_SUPPORT_BRIDGE_H_
#define THIRD_PARTY_CRUBIT_SUPPORT_BRIDGE_H_

// Allow others to check #ifdef CRUBIT_BRIDGE_ENABLED
#if !defined(SWIG) && defined(__clang__) && __cplusplus >= 202002L
#define CRUBIT_BRIDGE_ENABLED

#include <concepts>
#include <cstddef>
#include <cstring>
#include <optional>
#include <tuple>
#include <utility>

namespace crubit {

class Encoder;
class Decoder;

// A mutually understood ABI for sending bridge types between Rust and C++.
//
// Bridging values between Rust and C++ is typically done by breaking down
// values into their primitive, ABI-compatible parts like integers and pointers
// in the native language. Then, these primitive parts are sent across the
// language boundary, where the target language can reconstruct the semantically
// equivalent value. This typically happens by sending the parts as function
// arguments on an extern C function, but this doesn't work for
// generic/templated types without monomorphizing each instantiation since C
// doesn't have templates.
//
// The solution to this is to transform the value into an ABI-compatible layout
// that both languages understand, allowing for passing arbitrarily complex
// types through C as a char pointer to a stack allocated buffer. The
// `is_crubit_abi` check is used to describe this mutually understood ABI.
//
// Let's walk through the example of how `Option<T>` is bridged. The first step
// is to define an ABI for how it should be bridged, which is represented by a
// type that is `is_crubit_abi`.
//
// ```cpp
// template <typename Abi>
//   requires(is_crubit_abi<Abi>)
// struct OptionAbi {
//   using Value = std::optional<typename Abi::Value>;
//   // other items omitted...
// };
// ```
//
// This is saying "`OptionAbi<Abi>` is a description of how to bridge a
// `std::optional<typename Abi::Value>`." But before we proceed, we need to
// decide: what will the std::optional ABI be? To keep things general we'll
// choose to bridge `std::optional<T>` as a bool, followed by the value if the
// bool is true. To express this, we need to implement the other items:
//
// ```cpp
// template <typename Abi>
//   requires(is_crubit_abi<Abi>)
// struct OptionAbi {
//   using Value = std::optional<typename Abi::Value>;
//   static constexpr size_t kSize = sizeof(bool) + Abi::kSize;
//   static void Encode(Value value, Encoder& encoder) {
//     if (value.has_value()) {
//       encoder.EncodeTransmute(true);
//       encoder.Encode<Abi>(*std::move(value));
//     } else {
//       encoder.EncodeTransmute(false);
//     }
//   }
//   static Value Decode(Decoder& decoder) {
//     if (!decoder.DecodeTransmute<bool>()) {
//       return std::nullopt;
//     }
//     return decoder.Decode<Abi>();
//   }
// };
// ```
//
// There are several things going on here. First, we need to define the `kSize`
// constant. This information is used to statically compute the size of the
// buffer required to encode/decode an `std::optional<T>` with this ABI,
// allowing us to stack allocate the buffer. Importantly, the current
// implementation packs all the data with unaligned writes/reads, so alignment
// information is not needed. Second, we need to define the `Encode` and
// `Decode` functions. These functions implement the agreed-upon ABI: bool,
// optionally followed by the value if the bool is true.
//
// # Safety
//
// It's safety critical that the C++ implementation matches the Rust
// implementation exactly, since the ABI is supposed to be mutually understood.
template <typename Abi>
constexpr bool is_crubit_abi = requires {
  // The type that this CrubitAbi is encoding and decoding.
  typename Abi::Value;

  // The size in bytes of a `Value` when encoded with this ABI. This is used to
  // statically compute the size of the buffer required to encode/decode a
  // `Value` with this ABI.
  { Abi::kSize } -> std::convertible_to<size_t>;

  // Encodes a `Value`, advancing the encoders's position by `kSize` bytes.
  //
  // Aside from implementations for primitives, most implementations of this
  // function will be composed of other calls to [`Encoder::Encode::<Abi>`],
  // each one advancing the encoder's position by `A::kSize` bytes. The
  // implementation should ensure that the these calls do not advance the
  // encoder's position by more than `kSize` bytes. This is because the `kSize`
  // constant is used to compute the buffer size statically, and if the
  // encoder's position is advanced by more than `kSize`, the encoder may panic
  // in debug builds, or cause undefined behavior in release builds.
  //
  // # Notes
  //
  // The value must be semantically moved into the encoder. This means that if
  // you're transferring ownership of anything, you must ensure that the
  // original owner leaks the resource so it can later be reclaimed by
  // decoding. Prefer functions that explicitly leak, or defer to moving values
  // into a casted `alignof(T) char buf[sizeof(T)]` if leaking APIs are
  // unavailable.
  //
  // # Examples
  //
  // ```cpp
  // template <typename Abi1, typename Abi2>
  //   requires(is_crubit_abi<Abi1> && is_crubit_abi<Abi2>)
  // struct PairAbi {
  //   static void Encode(Value value, Encoder& encoder) {
  //     encoder.Encode<Abi1>(std::move(value.first));
  //     encoder.Encode<Abi2>(std::move(value.second));
  //   }
  //   // other items omitted...
  // };
  // ```
  {
    std::declval<Abi&&>().Encode(std::declval<typename Abi::Value>(),
                                 std::declval<Encoder&>())
  } -> std::same_as<void>;

  // Decodes a [`Value`], advancing the decoder's position by `kSize` bytes.
  //
  // Aside from implementations for primitives, most implementations of this
  // function will be composed of other calls to [`Decoder::Decode::<Abi>`],
  // each one advancing the decoder's position by `A::kSize` bytes. The
  // implementation should ensure that the these calls do not advance the
  // decoder's position by more than `kSize` bytes. This is because the `kSize`
  // constant is used to compute the buffer size statically, and if the
  // decoder's position is advanced by more than `kSize`, the decoder may panic
  // in debug builds, or cause undefined behavior in release builds.
  //
  // # Examples
  //
  // ```cpp
  // template <typename Abi1, typename Abi2>
  //   requires(is_crubit_abi<Abi1> && is_crubit_abi<Abi2>)
  // struct PairAbi {
  //   static Value Decode(Decoder& decoder) {
  //     return {
  //         .first = decoder.Decode<Abi1>(),
  //         .second = decoder.Decode<Abi2>(),
  //     };
  //   }
  //   // other items omitted...
  // };
  // ```
  //
  // # Safety
  //
  // The caller guarantees that the buffer's current position contains a
  // `Value` that was encoded with this ABI from Rust.
  {
    std::declval<Abi&&>().Decode(std::declval<Decoder&>())
  } -> std::same_as<typename Abi::Value>;
};

namespace internal {

template <typename Abi>
  requires(is_crubit_abi<Abi>)
void Encode(Abi&& abi, unsigned char* buf, typename Abi::Value value);

template <typename Abi>
  requires(is_crubit_abi<Abi>)
typename Abi::Value Decode(Abi&& abi, const unsigned char* buf);

}  // namespace internal

// A wrapper around a buffer that tracks which parts of a buffer have already
// been written to.
class Encoder {
 public:
  explicit Encoder(size_t remaining_bytes, unsigned char* buf)
      : remaining_bytes_(remaining_bytes), buf_(buf) {}

  void* Next(size_t size) & {
    remaining_bytes_ -= size;
    return buf_ + remaining_bytes_;
  }

 private:
  template <typename Abi>
    requires(is_crubit_abi<Abi>)
  friend void internal::Encode(Abi&& abi, unsigned char* buf,
                               typename Abi::Value value);
  // The number of bytes remaining in the buffer.
  size_t remaining_bytes_;
  unsigned char* buf_;
};

// A wrapper around a buffer that tracks which parts of a buffer have already
// been read from.
class Decoder {
 public:
  explicit Decoder(size_t remaining_bytes, const unsigned char* buf)
      : remaining_bytes_(remaining_bytes), buf_(buf) {}

  const void* Next(size_t size) & {
    remaining_bytes_ -= size;
    return buf_ + remaining_bytes_;
  }

 private:
  template <typename Abi>
    requires(is_crubit_abi<Abi>)
  friend typename Abi::Value internal::Decode(Abi&& abi,
                                              const unsigned char* buf);
  // The number of bytes remaining in the buffer.
  size_t remaining_bytes_;
  const unsigned char* buf_;
};

// A Crubit ABI for encoding and decoding a value of type `T` by copying the
// memory of the value using `memcpy`.
template <typename T>
  requires(std::move_constructible<T>)
struct TransmuteAbi {
  using Value = T;
  static constexpr size_t kSize = sizeof(Value);
  void Encode(Value value, Encoder& encoder) && {
    // Move-construct the value into a type erased buffer, ensuring that value
    // is in a "moved from" state. Then copy the value into the encoder buffer.
    // We use an intermediate buffer and a memcpy to avoid strict aliasing
    // violations. Furthermore, the destructor is not called- this is intended,
    // because we have semantically moved the value into the buffer.
    alignas(Value) char buf[kSize];
    std::memcpy(encoder.Next(kSize), new (buf) Value(std::move(value)), kSize);
  }
  Value Decode(Decoder& decoder) && {
    alignas(Value) char buf[kSize];
    // Copy the value from the decoder buffer into the intermediate buffer.
    std::memcpy(buf, decoder.Next(kSize), kSize);
    // Move-construct the value from the buffer.
    return std::move(*reinterpret_cast<Value*>(buf));
  }
};

template <typename... Abis>
  requires(is_crubit_abi<Abis> && ...)
struct TupleAbi {
  using Value = std::tuple<typename Abis::Value...>;
  static constexpr size_t kSize = (0 + ... + Abis::kSize);
  void Encode(Value value, Encoder& encoder) && {
    std::apply(
        [&](auto&&... args) {
          return std::apply(
              [&](auto&&... abis) {
                (std::move(abis).Encode(args, encoder), ...);
              },
              std::move(abis));
        },
        std::move(value));
  }
  Value Decode(Decoder& decoder) && {
    return std::apply(
        [&](Abis&&... abis) {
          return std::make_tuple(std::move(abis).Decode(decoder)...);
        },
        std::move(abis));
  }

  std::tuple<Abis...> abis;
};

template <typename Abi1, typename Abi2>
  requires(is_crubit_abi<Abi1> && is_crubit_abi<Abi2>)
struct PairAbi {
  explicit PairAbi(Abi1 abi1, Abi2 abi2) : abis(abi1, abi2) {}

  using Value = std::pair<typename Abi1::Value, typename Abi2::Value>;
  static constexpr size_t kSize = Abi1::kSize + Abi2::kSize;
  void Encode(Value value, Encoder& encoder) && {
    std::move(abis.first).Encode(std::move(value.first), encoder);
    std::move(abis.second).Encode(std::move(value.second), encoder);
  }
  Value Decode(Decoder& decoder) && {
    return {
        .first = std::move(abis.first).Decode(decoder),
        .second = std::move(abis.second).Decode(decoder),
    };
  }

  std::pair<Abi1, Abi2> abis;
};

template <typename Abi>
  requires(is_crubit_abi<Abi>)
struct OptionAbi {
  using Value = std::optional<typename Abi::Value>;
  static constexpr size_t kSize = sizeof(bool) + Abi::kSize;
  void Encode(Value value, Encoder& encoder) && {
    if (value.has_value()) {
      TransmuteAbi<bool>().Encode(true, encoder);
      std::move(abi).Encode(*std::move(value), encoder);
    } else {
      TransmuteAbi<bool>().Encode(false, encoder);
    }
  }
  Value Decode(Decoder& decoder) && {
    if (!TransmuteAbi<bool>().Decode(decoder)) {
      return std::nullopt;
    }
    return std::move(abi).Decode(decoder);
  }

  Abi abi;
};

template <typename T>
  requires(std::move_constructible<T>)
struct BoxedAbi {
  using Value = T;
  static constexpr size_t kSize = sizeof(void*);
  void Encode(Value value, Encoder& encoder) && {
    void* box = new Value(std::move(value));
    TransmuteAbi<void*>().Encode(box, encoder);
  }
  Value Decode(Decoder& decoder) && {
    Value* box =
        reinterpret_cast<Value*>(TransmuteAbi<void*>().Decode(decoder));
    Value value(std::move(*box));
    delete box;
    return value;
  }
};

namespace internal {

template <typename Abi>
  requires(is_crubit_abi<Abi>)
void Encode(Abi&& abi, unsigned char* buf, typename Abi::Value value) {
  Encoder encoder(Abi::kSize, buf);
  std::forward<Abi>(abi).Encode(std::move(value), encoder);
}

template <typename Abi>
  requires(is_crubit_abi<Abi>)
typename Abi::Value Decode(Abi&& abi, const unsigned char* buf) {
  Decoder decoder(Abi::kSize, buf);
  return std::forward<Abi>(abi).Decode(decoder);
}

}  // namespace internal

}  // namespace crubit

#endif  // CRUBIT_BRIDGE_ENABLED
#endif  // THIRD_PARTY_CRUBIT_SUPPORT_BRIDGE_H_
