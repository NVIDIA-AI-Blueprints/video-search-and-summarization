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

// Result type for when_any
template<typename Result>
struct when_any_result {
	// Index of the task that finished first
	std::size_t index;

	// List of tasks that were passed in
	Result tasks;
};

namespace detail {

// Shared state for when_all
template<typename Result>
struct when_all_state: public ref_count_base<when_all_state<Result>> {
	event_task<Result> event;
	Result result;

	when_all_state(std::size_t count)
		: ref_count_base<when_all_state<Result>>(count) {}

	// When all references are dropped, signal the event
	~when_all_state()
	{
		event.set(std::move(result));
	}
};

// Execution functions for when_all, for ranges and tuples
template<typename Task, typename Result>
struct when_all_func_range {
	std::size_t index;
	ref_count_ptr<when_all_state<Result>> state;

	when_all_func_range(std::size_t index, ref_count_ptr<when_all_state<Result>> state)
		: index(index), state(std::move(state)) {}

	// Copy the completed task object to the shared state. The event is
	// automatically signaled when all references are dropped.
	void operator()(Task t) const
	{
		state->result[index] = std::move(t);
	}
};
template<std::size_t index, typename Task, typename Result>
struct when_all_func_tuple {
	ref_count_ptr<when_all_state<Result>> state;

	when_all_func_tuple(ref_count_ptr<when_all_state<Result>> state)
		: state(std::move(state)) {}

	// Copy the completed task object to the shared state. The event is
	// automatically signaled when all references are dropped.
	void operator()(Task t) const
	{
		std::get<index>(state->result) = std::move(t);
	}
};

// Shared state for when_any
template<typename Result>
struct when_any_state: public ref_count_base<when_any_state<Result>> {
	event_task<when_any_result<Result>> event;
	Result result;

	when_any_state(std::size_t count)
		: ref_count_base<when_any_state<Result>>(count) {}

	// Signal the event when the first task reaches here
	void set(std::size_t i)
	{
		event.set({i, std::move(result)});
	}
};

// Execution function for when_any
template<typename Task, typename Result>
struct when_any_func {
	std::size_t index;
	ref_count_ptr<when_any_state<Result>> state;

	when_any_func(std::size_t index, ref_count_ptr<when_any_state<Result>> state)
		: index(index), state(std::move(state)) {}

	// Simply tell the state that our task has finished, it already has a copy
	// of the task object.
	void operator()(Task) const
	{
		state->set(index);
	}
};

// Internal implementation of when_all for variadic arguments
template<std::size_t index, typename Result>
void when_all_variadic(when_all_state<Result>*) {}
template<std::size_t index, typename Result, typename First, typename... T>
void when_all_variadic(when_all_state<Result>* state, First&& first, T&&... tasks)
{
	typedef typename std::decay<First>::type task_type;

	// Add a continuation to the task
	LIBASYNC_TRY {
		first.then(inline_scheduler(), detail::when_all_func_tuple<index, task_type, Result>(detail::ref_count_ptr<detail::when_all_state<Result>>(state)));
	} LIBASYNC_CATCH(...) {
		// Make sure we don't leak memory if then() throws
		state->remove_ref(sizeof...(T));
		LIBASYNC_RETHROW();
	}

	// Add continuations to remaining tasks
	detail::when_all_variadic<index + 1>(state, std::forward<T>(tasks)...);
}

// Internal implementation of when_any for variadic arguments
template<std::size_t index, typename Result>
void when_any_variadic(when_any_state<Result>*) {}
template<std::size_t index, typename Result, typename First, typename... T>
void when_any_variadic(when_any_state<Result>* state, First&& first, T&&... tasks)
{
	typedef typename std::decay<First>::type task_type;

	// Add a copy of the task to the results because the event may be
	// set before all tasks have finished.
	detail::task_base* t = detail::get_internal_task(first);
	t->add_ref();
	detail::set_internal_task(std::get<index>(state->result), detail::task_ptr(t));

	// Add a continuation to the task
	LIBASYNC_TRY {
		first.then(inline_scheduler(), detail::when_any_func<task_type, Result>(index, detail::ref_count_ptr<detail::when_any_state<Result>>(state)));
	} LIBASYNC_CATCH(...) {
		// Make sure we don't leak memory if then() throws
		state->remove_ref(sizeof...(T));
		LIBASYNC_RETHROW();
	}

	// Add continuations to remaining tasks
	detail::when_any_variadic<index + 1>(state, std::forward<T>(tasks)...);
}

} // namespace detail

