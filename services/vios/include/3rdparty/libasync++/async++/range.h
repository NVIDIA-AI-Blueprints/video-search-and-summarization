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

// Range type representing a pair of iterators
template<typename Iter>
class range {
	Iter iter_begin, iter_end;

public:
	range() = default;
	range(Iter a, Iter b)
		: iter_begin(a), iter_end(b) {}

	Iter begin() const
	{
		return iter_begin;
	}
	Iter end() const
	{
		return iter_end;
	}
};

// Construct a range from 2 iterators
template<typename Iter>
range<Iter> make_range(Iter begin, Iter end)
{
	return {begin, end};
}

// A range of integers
template<typename T>
class int_range {
	T value_begin, value_end;

	static_assert(std::is_integral<T>::value, "int_range can only be used with integral types");

public:
	class iterator {
		T current;

		explicit iterator(T a)
			: current(a) {}
		friend class int_range<T>;

	public:
		typedef T value_type;
		typedef std::ptrdiff_t difference_type;
		typedef iterator pointer;
		typedef T reference;
		typedef std::random_access_iterator_tag iterator_category;

		iterator() = default;

		T operator*() const
		{
			return current;
		}
		T operator[](difference_type offset) const
		{
			return current + offset;
		}

		iterator& operator++()
		{
			++current;
			return *this;
		}
		iterator operator++(int)
		{
			return iterator(current++);
		}
		iterator& operator--()
		{
			--current;
			return *this;
		}
		iterator operator--(int)
		{
			return iterator(current--);
		}

		iterator& operator+=(difference_type offset)
		{
			current += offset;
			return *this;
		}
		iterator& operator-=(difference_type offset)
		{
			current -= offset;
			return *this;
		}

		iterator operator+(difference_type offset) const
		{
			return iterator(current + offset);
		}
		iterator operator-(difference_type offset) const
		{
			return iterator(current - offset);
		}

		friend iterator operator+(difference_type offset, iterator other)
		{
			return other + offset;
		}

		friend difference_type operator-(iterator a, iterator b)
		{
			return a.current - b.current;
		}

		friend bool operator==(iterator a, iterator b)
		{
			return a.current == b.current;
		}
		friend bool operator!=(iterator a, iterator b)
		{
			return a.current != b.current;
		}
		friend bool operator>(iterator a, iterator b)
		{
			return a.current > b.current;
		}
		friend bool operator<(iterator a, iterator b)
		{
			return a.current < b.current;
		}
		friend bool operator>=(iterator a, iterator b)
		{
			return a.current >= b.current;
		}
		friend bool operator<=(iterator a, iterator b)
		{
			return a.current <= b.current;
		}
	};

	int_range(T begin, T end)
		: value_begin(begin), value_end(end) {}

	iterator begin() const
	{
		return iterator(value_begin);
	}
	iterator end() const
	{
		return iterator(value_end);
	}
};

// Construct an int_range between 2 values
template<typename T, typename U>
int_range<typename std::common_type<T, U>::type> irange(T begin, U end)
{
	return {begin, end};
}

} // namespace async
