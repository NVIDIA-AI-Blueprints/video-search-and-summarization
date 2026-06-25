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
namespace detail {

// Partitioners are essentially ranges with an extra split() function. The
// split() function returns a partitioner containing a range to be executed in a
// child task and modifies the parent partitioner's range to represent the rest
// of the original range. If the range cannot be split any more then split()
// should return an empty range.

// Detect whether a range is a partitioner
template<typename T, typename = decltype(std::declval<T>().split())>
two& is_partitioner_helper(int);
template<typename T>
one& is_partitioner_helper(...);
template<typename T>
struct is_partitioner: public std::integral_constant<bool, sizeof(is_partitioner_helper<T>(0)) - 1> {};

// Automatically determine a grain size for a sequence length
inline std::size_t auto_grain_size(std::size_t dist)
{
	// Determine the grain size automatically using a heuristic
	std::size_t grain = dist / (8 * hardware_concurrency());
	if (grain < 1)
		grain = 1;
	if (grain > 2048)
		grain = 2048;
	return grain;
}

template<typename Iter>
class static_partitioner_impl {
	Iter iter_begin, iter_end;
	std::size_t grain;

public:
	static_partitioner_impl(Iter begin, Iter end, std::size_t grain)
		: iter_begin(begin), iter_end(end), grain(grain) {}
	Iter begin() const
	{
		return iter_begin;
	}
	Iter end() const
	{
		return iter_end;
	}
	static_partitioner_impl split()
	{
		// Don't split if below grain size
		std::size_t length = std::distance(iter_begin, iter_end);
		static_partitioner_impl out(iter_end, iter_end, grain);
		if (length <= grain)
			return out;

		// Split our range in half
		iter_end = iter_begin;
		std::advance(iter_end, (length + 1) / 2);
		out.iter_begin = iter_end;
		return out;
	}
};

template<typename Iter>
class auto_partitioner_impl {
	Iter iter_begin, iter_end;
	std::size_t grain;
	std::size_t num_threads;
	std::thread::id last_thread;

public:
	// thread_id is initialized to "no thread" and will be set on first split
	auto_partitioner_impl(Iter begin, Iter end, std::size_t grain)
		: iter_begin(begin), iter_end(end), grain(grain) {}
	Iter begin() const
	{
		return iter_begin;
	}
	Iter end() const
	{
		return iter_end;
	}
	auto_partitioner_impl split()
	{
		// Don't split if below grain size
		std::size_t length = std::distance(iter_begin, iter_end);
		auto_partitioner_impl out(iter_end, iter_end, grain);
		if (length <= grain)
			return out;

		// Check if we are in a different thread than we were before
		std::thread::id current_thread = std::this_thread::get_id();
		if (current_thread != last_thread)
			num_threads = hardware_concurrency();

		// If we only have one thread, don't split
		if (num_threads <= 1)
			return out;

		// Split our range in half
		iter_end = iter_begin;
		std::advance(iter_end, (length + 1) / 2);
		out.iter_begin = iter_end;
		out.last_thread = current_thread;
		last_thread = current_thread;
		out.num_threads = num_threads / 2;
		num_threads -= out.num_threads;
		return out;
	}
};

} // namespace detail

// A simple partitioner which splits until a grain size is reached. If a grain
// size is not specified, one is chosen automatically.
template<typename Range>
detail::static_partitioner_impl<decltype(std::begin(std::declval<Range>()))> static_partitioner(Range&& range, std::size_t grain)
{
	return {std::begin(range), std::end(range), grain};
}
template<typename Range>
detail::static_partitioner_impl<decltype(std::begin(std::declval<Range>()))> static_partitioner(Range&& range)
{
	std::size_t grain = detail::auto_grain_size(std::distance(std::begin(range), std::end(range)));
	return {std::begin(range), std::end(range), grain};
}

// A more advanced partitioner which initially divides the range into one chunk
// for each available thread. The range is split further if a chunk gets stolen
// by a different thread.
template<typename Range>
detail::auto_partitioner_impl<decltype(std::begin(std::declval<Range>()))> auto_partitioner(Range&& range)
{
	std::size_t grain = detail::auto_grain_size(std::distance(std::begin(range), std::end(range)));
	return {std::begin(range), std::end(range), grain};
}

// Wrap a range in a partitioner. If the input is already a partitioner then it
// is returned unchanged. This allows parallel algorithms to accept both ranges
// and partitioners as parameters.
template<typename Partitioner>
typename std::enable_if<detail::is_partitioner<typename std::decay<Partitioner>::type>::value, Partitioner&&>::type to_partitioner(Partitioner&& partitioner)
{
	return std::forward<Partitioner>(partitioner);
}
template<typename Range>
typename std::enable_if<!detail::is_partitioner<typename std::decay<Range>::type>::value, detail::auto_partitioner_impl<decltype(std::begin(std::declval<Range>()))>>::type to_partitioner(Range&& range)
{
	return async::auto_partitioner(std::forward<Range>(range));
}

// Overloads with std::initializer_list
template<typename T>
detail::static_partitioner_impl<decltype(std::declval<std::initializer_list<T>>().begin())> static_partitioner(std::initializer_list<T> range)
{
	return async::static_partitioner(async::make_range(range.begin(), range.end()));
}
template<typename T>
detail::static_partitioner_impl<decltype(std::declval<std::initializer_list<T>>().begin())> static_partitioner(std::initializer_list<T> range, std::size_t grain)
{
	return async::static_partitioner(async::make_range(range.begin(), range.end()), grain);
}
template<typename T>
detail::auto_partitioner_impl<decltype(std::declval<std::initializer_list<T>>().begin())> auto_partitioner(std::initializer_list<T> range)
{
	return async::auto_partitioner(async::make_range(range.begin(), range.end()));
}
template<typename T>
detail::auto_partitioner_impl<decltype(std::declval<std::initializer_list<T>>().begin())> to_partitioner(std::initializer_list<T> range)
{
	return async::auto_partitioner(async::make_range(range.begin(), range.end()));
}

} // namespace async
