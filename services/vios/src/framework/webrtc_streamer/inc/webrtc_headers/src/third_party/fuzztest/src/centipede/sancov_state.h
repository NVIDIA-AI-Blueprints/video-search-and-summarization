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

#ifndef FUZZTEST_CENTIPEDE_SANCOV_STATE_H_
#define FUZZTEST_CENTIPEDE_SANCOV_STATE_H_

#include <pthread.h>

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <cstdio>
#include <cstdlib>
#include <cstring>
#include <ctime>

#include "absl/base/const_init.h"
#include "absl/base/nullability.h"
#include "absl/numeric/bits.h"
#include "./centipede/callstack.h"
#include "./centipede/concurrent_bitset.h"
#include "./centipede/concurrent_byteset.h"
#include "./centipede/dispatcher_flag_helper.h"
#include "./centipede/execution_metadata.h"
#include "./centipede/feature.h"
#include "./centipede/hashed_ring_buffer.h"
#include "./centipede/reverse_pc_table.h"
#include "./centipede/runner_cmp_trace.h"
#include "./centipede/runner_dl_info.h"
#include "./centipede/runner_utils.h"
#include "./centipede/sancov_object_array.h"
#include "./centipede/sancov_runtime.h"

extern "C" const char *absl_nullable GetSancovFlags();

namespace fuzztest::internal {

// An arbitrarily large size.
constexpr size_t kCmpFeatureSetSize = 1 << 18;

// Like std::lock_guard, but for pthread_mutex_t.
class LockGuard {
 public:
  explicit LockGuard(pthread_mutex_t &mu) : mu_(mu) { pthread_mutex_lock(&mu); }
  ~LockGuard() { pthread_mutex_unlock(&mu_); }

 private:
  pthread_mutex_t &mu_;
};

// Flags derived from CENTIPEDE_RUNNER_FLAGS.
// Flags used in instrumentation callbacks are bit-packed for efficiency.
struct SancovFlags {
  uint64_t path_level : 8;
  uint64_t use_pc_features : 1;
  uint64_t use_dataflow_features : 1;
  uint64_t use_cmp_features : 1;
  uint64_t callstack_level : 8;
  uint64_t use_counter_features : 1;
  uint64_t use_auto_dictionary : 1;
  uint64_t skip_seen_features : 1;
};

// One such object is created in runner's TLS.
// There is no CTOR, since we don't want to use the brittle and lazy TLS CTORs.
// All data members are zero-initialized during thread creation.
struct ThreadLocalSancovState {
  // Traces the memory comparison of `n` bytes at `s1` and `s2` called at
  // `caller_pc` with `is_equal` indicating whether the two memory regions have
  // equal contents. May add cmp features and auto-dictionary entries if
  // enabled.
  void TraceMemCmp(uintptr_t caller_pc, const uint8_t *s1, const uint8_t *s2,
                   size_t n, bool is_equal);

  // Intrusive doubly-linked list of TLS objects.
  // Guarded by state.tls_list_mu.
  ThreadLocalSancovState *next, *prev;

  // The pthread_create() interceptor calls OnThreadStart() before the thread
  // callback. The main thread also calls OnThreadStart(). OnThreadStop() will
  // be called when thread termination is detected internally - see
  // sancov_state.cc.
  void OnThreadStart();
  void OnThreadStop();

  // Whether OnThreadStart() is called on this thread. This is used as a proxy
  // of the readiness of the lower-level runtime.
  bool started;

  // Whether the thread should be traced for execution feedback.
  bool traced;

  // Whether the thread state is detached, i.e. the thread has ended.
  bool detached;

  // Paths are thread-local, so we maintain the current bounded path here.
  // We allow paths of up to 100, controlled at run-time via the "path_level".
  static constexpr uint64_t kBoundedPathLength = 100;
  HashedRingBuffer<kBoundedPathLength> path_ring_buffer;

  // Value of SP in the top call frame of the thread, computed in OnThreadStart.
  uintptr_t top_frame_sp;
  // The lower bound of the stack region of this thread. 0 means unknown.
  uintptr_t stack_region_low;

  // `lowest_sp` and `*sancov_lowest_sp` are read and written by both
  // the current thread and the sancov processing thread. Thus race conditions
  // may happen. We don't use mutex/atomic because they are slow (and it is not
  // possible on sancov_lowest_sp). Instead we let race conditions happen and
  // tolerate them with the best effort. An SP value is valid if and only if
  // `stack_region_low <= SP <= top_frame_sp && stack_region_low > 0`.
  // This should reject most bad values caused by race conditions.
  //
  // Lowest sp observed by the this library.
  uintptr_t lowest_sp;
  // A pointer to the lowest sp updated by sancov (if enabled). It is constant
  // and non-null when the thread is alive, and set to null when the thread
  // is terminated, guarded by `state.tls_list_mu`. So no race conditions on
  // the pointer itself.
  uintptr_t* sancov_lowest_sp;

