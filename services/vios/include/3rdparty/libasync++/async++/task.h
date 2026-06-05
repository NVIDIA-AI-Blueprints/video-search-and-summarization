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

// Exception thrown when an event_task is destroyed without setting a value
struct LIBASYNC_EXPORT_EXCEPTION abandoned_event_task {};

namespace detail {

// Common code for task and shared_task
template<typename Result>
class basic_task {
	// Reference counted internal task object
	detail::task_ptr internal_task;

	// Real result type, with void turned into fake_void
	typedef typename void_to_fake_void<Result>::type internal_result;

	// Type-specific task object
	typedef task_result<internal_result> internal_task_type;

	// Friend access
	friend async::task<Result>;
	friend async::shared_task<Result>;
	template<typename T>
	friend typename T::internal_task_type* get_internal_task(const T& t);
	template<typename T>
	friend void set_internal_task(T& t, task_ptr p);

	// Common code for get()
	void get_internal() const
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty task object");

		// If the task was canceled, throw the associated exception
		get_internal_task(*this)->wait_and_throw();
	}

	// Common code for then()
	template<typename Sched, typename Func, typename Parent>
	typename continuation_traits<Parent, Func>::task_type then_internal(Sched& sched, Func&& f, Parent&& parent) const
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty task object");

		// Save a copy of internal_task because it might get moved into exec_func
		task_base* my_internal = internal_task.get();

		// Create continuation
		typedef continuation_traits<Parent, Func> traits;
		typedef typename void_to_fake_void<typename traits::task_type::result_type>::type cont_internal_result;
		typedef continuation_exec_func<Sched, typename std::decay<Parent>::type, cont_internal_result, typename traits::decay_func, typename traits::is_value_cont, is_task<typename traits::result_type>::value> exec_func;
		typename traits::task_type cont;
		set_internal_task(cont, task_ptr(new task_func<Sched, exec_func, cont_internal_result>(std::forward<Func>(f), std::forward<Parent>(parent))));

		// Add the continuation to this task
		// Avoid an expensive ref-count modification since the task isn't shared yet
		get_internal_task(cont)->add_ref_unlocked();
		get_internal_task(cont)->sched = std::addressof(sched);
		my_internal->add_continuation(sched, task_ptr(get_internal_task(cont)));

		return cont;
	}

public:
	// Task result type
	typedef Result result_type;

	// Check if this task is not empty
	bool valid() const
	{
		return internal_task != nullptr;
	}

	// Query whether the task has finished executing
	bool ready() const
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty task object");
		return internal_task->ready();
	}

	// Query whether the task has been canceled with an exception
	bool canceled() const
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty task object");
		return internal_task->state.load(std::memory_order_acquire) == task_state::canceled;
	}

	// Wait for the task to complete
	void wait() const
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty task object");
		internal_task->wait();
	}

	// Get the exception associated with a canceled task
	std::exception_ptr get_exception() const
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty task object");
		if (internal_task->wait() == task_state::canceled)
			return get_internal_task(*this)->get_exception();
		else
			return std::exception_ptr();
	}
};

// Common code for event_task specializations
template<typename Result>
class basic_event {
	// Reference counted internal task object
	detail::task_ptr internal_task;

	// Real result type, with void turned into fake_void
	typedef typename detail::void_to_fake_void<Result>::type internal_result;

	// Type-specific task object
	typedef detail::task_result<internal_result> internal_task_type;

	// Friend access
	friend async::event_task<Result>;
	template<typename T>
	friend typename T::internal_task_type* get_internal_task(const T& t);

	// Common code for set()
	template<typename T>
	bool set_internal(T&& result) const
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty event_task object");

		// Only allow setting the value once
		detail::task_state expected = detail::task_state::pending;
		if (!internal_task->state.compare_exchange_strong(expected, detail::task_state::locked, std::memory_order_acquire, std::memory_order_relaxed))
			return false;

		LIBASYNC_TRY {
			// Store the result and finish
			get_internal_task(*this)->set_result(std::forward<T>(result));
			internal_task->finish();
		} LIBASYNC_CATCH(...) {
			// At this point we have already committed to setting a value, so
			// we can't return the exception to the caller. If we did then it
			// could cause concurrent set() calls to fail, thinking a value has
			// already been set. Instead, we simply cancel the task with the
			// exception we just got.
			get_internal_task(*this)->cancel_base(std::current_exception());
		}
		return true;
	}

