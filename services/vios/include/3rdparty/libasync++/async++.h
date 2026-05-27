// Copyright (c) 2015 Amanieu d'Antras
//
// Permission is hereby granted, free of charge, to any person obtaining a copy
// of this software and associated documentation files (the "Software"), to deal
// in the Software without restriction, including without limitation the rights
// to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
// copies of the Software, and to permit persons to whom the Software is
// furnished to do so, subject to the following conditions:
//
// The above copyright notice and this permission notice shall be included in
// all copies or substantial portions of the Software.
//
// THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
// IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
// FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
// AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
// LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
// OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN
// THE SOFTWARE.

#ifndef ASYNCXX_H_
#define ASYNCXX_H_

#include <algorithm>
#include <atomic>
#include <cassert>
#include <cstddef>
#include <cstdint>
#include <cstdlib>
#include <cstring>
#include <exception>
#include <functional>
#include <iterator>
#include <memory>
#include <mutex>
#include <thread>
#include <type_traits>
#include <utility>
#include <vector>

// Export declaration to make symbols visible for dll/so
#ifdef LIBASYNC_STATIC
# define LIBASYNC_EXPORT
# define LIBASYNC_EXPORT_EXCEPTION
#else
# ifdef _WIN32
#  ifdef LIBASYNC_BUILD
#   define LIBASYNC_EXPORT __declspec(dllexport)
#  else
#   define LIBASYNC_EXPORT __declspec(dllimport)
#  endif
#  define LIBASYNC_EXPORT_EXCEPTION
# else
#  define LIBASYNC_EXPORT __attribute__((visibility("default")))
#  define LIBASYNC_EXPORT_EXCEPTION __attribute__((visibility("default")))
# endif
#endif

// Support compiling without exceptions
#ifndef LIBASYNC_NO_EXCEPTIONS
# ifdef __clang__
#  if !defined(__EXCEPTIONS) || !__has_feature(cxx_exceptions)
#   define LIBASYNC_NO_EXCEPTIONS
#  endif
# elif defined(__GNUC__) && !defined(__EXCEPTIONS)
#  define LIBASYNC_NO_EXCEPTIONS
# elif defined(_MSC_VER) && defined(_HAS_EXCEPTIONS) && !_HAS_EXCEPTIONS
#  define LIBASYNC_NO_EXCEPTIONS
# endif
#endif
#ifdef LIBASYNC_NO_EXCEPTIONS
# define LIBASYNC_THROW(...) std::abort()
# define LIBASYNC_RETHROW() do {} while (false)
# define LIBASYNC_RETHROW_EXCEPTION(except) std::terminate()
# define LIBASYNC_TRY if (true)
# define LIBASYNC_CATCH(...) else if (false)
#else
# define LIBASYNC_THROW(...) throw __VA_ARGS__
# define LIBASYNC_RETHROW() throw
# define LIBASYNC_RETHROW_EXCEPTION(except) std::rethrow_exception(except)
# define LIBASYNC_TRY try
# define LIBASYNC_CATCH(...) catch (__VA_ARGS__)
#endif

// Optional debug assertions. If exceptions are enabled then use those, but
// otherwise fall back to an assert message.
#ifndef NDEBUG
# ifndef LIBASYNC_NO_EXCEPTIONS
#  define LIBASYNC_ASSERT(pred, except, message) ((pred) ? ((void)0) : throw except(message))
# else
#  define LIBASYNC_ASSERT(pred, except, message) ((pred) ? ((void)0) : assert(message))
# endif
#else
# define LIBASYNC_ASSERT(pred, except, message) ((void)0)
#endif

// Annotate move constructors and move assignment with noexcept to allow objects
// to be moved if they are in containers. Compilers which don't support noexcept
// will usually move regardless.
#if defined(__GNUC__) || _MSC_VER >= 1900
# define LIBASYNC_NOEXCEPT noexcept
#else
# define LIBASYNC_NOEXCEPT throw()
#endif

// Cacheline alignment to avoid false sharing between different threads
#define LIBASYNC_CACHELINE_SIZE 64
#ifdef __GNUC__
# define LIBASYNC_CACHELINE_ALIGN __attribute__((aligned(LIBASYNC_CACHELINE_SIZE)))
#elif defined(_MSC_VER)
# define LIBASYNC_CACHELINE_ALIGN __declspec(align(LIBASYNC_CACHELINE_SIZE))
#else
# define LIBASYNC_CACHELINE_ALIGN alignas(LIBASYNC_CACHELINE_SIZE)
#endif

// Force symbol visibility to hidden unless explicity exported
#ifndef LIBASYNC_STATIC
#if defined(__GNUC__) && !defined(_WIN32)
# pragma GCC visibility push(hidden)
#endif
#endif

// Some forward declarations
namespace async {

template<typename Result>
class task;
template<typename Result>
class shared_task;
template<typename Result>
class event_task;

} // namespace async

// Include sub-headers
#include "async++/traits.h"
#include "async++/aligned_alloc.h"
#include "async++/ref_count.h"
#include "async++/scheduler_fwd.h"
#include "async++/continuation_vector.h"
#include "async++/task_base.h"
#include "async++/scheduler.h"
#include "async++/task.h"
#include "async++/when_all_any.h"
#include "async++/cancel.h"
#include "async++/range.h"
#include "async++/partitioner.h"
#include "async++/parallel_invoke.h"
#include "async++/parallel_for.h"
#include "async++/parallel_reduce.h"

#ifndef LIBASYNC_STATIC
#if defined(__GNUC__) && !defined(_WIN32)
# pragma GCC visibility pop
#endif
#endif

#endif
