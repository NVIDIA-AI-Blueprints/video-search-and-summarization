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

// Compress the flags in the low bits of the pointer if the structures are
// suitably aligned. Fall back to a separate flags variable otherwise.
template<std::uintptr_t Mask, bool Enable>
class compressed_ptr {
	void* ptr;
	std::uintptr_t flags;

public:
	compressed_ptr() = default;
	compressed_ptr(void* ptr, std::uintptr_t flags)
		: ptr(ptr), flags(flags) {}

	template<typename T>
	T* get_ptr() const
	{
		return static_cast<T*>(ptr);
	}
	std::uintptr_t get_flags() const
	{
		return flags;
	}

	void set_ptr(void* p)
	{
		ptr = p;
	}
	void set_flags(std::uintptr_t f)
	{
		flags = f;
	}
};
template<std::uintptr_t Mask>
class compressed_ptr<Mask, true> {
	std::uintptr_t data;

public:
	compressed_ptr() = default;
	compressed_ptr(void* ptr, std::uintptr_t flags)
		: data(reinterpret_cast<std::uintptr_t>(ptr) | flags) {}

	template<typename T>
	T* get_ptr() const
	{
		return reinterpret_cast<T*>(data & ~Mask);
	}
	std::uintptr_t get_flags() const
	{
		return data & Mask;
	}

	void set_ptr(void* p)
	{
		data = reinterpret_cast<std::uintptr_t>(p) | (data & Mask);
	}
	void set_flags(std::uintptr_t f)
	{
		data = (data & ~Mask) | f;
	}
};

// Thread-safe vector of task_ptr which is optimized for the common case of
// only having a single continuation.
class continuation_vector {
	// Heap-allocated data for the slow path
	struct vector_data {
		std::vector<task_base*> vector;
		std::mutex lock;
	};

	// Flags to describe the state of the vector
	enum flags {
		// If set, no more changes are allowed to internal_data
		is_locked = 1,

		// If set, the pointer is a vector_data* instead of a task_base*. If
		// there are 0 or 1 elements in the vector, the task_base* form is used.
		is_vector = 2
	};
	static const std::uintptr_t flags_mask = 3;

	// Embed the two bits in the data if they are suitably aligned. We only
	// check the alignment of vector_data here because task_base isn't defined
	// yet. Since we align task_base to LIBASYNC_CACHELINE_SIZE just use that.
	typedef compressed_ptr<flags_mask, (LIBASYNC_CACHELINE_SIZE & flags_mask) == 0 &&
	                                   (std::alignment_of<vector_data>::value & flags_mask) == 0> internal_data;

	// All changes to the internal data are atomic
	std::atomic<internal_data> atomic_data;

public:
	// Start unlocked with zero elements in the fast path
	continuation_vector()
	{
		// Workaround for a bug in certain versions of clang with libc++
		// error: no viable conversion from 'async::detail::compressed_ptr<3, true>' to '_Atomic(async::detail::compressed_ptr<3, true>)'
		atomic_data.store(internal_data(nullptr, 0), std::memory_order_relaxed);
	}

	// Free any left over data
	~continuation_vector()
	{
		// Converting to task_ptr instead of using remove_ref because task_base
		// isn't defined yet at this point.
		internal_data data = atomic_data.load(std::memory_order_relaxed);
		if (data.get_flags() & flags::is_vector) {
			// No need to lock the mutex, we are the only thread at this point
			for (task_base* i: data.get_ptr<vector_data>()->vector)
				(task_ptr(i));
			delete data.get_ptr<vector_data>();
		} else {
			// If the data is locked then the inline pointer is already gone
			if (!(data.get_flags() & flags::is_locked))
				task_ptr tmp(data.get_ptr<task_base>());
		}
	}

	// Try adding an element to the vector. This fails and returns false if
	// the vector has been locked. In that case t is not modified.
	bool try_add(task_ptr&& t)
	{
		// Cache to avoid re-allocating vector_data multiple times. This is
		// automatically freed if it is not successfully saved to atomic_data.
		std::unique_ptr<vector_data> vector;

		// Compare-exchange loop on atomic_data
		internal_data data = atomic_data.load(std::memory_order_relaxed);
		internal_data new_data;
		do {
			// Return immediately if the vector is locked
			if (data.get_flags() & flags::is_locked)
				return false;

			if (data.get_flags() & flags::is_vector) {
				// Larger vectors use a mutex, so grab the lock
				std::atomic_thread_fence(std::memory_order_acquire);
				std::lock_guard<std::mutex> locked(data.get_ptr<vector_data>()->lock);

				// We need to check again if the vector has been locked here
				// to avoid a race condition with flush_and_lock
				if (atomic_data.load(std::memory_order_relaxed).get_flags() & flags::is_locked)
					return false;

				// Add the element to the vector and return
				data.get_ptr<vector_data>()->vector.push_back(t.release());
				return true;
			} else {
				if (data.get_ptr<task_base>()) {
					// Going from 1 to 2 elements, allocate a vector_data
					if (!vector)
						vector.reset(new vector_data{{data.get_ptr<task_base>(), t.get()}, {}});
					new_data = {vector.get(), flags::is_vector};
				} else {
					// Going from 0 to 1 elements
					new_data = {t.get(), 0};
				}
			}
		} while (!atomic_data.compare_exchange_weak(data, new_data, std::memory_order_release, std::memory_order_relaxed));

		// If we reach this point then atomic_data was successfully changed.
		// Since the pointers are now saved in the vector, release them from
		// the smart pointers.
		t.release();
		vector.release();
		return true;
	}

	// Lock the vector and flush all elements through the given function
	template<typename Func> void flush_and_lock(Func&& func)
	{
		// Try to lock the vector using a compare-exchange loop
		internal_data data = atomic_data.load(std::memory_order_relaxed);
		internal_data new_data;
		do {
			new_data = data;
			new_data.set_flags(data.get_flags() | flags::is_locked);
		} while (!atomic_data.compare_exchange_weak(data, new_data, std::memory_order_acquire, std::memory_order_relaxed));

		if (data.get_flags() & flags::is_vector) {
			// If we are using vector_data, lock it and flush all elements
			std::lock_guard<std::mutex> locked(data.get_ptr<vector_data>()->lock);
			for (auto i: data.get_ptr<vector_data>()->vector)
				func(task_ptr(i));

			// Clear the vector to save memory. Note that we don't actually free
			// the vector_data here because other threads may still be using it.
			// This isn't a very significant cost since multiple continuations
			// are relatively rare.
			data.get_ptr<vector_data>()->vector.clear();
		} else {
			// If there is an inline element, just pass it on
			if (data.get_ptr<task_base>())
				func(task_ptr(data.get_ptr<task_base>()));
		}
	}
};

} // namespace detail
} // namespace async