  // The (imprecise) call stack is updated by the PC callback.
  CallStack<> call_stack;

  // Cmp traces capture the arguments of CMP instructions, memcmp, etc.
  // We have dedicated traces for 2-, 4-, and 8-byte comparison, and
  // a catch-all `cmp_traceN` trace for memcmp, etc.
  CmpTrace<2, 64> cmp_trace2;
  CmpTrace<4, 64> cmp_trace4;
  CmpTrace<8, 64> cmp_trace8;
  CmpTrace<0, 64> cmp_traceN;

  // Set this to true if the thread needs to be ignored in ForEachTLS.
  // It should be always false if the state is in the global detached_tls_list.
  bool ignore;
};

struct SancovState {
  SancovState();
  ~SancovState();

  DispatcherFlagHelper flag_helper = DispatcherFlagHelper(GetSancovFlags());

  // TODO(xinhaoyuan): Change to use meaningful flag names instead of the
  // generic names arg1/2/3.
  const char *arg1 = flag_helper.GetStringFlag(":arg1=");
  const char *arg2 = flag_helper.GetStringFlag(":arg2=");
  const char *arg3 = flag_helper.GetStringFlag(":arg3=");

  SancovFlags flags = {
      /*path_level=*/std::min(ThreadLocalSancovState::kBoundedPathLength,
                              flag_helper.HasIntFlag(":path_level=", 0)),
      /*use_pc_features=*/flag_helper.HasFlag(":use_pc_features:"),
      /*use_dataflow_features=*/
      flag_helper.HasFlag(":use_dataflow_features:"),
      /*use_cmp_features=*/flag_helper.HasFlag(":use_cmp_features:"),
      /*callstack_level=*/flag_helper.HasIntFlag(":callstack_level=", 0),
      /*use_counter_features=*/
      flag_helper.HasFlag(":use_counter_features:"),
      /*use_auto_dictionary=*/
      flag_helper.HasFlag(":use_auto_dictionary:"),
      /*skip_seen_features=*/flag_helper.HasFlag(":skip_seen_features:"),
  };

  // Computed by DlInfo().
  // Usually, the main object is the executable binary containing main()
  // and most of the executable code (we assume that the target is
  // built in mostly-static mode, i.e. -dynamic_mode=off).
  // When the `dl_path_suffix` runner flag is provided, the main_object refers
  // to the dynamic library (DSO) pointed to by this flag.
  //
  // Note: this runner currently does not support more than one instrumented
  // DSO in the process, i.e. you either instrument the main binary, or one DSO.
  // Supporting more than one DSO will require major changes,
  // major added complexity, and potentially cause slowdown.
  // There is currently no motivation for such a change.
  DlInfo main_object;

  // Doubly linked list of TLSs of all live threads.
  ThreadLocalSancovState *tls_list;
  // Doubly linked list of detached TLSs.
  ThreadLocalSancovState *detached_tls_list;
  // Guards `tls_list` and `detached_tls_list`.
  pthread_mutex_t tls_list_mu = PTHREAD_MUTEX_INITIALIZER;
  // Iterates all TLS objects under tls_list_mu, except those with `ignore` set.
  // Calls `callback()` on every TLS.
  template <typename Callback>
  void ForEachTls(Callback callback) {
    LockGuard lock(tls_list_mu);
    for (auto *it = tls_list; it; it = it->next) {
      if (!it->ignore) callback(*it);
    }
    for (auto *it = detached_tls_list; it; it = it->next) {
      callback(*it);
    }
  }

  // Reclaims all TLSs in detached_tls_list and cleans up the list.
  void CleanUpDetachedTls();

  // State for SanitizerCoverage.
  // See https://clang.llvm.org/docs/SanitizerCoverage.html.
  SanCovObjectArray sancov_objects;
  // An arbitrarily large size.
  static constexpr size_t kDataFlowFeatureSetSize = 1 << 18;
  ConcurrentBitSet<kDataFlowFeatureSetSize> data_flow_feature_set{
      absl::kConstInit};

  // Tracing CMP instructions, capture events from these domains:
  // kCMPEq, kCMPModDiff, kCMPHamming, kCMPModDiffLog, kCMPMsbEq.
  // See https://clang.llvm.org/docs/SanitizerCoverage.html#tracing-data-flow.
  ConcurrentBitSet<kCmpFeatureSetSize> cmp_eq_set{absl::kConstInit};
  ConcurrentBitSet<kCmpFeatureSetSize> cmp_moddiff_set{absl::kConstInit};
  ConcurrentBitSet<kCmpFeatureSetSize> cmp_hamming_set{absl::kConstInit};
  ConcurrentBitSet<kCmpFeatureSetSize> cmp_difflog_set{absl::kConstInit};
  ConcurrentBitSet<kCmpFeatureSetSize> cmp_feature_set{absl::kConstInit};