// Combine a set of tasks into one task which is signaled when all specified tasks finish
template<typename Iter>
task<std::vector<typename std::decay<typename std::iterator_traits<Iter>::value_type>::type>> when_all(Iter begin, Iter end)
{
	typedef typename std::decay<typename std::iterator_traits<Iter>::value_type>::type task_type;
	typedef std::vector<task_type> result_type;

	// Handle empty ranges
	if (begin == end)
		return make_task(result_type());

	// Create shared state, initialized with the proper reference count
	std::size_t count = std::distance(begin, end);
	auto* state = new detail::when_all_state<result_type>(count);
	state->result.resize(count);
	auto out = state->event.get_task();

	// Add a continuation to each task to add its result to the shared state
	// Last task sets the event result
	for (std::size_t i = 0; begin != end; i++, ++begin) {
		LIBASYNC_TRY {
			(*begin).then(inline_scheduler(), detail::when_all_func_range<task_type, result_type>(i, detail::ref_count_ptr<detail::when_all_state<result_type>>(state)));
		} LIBASYNC_CATCH(...) {
			// Make sure we don't leak memory if then() throws
			state->remove_ref(std::distance(begin, end) - 1);
			LIBASYNC_RETHROW();
		}
	}

	return out;
}

// Combine a set of tasks into one task which is signaled when one of the tasks finishes
template<typename Iter>
task<when_any_result<std::vector<typename std::decay<typename std::iterator_traits<Iter>::value_type>::type>>> when_any(Iter begin, Iter end)
{
	typedef typename std::decay<typename std::iterator_traits<Iter>::value_type>::type task_type;
	typedef std::vector<task_type> result_type;

	// Handle empty ranges
	if (begin == end)
		return make_task(when_any_result<result_type>());

	// Create shared state, initialized with the proper reference count
	std::size_t count = std::distance(begin, end);
	auto* state = new detail::when_any_state<result_type>(count);
	state->result.resize(count);
	auto out = state->event.get_task();

	// Add a continuation to each task to set the event. First one wins.
	for (std::size_t i = 0; begin != end; i++, ++begin) {
		// Add a copy of the task to the results because the event may be
		// set before all tasks have finished.
		detail::task_base* t = detail::get_internal_task(*begin);
		t->add_ref();
		detail::set_internal_task(state->result[i], detail::task_ptr(t));

		LIBASYNC_TRY {
			(*begin).then(inline_scheduler(), detail::when_any_func<task_type, result_type>(i, detail::ref_count_ptr<detail::when_any_state<result_type>>(state)));
		} LIBASYNC_CATCH(...) {
			// Make sure we don't leak memory if then() throws
			state->remove_ref(std::distance(begin, end) - 1);
			LIBASYNC_RETHROW();
		}
	}

	return out;
}

// when_all wrapper accepting ranges
template<typename T>
decltype(async::when_all(std::begin(std::declval<T>()), std::end(std::declval<T>()))) when_all(T&& tasks)
{
	return async::when_all(std::begin(std::forward<T>(tasks)), std::end(std::forward<T>(tasks)));
}

// when_any wrapper accepting ranges
template<typename T>
decltype(async::when_any(std::begin(std::declval<T>()), std::end(std::declval<T>()))) when_any(T&& tasks)
{
	return async::when_any(std::begin(std::forward<T>(tasks)), std::end(std::forward<T>(tasks)));
}

// when_all with variadic arguments
inline task<std::tuple<>> when_all()
{
	return async::make_task(std::tuple<>());
}
template<typename... T>
task<std::tuple<typename std::decay<T>::type...>> when_all(T&&... tasks)
{
	typedef std::tuple<typename std::decay<T>::type...> result_type;

	// Create shared state
	auto state = new detail::when_all_state<result_type>(sizeof...(tasks));
	auto out = state->event.get_task();

	// Register all the tasks on the event
	detail::when_all_variadic<0>(state, std::forward<T>(tasks)...);

	return out;
}

// when_any with variadic arguments
inline task<when_any_result<std::tuple<>>> when_any()
{
	return async::make_task(when_any_result<std::tuple<>>());
}
template<typename... T>
task<when_any_result<std::tuple<typename std::decay<T>::type...>>> when_any(T&&... tasks)
{
	typedef std::tuple<typename std::decay<T>::type...> result_type;

	// Create shared state
	auto state = new detail::when_any_state<result_type>(sizeof...(tasks));
	auto out = state->event.get_task();

	// Register all the tasks on the event
	detail::when_any_variadic<0>(state, std::forward<T>(tasks)...);

	return out;
}

} // namespace async