public:
	// Movable but not copyable
	basic_event(basic_event&& other) LIBASYNC_NOEXCEPT
		: internal_task(std::move(other.internal_task)) {}
	basic_event& operator=(basic_event&& other) LIBASYNC_NOEXCEPT
	{
		internal_task = std::move(other.internal_task);
		return *this;
	}

	// Main constructor
	basic_event()
		: internal_task(new internal_task_type)
	{
		internal_task->event_task_got_task = false;
	}

	// Cancel events if they are destroyed before they are set
	~basic_event()
	{
		// This check isn't thread-safe but set_exception does a proper check
		if (internal_task && !internal_task->ready() && !internal_task->is_unique_ref(std::memory_order_relaxed)) {
#ifdef LIBASYNC_NO_EXCEPTIONS
			// This will result in an abort if the task result is read
			set_exception(std::exception_ptr());
#else
			set_exception(std::make_exception_ptr(abandoned_event_task()));
#endif
		}
	}

	// Get the task linked to this event. This can only be called once.
	task<Result> get_task()
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty event_task object");
		LIBASYNC_ASSERT(!internal_task->event_task_got_task, std::logic_error, "get_task() called twice on event_task");

		// Even if we didn't trigger an assert, don't return a task if one has
		// already been returned.
		task<Result> out;
		if (!internal_task->event_task_got_task)
			set_internal_task(out, internal_task);
		internal_task->event_task_got_task = true;
		return out;
	}

	// Cancel the event with an exception and cancel continuations
	bool set_exception(std::exception_ptr except) const
	{
		LIBASYNC_ASSERT(internal_task, std::invalid_argument, "Use of empty event_task object");

		// Only allow setting the value once
		detail::task_state expected = detail::task_state::pending;
		if (!internal_task->state.compare_exchange_strong(expected, detail::task_state::locked, std::memory_order_acquire, std::memory_order_relaxed))
			return false;

		// Cancel the task
		get_internal_task(*this)->cancel_base(std::move(except));
		return true;
	}
};

} // namespace detail

template<typename Result>
class task: public detail::basic_task<Result> {
public:
	// Movable but not copyable
	task() = default;
	task(task&& other) LIBASYNC_NOEXCEPT
		: detail::basic_task<Result>(std::move(other)) {}
	task& operator=(task&& other) LIBASYNC_NOEXCEPT
	{
		detail::basic_task<Result>::operator=(std::move(other));
		return *this;
	}

	// Get the result of the task
	Result get()
	{
		this->get_internal();

		// Move the internal state pointer so that the task becomes invalid,
		// even if an exception is thrown.
		detail::task_ptr my_internal = std::move(this->internal_task);
		return detail::fake_void_to_void(static_cast<typename task::internal_task_type*>(my_internal.get())->get_result(*this));
	}

	// Add a continuation to the task
	template<typename Sched, typename Func>
	typename detail::continuation_traits<task, Func>::task_type then(Sched& sched, Func&& f)
	{
		return this->then_internal(sched, std::forward<Func>(f), std::move(*this));
	}
	template<typename Func>
	typename detail::continuation_traits<task, Func>::task_type then(Func&& f)
	{
		return then(::async::default_scheduler(), std::forward<Func>(f));
	}

	// Create a shared_task from this task
	shared_task<Result> share()
	{
		LIBASYNC_ASSERT(this->internal_task, std::invalid_argument, "Use of empty task object");

		shared_task<Result> out;
		detail::set_internal_task(out, std::move(this->internal_task));
		return out;
	}
};

template<typename Result>
class shared_task: public detail::basic_task<Result> {
	// get() return value: const Result& -or- void
	typedef typename std::conditional<
		std::is_void<Result>::value,
		void,
		typename std::add_lvalue_reference<
			typename std::add_const<Result>::type
		>::type
	>::type get_result;

public:
	// Movable and copyable
	shared_task() = default;

	// Get the result of the task
	get_result get() const
	{
		this->get_internal();
		return detail::fake_void_to_void(detail::get_internal_task(*this)->get_result(*this));
	}

	// Add a continuation to the task
	template<typename Sched, typename Func>
	typename detail::continuation_traits<shared_task, Func>::task_type then(Sched& sched, Func&& f) const
	{
		return this->then_internal(sched, std::forward<Func>(f), *this);
	}
	template<typename Func>
	typename detail::continuation_traits<shared_task, Func>::task_type then(Func&& f) const
	{
		return then(::async::default_scheduler(), std::forward<Func>(f));
	}
};

