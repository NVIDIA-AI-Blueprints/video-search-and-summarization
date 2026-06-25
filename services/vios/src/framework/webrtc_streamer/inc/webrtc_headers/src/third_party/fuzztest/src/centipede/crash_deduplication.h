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

#ifndef FUZZTEST_CENTIPEDE_CRASH_DEDUPLICATION_H_
#define FUZZTEST_CENTIPEDE_CRASH_DEDUPLICATION_H_

#include <cstddef>
#include <filesystem>  // NOLINT
#include <string>

#include "absl/container/flat_hash_map.h"
#include "./centipede/centipede_callbacks.h"
#include "./centipede/crash_summary.h"
#include "./centipede/environment.h"
#include "./centipede/workdir.h"

namespace fuzztest::internal {

struct CrashDetails {
  std::string input_signature;
  std::string description;
  std::string input_path;
};

// Returns a map of crash signatures to crash details for crashes in the
// workdir. Only one crash per signature is returned, selected arbitrarily.
absl::flat_hash_map<std::string, CrashDetails> GetCrashesFromWorkdir(
    const WorkDir& workdir, size_t total_shards);

// Organizes crashing inputs from `crashing_dir` by attempting to reproduce
// them, and stores new crashes from `new_crashes_by_signature` that are not
// duplicates of existing ones.
//
// The input files in `crashing_dir` are uniquely identified by `bug_id`s. The
// file names are in the format `<bug_id>-<crash_signature>-<input_signature>`
// or `<input_signature>` (legacy format, in which case `<input_signature>` is
// also considered to be the `bug_id`, and the crash signature is considered to
// be missing).
//
// 1. Inputs from `crashing_dir` are re-executed:
//   - If an input is reproducible and causes an input failure (i.e., not a
//     setup failure or other special cases):
//     - It is kept in `crashing_dir` and reported to `crash_summary`.
//     - If its crash signature has changed, it is renamed to reflect the new
//       signature: `<bug_id>-<new_crash_signature>-<input_signature>`.
//     - The file's modification time is updated.
//   - If an input is not reproducible or doesn't cause an input failure:
//     - If its parsed crash signature is found in `new_crashes_by_signature`,
//       this signature hasn't been seen in a reproducible crash yet, and no
//       other input has been processed for this signature in the current call:
//       - The `bug_id` is reused for bug continuity: If the input from
//         `new_crashes_by_signature` has the same input signature as the
//         irreproducible input (flaky crash), the file is kept in
//         `crashing_dir` and its modification time is updated. Otherwise, the
//         irreproducible input is moved to `regression_dir`, and the input
//         from `new_crashes_by_signature` is copied to `crashing_dir` using
//         file name `<bug_id>-<crash_signature>-<new_input_signature>`.
//       - The crash is reported to `crash_summary`.
//     - Otherwise, if the input file has a valid name, it is copied to
//       `regression_dir`.
//     - If input file has an invalid name, it is moved to `regression_dir`.
//
// 2. New crashes from `new_crashes_by_signature` are stored:
//   - If a new crash has a signature that was not observed among crashes
//     from step 1, it is stored in `crashing_dir` with a newly generated
//     `bug_id`: `<new_bug_id>-<crash_signature>-<input_signature>`, and
//     reported to `crash_summary`.
//   - If the total number of inputs in `crashing_dir` reaches a predefined
//     limit, no more new crashes will be stored (unless they replace old
//     irreproducible inputs as described in step 1).
void OrganizeCrashingInputs(
    const std::filesystem::path& regression_dir,
    const std::filesystem::path& crashing_dir, const Environment& env,
    CentipedeCallbacksFactory& callbacks_factory,
    const absl::flat_hash_map<std::string, CrashDetails>&
        new_crashes_by_signature,
    CrashSummary& crash_summary);

}  // namespace fuzztest::internal

#endif  // FUZZTEST_CENTIPEDE_CRASH_DEDUPLICATION_H_
