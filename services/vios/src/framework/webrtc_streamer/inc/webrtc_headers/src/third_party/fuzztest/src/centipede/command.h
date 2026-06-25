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

#ifndef THIRD_PARTY_CENTIPEDE_COMMAND_H_
#define THIRD_PARTY_CENTIPEDE_COMMAND_H_

#include <memory>
#include <optional>
#include <string>
#include <string_view>
#include <vector>

#include "absl/status/status.h"
#include "absl/synchronization/mutex.h"
#include "absl/time/time.h"

namespace fuzztest::internal {

class Command final {
 public:
  struct Options {
    // Arguments to pass to the executed command. The command is executed by the
    // shell, so the arguments need to be shell-escaped.
    // TODO(b/381910257): Escape the arguments for passing to the shell.
    std::vector<std::string> args;
    // Environment variables to set or unset in the subprocess, relative to the
    // environment of the parent process. The variables in the form "KEY=VALUE"
    // are set, and the variables in the form "-KEY" are unset.
    std::vector<std::string> env_diff;
    // Redirect stdout to this file path with an opaque suffix. If empty, use
    // parent's STDOUT.
    //
    // `Command` automatically unlinks any previous redirected files on
    // execution and destruction.
    std::string stdout_file_prefix;
    // Redirect stderr to this file path with an opaque suffix. If empty, use
    // parent's STDERR. If `out` == `err` and both are non-empty, stdout/stderr
    // are combined.
    //
    // `Command` automatically unlinks any previous redirected files on
    // execution and destruction.
    std::string stderr_file_prefix;
    // "@@" in the command will be replaced with `temp_file_path`.
    std::string temp_file_path;
  };

  // Constructs a command to run the binary at `path` with the given `options`.
  // The path can contain "@@" which will be replaced with
  // `options.temp_file_path`.
  explicit Command(std::string_view path, Options options);

  // Constructs a command to run the binary at `path` with default options.
  explicit Command(std::string_view path);

  // Not movable or copyable to simplify the resource management logic.
  Command(const Command& other) = delete;
  Command& operator=(const Command& other) = delete;
  Command(Command&& other) noexcept = delete;
  Command& operator=(Command&& other) noexcept = delete;

  // Cleans up the fork server, if that was created.
  ~Command();

  // Returns a string representing the command, e.g. like this
  // "env -u ENV1 ENV2=VAL2 path arg1 arg2 > out 2>& err"
  //
  // Note that the result reflects the latest command executed and can be
  // changed after next execution. It can be missing some parts (e.g. output
  // redirection) if there is no previous execution.
  std::string ToString() const;

  // Execute the command asynchronously. Returns true if it starts a new
  // execution, false otherwise. Must be called only when the command
  // is not executing.
  bool ExecuteAsync();

  // Returns whether the command is currently executing.
  bool is_executing() const { return is_executing_; }

  // Waits for the command execution and returns the exit status if the
  // execution finishes within `deadline`. Must be called only when the command
  // is executing. execution or the execution times out. If interrupted, may
  // call `RequestEarlyStop()` (see stop.h).
  std::optional<int> Wait(absl::Time deadline);

  // Requests the command execution to stop by sending SIGTERM or SIGKILL (when
  // `force` is true). Must be called only when the command is executing. Note
  // that after calling this, `Wait()` is still needed to complete the
  // execution.
  void RequestStop(bool force = false);

  // Convenient method to execute synchronously.
  int Execute() {
    if (!ExecuteAsync()) return EXIT_FAILURE;
    return Wait(absl::InfiniteFuture()).value_or(EXIT_FAILURE);
  }

  // Attempts to start a fork server, returns true on success.
  // Pipe files for the fork server are created in `temp_dir_path`
  // with prefix `prefix`.
  // See runner_fork_server.cc for details.
  bool StartForkServer(std::string_view temp_dir_path, std::string_view prefix);

  // Accessors.
  const std::string& path() const { return path_; }

  // Accessors to the paths of the {stdout,stderr} of the last command
  // execution, if redirected to files. Otherwise the paths will be empty. Note
  // that it is possible that the files does not exist on the paths, e.g. if the
  // command haven't touch them yet.
  const std::string& stdout_file() const { return stdout_file_; }
  const std::string& stderr_file() const { return stderr_file_; }

 private:
  struct ForkServerProps;

  int pid_ = -1;
  bool is_executing_ = false;

  // Derived from Options::{stdout,stderr}_file_prefix, with the realized suffix
  // if the options are non-empty.
  std::string stdout_file_;
  std::string stderr_file_;

  // Removes previous files (if any), and sets {stdout,stderr}_file_ with
  // `new_suffix` if both parts are not empty. Note that it only sets the new
  // file paths without touching the files.
  void ResetRedirectionFiles(std::string_view new_suffix);

  // Returns the status of the fork server process. Expects that the server was
  // previously started using `StartForkServer()`.
  absl::Status VerifyForkServerIsHealthy();

  // Reads and returns the stdout of the command, if redirected to a file. If
  // not redirected, returns a placeholder text.
  std::string ReadRedirectedStdout() const;
  // Reads and returns the stderr of the command, if redirected to a file that
  // is also different from the redirected stdout. If not redirected, returns a
  // placeholder text.
  std::string ReadRedirectedStderr() const;
  // Possibly logs information about a crash, starting with `message`, followed
  // by the command line, followed by the redirected stdout and stderr read
  // from `options_.out` and `options_.err` files, if any.
  void LogProblemInfo(std::string_view message) const;
  // Just as `LogCrashInfo()`, but logging occurs only when the FUZZTEST_VLOG
  // level (set via `--v` or its equivalents) is >= `min_vlog`.
  void VlogProblemInfo(std::string_view message, int vlog_level) const;

  const std::string path_;
  const Options options_;
  std::string command_line_;

  std::unique_ptr<ForkServerProps> fork_server_;
};

// Get the shared mutex for execution logging for preventing confusing
// interlaced logs when multiple threads are logging at the same time. Note that
// the printing all log content at once is not viable due to the single log line
// length limit.
absl::Mutex& GetExecutionLoggingMutex();

}  // namespace fuzztest::internal

#endif  // THIRD_PARTY_CENTIPEDE_COMMAND_H_
