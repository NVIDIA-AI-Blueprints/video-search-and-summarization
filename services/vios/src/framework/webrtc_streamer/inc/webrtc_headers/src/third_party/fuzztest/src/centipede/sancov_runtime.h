// Copyright 2025 The Centipede Authors.
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
//      https://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

#ifndef FUZZTEST_CENTIPEDE_SANCOV_RUNTIME_H_
#define FUZZTEST_CENTIPEDE_SANCOV_RUNTIME_H_

// Sancov runtime interface.
//
// This header needs to be C compatible.

#include <stdbool.h>  // NOLINT
#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

struct SanCovRuntimeRawFeatureParts {
  const uint64_t* features;
  size_t num_features;
};

// Clears coverage data accumulated so far from sancov callbacks and/or
// interceptors.
//
// Will always clear the thread-local data updated during execution, and if
// `full_clear==true` will clear all accumulated coverage data.
void SanCovRuntimeClearCoverage(bool full_clear);

// Post-processes all coverage data, putting it all into an array of features,
// and returning a ptr and the length of this array.
//
// Will return a valid `features` pointer to a 64-bit element array of length
// `num_features`: This feature array is initialized at process startup, and
// its data (belonging to a global state) will remain valid for the duration of
// the process.
//
// If `reject_input==true`, then it will simply empty the feature array.
struct SanCovRuntimeRawFeatureParts SanCovRuntimeGetCoverage(bool reject_input);

#ifdef __cplusplus
}  // extern "C"
#endif

#endif  // FUZZTEST_CENTIPEDE_SANCOV_RUNTIME_H_