// Special task type which can be triggered manually rather than when a function executes.
template<typename Result>
class event_task: public detail::basic_event<Result> {
public:
	// Movable but not copyable
	event_task() = default;
	event_task(event_task&& other) LIBASYNC_NOEXCEPT
		: detail::basic_event<Result>(std::move(other)) {}
	event_task& operator=(event_task&& other) LIBASYNC_NOEXCEPT
	{
		detail::basic_event<Result>::operator=(std::move(other));
		return *this;
	}

	// Set the result of the task, mark it as completed and run its continuations
	bool set(const Result& result) const
	{
		return this->set_internal(result);
	}
	bool set(Result&& result) const
	{
		return this->set_internal(std::move(result));
	}
};

// Specialization for references
template<typename Result>
class event_task<Result&>: public detail::basic_event<Result&> {
public:
	// Movable but not copyable
	event_task() = default;
	event_task(event_task&& other) LIBASYNC_NOEXCEPT
		: detail::basic_event<Result&>(std::move(other)) {}
	event_task& operator=(event_task&& other) LIBASYNC_NOEXCEPT
	{
		detail::basic_event<Result&>::operator=(std::move(other));
		return *this;
	}

	// Set the result of the task, mark it as completed and run its continuations
	bool set(Result& result) const
	{
		return this->set_internal(result);
	}
};

// Specialization for void
template<>
class event_task<void>: public detail::basic_event<void> {
public:
	// Movable but not copyable
	event_task() = default;
	event_task(event_task&& other) LIBASYNC_NOEXCEPT
		: detail::basic_event<void>(std::move(other)) {}
	event_task& operator=(event_task&& other) LIBASYNC_NOEXCEPT
	{
		detail::basic_event<void>::operator=(std::move(other));
		return *this;
	}

	// Set the result of the task, mark it as completed and run its continuations
	bool set()
	{
		return this->set_internal(detail::fake_void());
	}
};

// Task type returned by local_spawn()
template<typename Sched, typename Func>
class local_task {
	// Make sure the function type is callable
	typedef typename std::decay<Func>::type decay_func;
	static_assert(detail::is_callable<decay_func()>::value, "Invalid function type passed to local_spawn()");

	// Task result type
	typedef typename detail::remove_task<decltype(std::declval<decay_func>()())>::type result_type;
	typedef typename detail::void_to_fake_void<result_type>::type internal_result;

	// Task execution function type
	typedef detail::root_exec_func<Sched, internal_result, decay_func, detail::is_task<decltype(std::declval<decay_func>()())>::value> exec_func;

	// Task object embedded directly. The ref-count is initialized to 1 so it
	// will never be freed using delete, only when the local_task is destroyed.
	detail::task_func<Sched, exec_func, internal_result> internal_task;

	// Friend access for local_spawn
	template<typename S, typename F>
	friend local_task<S, F> local_spawn(S& sched, F&& f);
	template<typename F>
	friend local_task<detail::default_scheduler_type, F> local_spawn(F&& f);

	// Constructor, used by local_spawn
	local_task(Sched& sched, Func&& f)
		: internal_task(std::forward<Func>(f))
	{
		// Avoid an expensive ref-count modification since the task isn't shared yet
		internal_task.add_ref_unlocked();
		detail::schedule_task(sched, detail::task_ptr(&internal_task));
	}

public:
	// Non-movable and non-copyable
	local_task(const local_task&) = delete;
	local_task& operator=(const local_task&) = delete;

	// Wait for the task to complete when destroying
	~local_task()
	{
		wait();

		// Now spin until the reference count drops to 1, since the scheduler
		// may still have a reference to the task.
		while (!internal_task.is_unique_ref(std::memory_order_acquire)) {
#if defined(__GLIBCXX__) && __GLIBCXX__ <= 20140612
			// Some versions of libstdc++ (4.7 and below) don't include a
			// definition of std::this_thread::yield().
			sched_yield();
#else
			std::this_thread::yield();
#endif
		}
	}

	// Query whether the task has finished executing
	bool ready() const
	{
		return internal_task.ready();
	}

	// Query whether the task has been canceled with an exception
	bool canceled() const
	{
		return internal_task.state.load(std::memory_order_acquire) == detail::task_state::canceled;
	}

