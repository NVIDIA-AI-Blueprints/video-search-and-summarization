// Copyright 2026 Google LLC
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

#ifndef FUZZTEST_FUZZTEST_INTERNAL_DOMAINS_LAZY_H_
#define FUZZTEST_FUZZTEST_INTERNAL_DOMAINS_LAZY_H_

#include <cstddef>
#include <functional>
#include <memory>
#include <optional>
#include <tuple>
#include <utility>

#include "absl/random/bit_gen_ref.h"
#include "absl/status/status.h"
#include "./common/logging.h"
#include "./fuzztest/internal/domains/domain_base.h"
#include "./fuzztest/internal/meta.h"
#include "./fuzztest/internal/serialization.h"

namespace fuzztest {
namespace internal {

// A helper domain that allows its inner domain to be lazily initialized.
template <typename DomainT, typename... Args>
class Lazy : public domain_implementor::DomainBase<Lazy<DomainT, Args...>,
                                                   value_type_t<DomainT>,
                                                   corpus_type_t<DomainT>> {
 public:
  using typename Lazy::DomainBase::corpus_type;
  using typename Lazy::DomainBase::value_type;

  Lazy(Args&&... args)
      : args_(std::tuple<Args...>(std::forward<Args>(args)...)) {}

  Lazy(const Lazy& other) {
    if (other.inner_ != nullptr) {
      inner_ = std::make_unique<DomainT>(*other.inner_);
    } else {
      args_ = other.args_;
      setup_ = other.setup_;
    }
  }

  Lazy(Lazy&& other) noexcept = default;
  Lazy& operator=(Lazy&& other) = default;

  corpus_type Init(absl::BitGenRef prng) { return GetInnerDomain().Init(prng); }

  Lazy& WithLazySetup(std::function<void(DomainT&)> setup) {
    setup_ = std::move(setup);
    return *this;
  }

  void Mutate(corpus_type& corpus_value, absl::BitGenRef prng,
              const domain_implementor::MutationMetadata& metadata,
              bool only_shrink) {
    GetInnerDomain().Mutate(corpus_value, prng, metadata, only_shrink);
  }

  value_type GetValue(const corpus_type& corpus_value) const {
    return GetInnerDomain().GetValue(corpus_value);
  }

  std::optional<corpus_type> FromValue(const value_type& v) const {
    return GetInnerDomain().FromValue(v);
  }

  std::optional<corpus_type> ParseCorpus(const IRObject& obj) const {
    return GetInnerDomain().ParseCorpus(obj);
  }

  IRObject SerializeCorpus(const corpus_type& corpus_value) const {
    return GetInnerDomain().SerializeCorpus(corpus_value);
  }

  absl::Status ValidateCorpusValue(const corpus_type& corpus_value) const {
    return GetInnerDomain().ValidateCorpusValue(corpus_value);
  }

  auto GetPrinter() const { return GetInnerDomain().GetPrinter(); }

 private:
  template <std::size_t... Is>
  void PopulateInner(std::index_sequence<Is...>) const {
    FUZZTEST_CHECK(args_.has_value())
        << "args is unavailable for creating the inner domain";
    inner_ = std::make_unique<DomainT>(std::move(std::get<Is>(*args_))...);
    if (setup_) setup_(*inner_);
    args_ = std::nullopt;
    setup_ = {};
  }

  const DomainT& GetInnerDomain() const {
    if (inner_ == nullptr) {
      PopulateInner(std::index_sequence_for<Args...>{});
    }
    return *inner_;
  }

  DomainT& GetInnerDomain() {
    if (inner_ == nullptr) {
      PopulateInner(std::index_sequence_for<Args...>{});
    }
    return *inner_;
  }

  mutable std::unique_ptr<DomainT> inner_ = nullptr;
  // Arguments passed to the inner domain constructor. Set iff inner_ ==
  // nullptr.
  mutable std::optional<std::tuple<Args...>> args_;
  // Must be the default value if inner_ != nullptr.
  mutable std::function<void(DomainT&)> setup_;
};

}  // namespace internal
}  // namespace fuzztest

#endif  // FUZZTEST_FUZZTEST_INTERNAL_DOMAINS_LAZY_H_
