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
# error "Do not include this header directly, include <async++.h> instead."
#endif

namespace async {

// Improved version of std::hardware_concurrency:
// - It never returns 0, 1 is returned instead.
// - It is guaranteed to remain constant for the duration of the program.
LIBASYNC_EXPORT std::size_t hardware_concurrency() LIBASYNC_NOEXCEPT;

// Task handle used by a wait handler
class task_wait_handle {
	detail::task_base* handle;

	// Allow construction in wait_for_task()
	friend LIBASYNC_EXPORT void detail::wait_for_task(detail::task_base* t);
	task_wait_handle(detail::task_base* t)
		: handle(t) {}

	// Execution function for use by wait handlers
	template<typename Func>
	struct wait_exec_func: private detail::func_base<Func> {
		template<typename F>
		explicit wait_exec_func(F&& f)
			: detail::func_base<Func>(std::forward<F>(f)) {}
		void operator()(detail::task_base*)
		{
			// Just call the function directly, all this wrapper does is remove
			// the task_base* parameter.
			this->get_func()();
		}
	};

public:
	task_wait_handle()
		: handle(nullptr) {}

	// Check if the handle is valid
	explicit operator bool() const
	{
		return handle != nullptr;
	}

	// Check if the task has finished executing
	bool ready() const
	{
		return detail::is_finished(handle->state.load(std::memory_order_acquire));
	}

	// Queue a function to be executed when the task has finished executing.
	template<typename Func>
	void on_finish(Func&& func)
	{
		// Make sure the function type is callable
		static_assert(detail::is_callable<Func()>::value, "Invalid function type passed to on_finish()");

		auto cont = new detail::task_func<typename std::remove_reference<decltype(inline_scheduler())>::type, wait_exec_func<typename std::decay<Func>::type>, detail::fake_void>(std::forward<Func>(func));
		cont->sched = std::addressof(inline_scheduler());
		handle->add_continuation(inline_scheduler(), detail::task_ptr(cont));
	}
};

// Wait handler function prototype
typedef void (*wait_handler)(task_wait_handle t);

// Set a wait handler to control what a task does when it has "free time", which
// is when it is waiting for another task to complete. The wait handler can do
// other work, but should return when it detects that the task has completed.
// The previously installed handler is returned.
LIBASYNC_EXPORT wait_handler set_thread_wait_handler(wait_handler w) LIBASYNC_NOEXCEPT;

// Exception thrown if a task_run_handle is destroyed without being run
struct LIBASYNC_EXPORT_EXCEPTION task_not_executed {};

// Task handle used in scheduler, acts as a unique_ptr to a task object
class task_run_handle {
	detail::task_ptr handle;

	// Allow construction in schedule_task()
	template<typename Sched>
	friend void detail::schedule_task(Sched& sched, detail::task_ptr t);
	explicit task_run_handle(detail::task_ptr t)
		: handle(std::move(t)) {}

public:
	// Movable but not copyable
	task_run_handle() = default;
	task_run_handle(task_run_handle&& other) LIBASYNC_NOEXCEPT
		: handle(std::move(other.handle)) {}
	task_run_handle& operator=(task_run_handle&& other) LIBASYNC_NOEXCEPT
	{
		handle = std::move(other.handle);
		return *this;
	}

	// If the task is not executed, cancel it with an exception
	~task_run_handle()
	{
		if (handle)
			handle->vtable->cancel(handle.get(), std::make_exception_ptr(task_not_executed()));
	}

	// Check if the handle is valid
	explicit operator bool() const
	{
		return handle != nullptr;
	}

	// Run the task and release the handle
	void run()
	{
		handle->vtable->run(handle.get());
		handle = nullptr;
	}

	// Run the task but run the given wait handler when waiting for a task,
	// instead of just sleeping.
	void run_with_wait_handler(wait_handler handler)
	{
		wait_handler old = set_thread_wait_handler(handler);
		run();
		set_thread_wait_handler(old);
	}

	// Conversion to and from void pointer. This allows the task handle to be
	// sent through C APIs which don't preserve types.
	void* to_void_ptr()
	{
		return handle.release();
	}
	static task_run_handle from_void_ptr(void* ptr)
	{
		return task_run_handle(detail::task_ptr(static_cast<detail::task_base*>(ptr)));
	}
};

namespace detail {

// Schedule a task for execution using its scheduler
template<typename Sched>
void schedule_task(Sched& sched, task_ptr t)
{
	static_assert(is_scheduler<Sched>::value, "Type is not a valid scheduler");
	sched.schedule(task_run_handle(std::move(t)));
}

// Inline scheduler implementation
inline void inline_scheduler_impl::schedule(task_run_handle t)
{
	t.run();
}

} // namespace detail
} // namespace async