	// Wait for the task to complete
	void wait()
	{
		internal_task.wait();
	}

	// Get the result of the task
	result_type get()
	{
		internal_task.wait_and_throw();
		return detail::fake_void_to_void(internal_task.get_result(task<result_type>()));
	}

	// Get the exception associated with a canceled task
	std::exception_ptr get_exception() const
	{
		if (internal_task.wait() == detail::task_state::canceled)
			return internal_task.get_exception();
		else
			return std::exception_ptr();
	}
};

// Spawn a function asynchronously
template<typename Sched, typename Func>
task<typename detail::remove_task<typename std::result_of<typename std::decay<Func>::type()>::type>::type> spawn(Sched& sched, Func&& f)
{
	// Using result_of in the function return type to work around bugs in the Intel
	// C++ compiler.

	// Make sure the function type is callable
	typedef typename std::decay<Func>::type decay_func;
	static_assert(detail::is_callable<decay_func()>::value, "Invalid function type passed to spawn()");

	// Create task
	typedef typename detail::void_to_fake_void<typename detail::remove_task<decltype(std::declval<decay_func>()())>::type>::type internal_result;
	typedef detail::root_exec_func<Sched, internal_result, decay_func, detail::is_task<decltype(std::declval<decay_func>()())>::value> exec_func;
	task<typename detail::remove_task<decltype(std::declval<decay_func>()())>::type> out;
	detail::set_internal_task(out, detail::task_ptr(new detail::task_func<Sched, exec_func, internal_result>(std::forward<Func>(f))));

	// Avoid an expensive ref-count modification since the task isn't shared yet
	detail::get_internal_task(out)->add_ref_unlocked();
	detail::schedule_task(sched, detail::task_ptr(detail::get_internal_task(out)));

	return out;
}
template<typename Func>
decltype(async::spawn(::async::default_scheduler(), std::declval<Func>())) spawn(Func&& f)
{
	return async::spawn(::async::default_scheduler(), std::forward<Func>(f));
}

// Create a completed task containing a value
template<typename T>
task<typename std::decay<T>::type> make_task(T&& value)
{
	task<typename std::decay<T>::type> out;

	detail::set_internal_task(out, detail::task_ptr(new detail::task_result<typename std::decay<T>::type>));
	detail::get_internal_task(out)->set_result(std::forward<T>(value));
	detail::get_internal_task(out)->state.store(detail::task_state::completed, std::memory_order_relaxed);

	return out;
}
template<typename T>
task<T&> make_task(std::reference_wrapper<T> value)
{
	task<T&> out;

	detail::set_internal_task(out, detail::task_ptr(new detail::task_result<T&>));
	detail::get_internal_task(out)->set_result(value.get());
	detail::get_internal_task(out)->state.store(detail::task_state::completed, std::memory_order_relaxed);

	return out;
}
inline task<void> make_task()
{
	task<void> out;

	detail::set_internal_task(out, detail::task_ptr(new detail::task_result<detail::fake_void>));
	detail::get_internal_task(out)->state.store(detail::task_state::completed, std::memory_order_relaxed);

	return out;
}

// Create a canceled task containing an exception
template<typename T>
task<T> make_exception_task(std::exception_ptr except)
{
	task<T> out;

	detail::set_internal_task(out, detail::task_ptr(new detail::task_result<typename detail::void_to_fake_void<T>::type>));
	detail::get_internal_task(out)->set_exception(std::move(except));
	detail::get_internal_task(out)->state.store(detail::task_state::canceled, std::memory_order_relaxed);

	return out;
}

// Spawn a very limited task which is restricted to the current function and
// joins on destruction. Because local_task is not movable, the result must
// be captured in a reference, like this:
// auto&& x = local_spawn(...);
template<typename Sched, typename Func>
#ifdef __GNUC__
__attribute__((warn_unused_result))
#endif
local_task<Sched, Func> local_spawn(Sched& sched, Func&& f)
{
	// Since local_task is not movable, we construct it in-place and let the
	// caller extend the lifetime of the returned object using a reference.
	return {sched, std::forward<Func>(f)};
}
template<typename Func>
#ifdef __GNUC__
__attribute__((warn_unused_result))
#endif
local_task<detail::default_scheduler_type, Func> local_spawn(Func&& f)
{
	return {::async::default_scheduler(), std::forward<Func>(f)};
}

} // namespace async