  // We think that call stack produces rich signal, so we give a few bits to it.
  static constexpr size_t kCallStackFeatureSetSize = 1 << 24;
  ConcurrentBitSet<kCallStackFeatureSetSize> callstack_set{absl::kConstInit};

  // kMaxNumPcs is the maximum number of instrumented PCs in the binary.
  // We can be generous here since the unused memory will not cost anything.
  // `pc_counter_set` is a static byte set supporting up to kMaxNumPcs PCs.
  static constexpr size_t kMaxNumPcs = 1 << 28;
  TwoLayerConcurrentByteSet<kMaxNumPcs> pc_counter_set{absl::kConstInit};
  // This is the actual number of PCs, aligned up to
  // pc_counter_set::kSizeMultiple, computed at startup.
  size_t actual_pc_counter_set_size_aligned;

  // Used by trace_pc instrumentation. Populated if `pcs_file_path` flag is set.
  ReversePCTable reverse_pc_table;

  // We use edge instrumentation w/ callbacks to implement bounded-path
  // coverage.
  // * The current PC is converted to an offset (a PC index).
  // * The offset is pushed to a HashedRingBuffer, producing a hash.
  // * The resulting hash represents N most recent PCs, we use it as a feature.
  //
  // WARNING: this is highly experimental.
  // This is far from perfect and may be not sensitive enough in some cases
  // and create exponential number of features in other cases.
  // Some areas to experiment with:
  // * Handle only function-entry PCs, i.e. use call paths, not branch paths.
  // * Play with the length of the path (kBoundedPathLength)
  // * Use call stacks instead of paths (via unwinding or other
  // instrumentation).

  // An arbitrarily large size.
  static constexpr size_t kPathBitSetSize = 1 << 25;
  // Observed paths. The total number of observed paths for --path_level=N
  // can be up to NumPCs**N.
  // So, we make the bitset very large, but it may still saturate.
  ConcurrentBitSet<kPathBitSetSize> path_feature_set{absl::kConstInit};

  // An arbitrarily large size.
  static const size_t kMaxFeatures = 1 << 20;
  // FeatureArray used to accumulate features from all sources.
  FeatureArray<kMaxFeatures> g_features;
  // Execution metadata gathered by `PostProcessSancov`.
  //
  // TODO: b/443264359 - export it in the runtime interface.
  ExecutionMetadata metadata;

  // Features that were seen before.
  static constexpr size_t kSeenFeatureSetSize =
      absl::bit_ceil(feature_domains::kLastDomain.end());
  ConcurrentBitSet<kSeenFeatureSetSize> seen_features{absl::kConstInit};

  // Initialized in CTOR from the __centipede_extra_features section.
  feature_t *user_defined_begin;
  feature_t *user_defined_end;
};

// Clears all the thread-local data updated during execution.
__attribute__((noinline))  // so that we see it in profile.
void CleanUpSancovTls();

// All bitsets, counter arrays and such need to be clear before every execution.
// However, clearing them is expensive because they are sparse.
// Instead, we rely on ForEachNonZeroByte() and
// ConcurrentBitSet::ForEachNonZeroBit to clear the bits/bytes after they
// finish iterating.
// If `full_clear==true` clear all coverage anyway - useful to remove the
// coverage accumulated during startup.
__attribute__((noinline))  // so that we see it in profile.
void PrepareSancov(bool full_clear);

// Post-processes all coverage data, puts it all into `g_features`.
//
// If `reject_input` is `true`, it simply sets `g_features` to empty.
// This can be used to support features like:
// https://llvm.org/docs/LibFuzzer.html#rejecting-unwanted-inputs
__attribute__((noinline))  // so that we see it in profile.
void PostProcessSancov(bool reject_input = false);

void MaybeAddFeature(feature_t feature);

// Returns a pointer to `g_features` and its length.
SanCovRuntimeRawFeatureParts SanCovRuntimeGetFeatures();

// Gets the execution metadata gathered in `PostProcessSancov`.
const ExecutionMetadata& SanCovRuntimeGetExecutionMetadata();

// Check for stack limit for `stack_usage`, with `is_current_stack` set if it
// is for the current calling stack.
__attribute__((weak)) void CheckStackLimit(size_t stack_usage,
                                           bool is_current_stack);

extern ExplicitLifetime<SancovState> sancov_state;
extern __thread ThreadLocalSancovState tls;

// Initializes the sancov runtime. Must be called before using it. It can be
// called multiple times while only the first time is effective.
void SancovRuntimeInitialize();

}  // namespace fuzztest::internal

#endif  // FUZZTEST_CENTIPEDE_SANCOV_STATE_H_
