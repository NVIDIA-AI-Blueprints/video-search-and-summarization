/*
 * Copyright 2019 The libgav1 Authors
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 *      http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#ifndef LIBGAV1_SRC_UTILS_EXECUTOR_H_
#define LIBGAV1_SRC_UTILS_EXECUTOR_H_

#include <functional>

namespace libgav1 {

class Executor {
 public:
  virtual ~Executor();

  // Schedules the specified "callback" for execution in this executor.
  // Depending on the subclass implementation, this may block in some
  // situations.
  virtual void Schedule(std::function<void()> callback) = 0;
};

}  // namespace libgav1

#endif  // LIBGAV1_SRC_UTILS_EXECUTOR_H_
