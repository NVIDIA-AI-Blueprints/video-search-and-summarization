// Copyright 2025 Google LLC
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef FUZZTEST_FUZZTEST_INTERNAL_DOMAINS_BIT_GEN_REF_H_
#define FUZZTEST_FUZZTEST_INTERNAL_DOMAINS_BIT_GEN_REF_H_

#include <cstdint>
#include <optional>
#include <tuple>
#include <utility>
#include <vector>

#include "absl/random/bit_gen_ref.h"
#include "absl/status/status.h"
#include "absl/strings/str_format.h"
#include "./common/logging.h"
#include "./fuzztest/fuzzing_bit_gen.h"
#include "./fuzztest/internal/domains/domain_base.h"
#include "./fuzztest/internal/meta.h"
#include "./fuzztest/internal/printer.h"
#include "./fuzztest/internal/serialization.h"

namespace fuzztest::internal {

// The fuzztest "corpus value" holds the fuzzed data stream and a
// maybe-initialized instance of a fuzz-test specific URBG which is bound to
// an absl::BitGenRef in BitGenRefDomain::GetValue.
//
// The URBG instance is lazily initialized when GetBitGen is called and
// destroyed when CleanupBitGen is called.
template <typename InnerCorpus>
class BitGenCorpus {
 public:
  using URBG = FuzzingBitGen;

  explicit BitGenCorpus(InnerCorpus inner_corpus)
      : inner_corpus_(std::move(inner_corpus)) {}
  ~BitGenCorpus() { CleanupBitGen(); }

  // Copy and move do not initialize the internal URBG instance.
  BitGenCorpus(const BitGenCorpus& o)
      : inner_corpus_(o.inner_corpus_), bitgen_(std::nullopt) {}
  BitGenCorpus& operator=(const BitGenCorpus& o) {
    // The internal URBG should be unused.
    FUZZTEST_CHECK(!bitgen_.has_value());
    inner_corpus_ = o.inner_corpus_;
    return *this;
  }
  BitGenCorpus(BitGenCorpus&& o)
      : inner_corpus_(std::move(o.inner_corpus_)), bitgen_(std::nullopt) {}
  BitGenCorpus& operator=(BitGenCorpus&& o) {
    // The internal URBG should be unused.
    FUZZTEST_CHECK(!o.bitgen_.has_value());
    FUZZTEST_CHECK(!bitgen_.has_value());
    inner_corpus_ = std::move(o.inner_corpus_);
    return *this;
  }

  InnerCorpus& inner_corpus() { return inner_corpus_; }
  const InnerCorpus& inner_corpus() const { return inner_corpus_; }

  // Cleanup the internal URBG instance.
  void CleanupBitGen() { bitgen_.reset(); }

  // Returns a reference to the URBG instance.
  // If it has not been initialized, it will be initialized.
  // The returned reference is valid until the next call to CleanupBitGen.
  template <typename InnerDomain>
  URBG& GetBitGen(InnerDomain& domain) const {
    static_assert(std::is_same_v<InnerCorpus, corpus_type_t<InnerDomain>>);
    if (!bitgen_.has_value()) {
      uint64_t seed = 0;
      std::tie(data_stream_, control_stream_, seed) =
          domain.GetValue(inner_corpus_);
      // The values are copied so that they can outlive the URBG instance.
      bitgen_.emplace(data_stream_, control_stream_, seed);
    }
    return *bitgen_;
  }

 private:
  InnerCorpus inner_corpus_;

