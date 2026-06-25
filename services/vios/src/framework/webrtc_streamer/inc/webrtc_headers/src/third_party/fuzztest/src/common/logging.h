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

#ifndef FUZZTEST_COMMON_LOGGING_H_
#define FUZZTEST_COMMON_LOGGING_H_

#include "absl/log/absl_check.h"
#include "absl/log/absl_log.h"

// NOTE: these macros are for internal use within the FuzzTest codebase.
#define FUZZTEST_CHECK(cond) ABSL_CHECK(cond)
#define FUZZTEST_CHECK_EQ(a, b) ABSL_CHECK_EQ(a, b)
#define FUZZTEST_CHECK_GE(a, b) ABSL_CHECK_GE(a, b)
#define FUZZTEST_CHECK_GT(a, b) ABSL_CHECK_GT(a, b)
#define FUZZTEST_CHECK_LE(a, b) ABSL_CHECK_LE(a, b)
#define FUZZTEST_CHECK_LT(a, b) ABSL_CHECK_LT(a, b)
#define FUZZTEST_CHECK_NE(a, b) ABSL_CHECK_NE(a, b)
#define FUZZTEST_CHECK_OK(status) ABSL_CHECK_OK(status)
#define FUZZTEST_PCHECK(cond) ABSL_PCHECK(cond)
#define FUZZTEST_PRECONDITION(cond) \
  ABSL_CHECK(cond) << "[Failed precondition] Incorrect usage: "
#define FUZZTEST_QCHECK(cond) ABSL_QCHECK(cond)
#define FUZZTEST_QCHECK_OK(status) ABSL_QCHECK_OK(status)

#define FUZZTEST_LOG(severity) ABSL_LOG(severity)
#define FUZZTEST_LOG_EVERY_POW_2(severity) ABSL_LOG_EVERY_POW_2(severity)
#define FUZZTEST_LOG_FIRST_N(severity, n) ABSL_LOG_FIRST_N(severity, n)
#define FUZZTEST_LOG_IF(severity, condition) ABSL_LOG_IF(severity, condition)
#define FUZZTEST_PLOG(severity) ABSL_PLOG(severity)
#define FUZZTEST_VLOG(verbose_level) ABSL_VLOG(verbose_level)
#define FUZZTEST_VLOG_EVERY_N(verbose_level, n) \
  ABSL_VLOG_EVERY_N(verbose_level, n)
#define FUZZTEST_VLOG_IS_ON(verbose_level) ABSL_VLOG_IS_ON(verbose_level)

// Easy variable value logging: FUZZTEST_LOG(INFO) << VV(foo) << VV(bar);
#define VV(x) #x ": " << (x) << " "

#endif  // FUZZTEST_COMMON_LOGGING_H_
