// Copyright 2022 The Centipede Authors.
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

#ifndef THIRD_PARTY_CENTIPEDE_RUNNER_H_
#define THIRD_PARTY_CENTIPEDE_RUNNER_H_

#include <pthread.h>  // NOLINT: use pthread to avoid extra dependencies.
#include <time.h>

#include <atomic>
#include <cstdint>

#include "./centipede/byte_array_mutator.h"
#include "./centipede/dispatcher_flag_helper.h"
#include "./centipede/knobs.h"
#include "./centipede/runner_interface.h"
#include "./centipede/runner_result.h"
#include "./centipede/runner_utils.h"

namespace fuzztest::internal {

// Flags derived from CENTIPEDE_RUNNER_FLAGS.
struct RunTimeFlags {
  std::atomic<uint64_t> timeout_per_input;
  uint64_t timeout_per_batch;
  std::atomic<uint64_t> rss_limit_mb;
  uint64_t crossover_level;
  uint64_t ignore_timeout_reports : 1;
  uint64_t max_len;
  std::atomic<uint64_t> stack_limit_kb;
};

// One global object of this type is created by the runner at start up.
// All data members will be initialized to zero, unless they have initializers.
// Accesses to the subobjects should be fast, so we are trying to avoid
// extra memory references where possible.
//
// This class has a non-trivial destructor to work with targets that do not use
// the runner or LLVM fuzzer API at all.
//
// TODO(kcc): use a CTOR with absl::kConstInit (will require refactoring).
struct GlobalRunnerState {
  // Used by LLVMFuzzerMutate and initialized in main().
  ByteArrayMutator *byte_array_mutator = nullptr;
  Knobs knobs;

  GlobalRunnerState();

  // Performs necessary cleanup on process termination.
  void OnTermination();

  DispatcherFlagHelper flag_helper =
      DispatcherFlagHelper(CentipedeGetRunnerFlags());

  // Note that this field reflects the initial runner flags. But some
  // flags can change later (if wrapped with std::atomic).
  RunTimeFlags run_time_flags = {
      /*timeout_per_input=*/flag_helper.HasIntFlag(":timeout_per_input=", 0),
      /*timeout_per_batch=*/flag_helper.HasIntFlag(":timeout_per_batch=", 0),
      /*rss_limit_mb=*/flag_helper.HasIntFlag(":rss_limit_mb=", 0),
      /*crossover_level=*/flag_helper.HasIntFlag(":crossover_level=", 50),
      /*ignore_timeout_reports=*/
      flag_helper.HasFlag(":ignore_timeout_reports:"),
      /*max_len=*/flag_helper.HasIntFlag(":max_len=", 4000),
      /*stack_limit_kb=*/flag_helper.HasIntFlag(":stack_limit_kb=", 0),
  };

  // The path to a file where the runner may write the description of failure.
  const char *failure_description_path =
      flag_helper.GetStringFlag(":failure_description_path=");

  std::atomic<bool> has_failure_description;

  const char* persistent_mode_socket_path =
      flag_helper.GetStringFlag(":persistent_mode_socket=");
  int persistent_mode_socket = 0;

  pthread_mutex_t execution_result_override_mu = PTHREAD_MUTEX_INITIALIZER;
  // If not nullptr, it points to a batch result with either zero or one
  // execution. When an execution result present, it will be passed as the
  // execution result of the current test input. The object is owned and cleaned
  // up by the state, protected by execution_result_override_mu, and set by
  // `CentipedeSetExecutionResult()`.
  BatchResult *execution_result_override;

  // Execution stats for the currently executed input.
  ExecutionResult::Stats stats;

  // CentipedeRunnerMain() sets this to true.
  bool centipede_runner_main_executed = false;

  // Timeout-related machinery.

  // Starts the watchdog thread that terminates the runner if any of the
  // rss/time limits are exceeded.
  void StartWatchdogThread();
  // Resets the per-input timer. Call this before executing every input.
  void ResetTimers();

  // Per-input timer. Initially, zero. ResetInputTimer() sets it to the current
  // time.
  std::atomic<time_t> input_start_time;

  // Per-batch timer. Initially, zero. ResetInputTimer() sets it to the current
  // time before the first input and never resets it.
  std::atomic<time_t> batch_start_time;

  // The Watchdog thread sets this to true.
  std::atomic<bool> watchdog_thread_started;
};

extern ExplicitLifetime<GlobalRunnerState> state;

}  // namespace fuzztest::internal

#endif  // THIRD_PARTY_CENTIPEDE_RUNNER_H_