  // Inputs to the FuzzingBitGen constructor which must outlive it.
  mutable std::vector<uint8_t> data_stream_;
  mutable std::vector<uint8_t> control_stream_;
  mutable std::optional<URBG> bitgen_;
};

// A FuzzTest domain for an absl::BitGenRef, which is an arbitrary
// uniform random bit generator and can be used for functions accepting an
// absl::BitGenRef, such as absl::Uniform and other Abseil distribution
// functions. The generated sequences will be stable across executions, though
// it may occasionally be broken when there are changes to the underlying
// implementation such as adding support for new distributions, etc.
//
// The domain accepts an input "data stream" corpus which is used to initialize
// a FuzzingBitGen instance. This internal FuzzingBitGen instance is bound to an
// absl::BitGenRef when GetValue is called. The control stream is reused
// (wrapped around) when exhausted. The data stream falls back to an LCG PRNG
// when exhausted.
//
// BitGenRefDomain does not support seeded domains.
// BitGenRefDomain does not support GetRandomValue.
template <typename InnerDomain>
class BitGenRefDomain : public domain_implementor::DomainBase<
                            /*Derived=*/BitGenRefDomain<InnerDomain>,
                            /*value_type=*/absl::BitGenRef,
                            /*corpus_type=*/
                            BitGenCorpus<corpus_type_t<InnerDomain>>> {
 public:
  using typename BitGenRefDomain::DomainBase::corpus_type;
  using typename BitGenRefDomain::DomainBase::value_type;

  explicit BitGenRefDomain(const InnerDomain& inner) : inner_(inner) {}
  explicit BitGenRefDomain(InnerDomain&& inner) : inner_(std::move(inner)) {}

  BitGenRefDomain(const BitGenRefDomain&) = default;
  BitGenRefDomain(BitGenRefDomain&&) = default;
  BitGenRefDomain& operator=(const BitGenRefDomain&) = default;
  BitGenRefDomain& operator=(BitGenRefDomain&&) = default;

  corpus_type Init(absl::BitGenRef prng) {
    return corpus_type(inner_.Init(prng));
  }
  void Mutate(corpus_type& corpus_value, absl::BitGenRef prng,
              const domain_implementor::MutationMetadata& metadata,
              bool only_shrink) {
    corpus_value.CleanupBitGen();
    inner_.Mutate(corpus_value.inner_corpus(), prng, metadata, only_shrink);
  }

  value_type GetValue(const corpus_type& corpus_value) const {
    return corpus_value.GetBitGen(inner_);
  }

  value_type GetRandomValue(absl::BitGenRef /*prng*/) {
    // See b/404828355
    FUZZTEST_LOG(FATAL) << "The domain doesn't support GetRandomValue().";
  }

  std::optional<corpus_type> FromValue(const value_type&) const {
    // No conversion from absl::BitGenRef back to corpus.
    return std::nullopt;
  }

  absl::Status ValidateCorpusValue(const corpus_type& corpus_value) const {
    return inner_.ValidateCorpusValue(corpus_value.inner_corpus());
  }

  void UpdateMemoryDictionary(
      const corpus_type& corpus_value,
      domain_implementor::ConstCmpTablesPtr cmp_tables) {
    return inner_.UpdateMemoryDictionary(corpus_value.inner_corpus(),
                                         cmp_tables);
  }

  std::optional<corpus_type> ParseCorpus(const internal::IRObject& obj) const {
    auto x = inner_.ParseCorpus(obj);
    if (!x.has_value()) {
      return std::nullopt;
    }
    return corpus_type(*std::move(x));
  }

  internal::IRObject SerializeCorpus(const corpus_type& corpus_value) const {
    return inner_.SerializeCorpus(corpus_value.inner_corpus());
  }

  auto GetPrinter() const { return Printer{}; }

 private:
  struct Printer {
    void PrintCorpusValue(const corpus_type& val,
                          domain_implementor::RawSink out,
                          domain_implementor::PrintMode /*mode*/) const {
      absl::Format(out, "FuzzingBitGen({");
      bool first = true;
      for (const auto& x : std::get<0>(val.inner_corpus())) {
        if (!first) {
          absl::Format(out, ", ");
        }
        absl::Format(out, "%d", x);
        first = false;
      }
      absl::Format(out, "}, {");
      first = true;
      for (const auto& x : std::get<1>(val.inner_corpus())) {
        if (!first) {
          absl::Format(out, ", ");
        }
        absl::Format(out, "%d", x);
        first = false;
      }
      absl::Format(out, "}, 0x%016x)", std::get<2>(val.inner_corpus()));
    }
  };

  InnerDomain inner_;
};

}  // namespace fuzztest::internal

#endif  // FUZZTEST_FUZZTEST_INTERNAL_DOMAINS_BIT_GEN_REF_H_
