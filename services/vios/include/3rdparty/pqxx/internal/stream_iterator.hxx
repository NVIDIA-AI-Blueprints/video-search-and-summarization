/** Stream iterators.
 *
 * Copyright (c) 2000-2023, Jeroen T. Vermeulen.
 *
 * See COPYING for copyright license.  If you did not receive a file called
 * COPYING with this source code, please notify the distributor of this
 * mistake, or contact the author.
 */
#ifndef PQXX_H_STREAM_ITERATOR
#define PQXX_H_STREAM_ITERATOR

#include <memory>

namespace pqxx
{
template<typename... TYPE> class stream_query;
}


namespace pqxx::internal
{
// C++20: Replace with generator?
/// Input iterator for stream_from.
/** Just barely enough to support range-based "for" loops on stream_from.
 * Don't assume that any of the usual behaviour works beyond that.
 */
template<typename... TYPE> class stream_from_input_iterator
{
  using stream_t = stream_from;

public:
  using value_type = std::tuple<TYPE...>;

  /// Construct an "end" iterator.
  stream_from_input_iterator() = default;

  explicit stream_from_input_iterator(stream_t &home) : m_home(&home)
  {
    advance();
  }
  stream_from_input_iterator(stream_from_input_iterator const &) = default;

  stream_from_input_iterator &operator++()
  {
    advance();
    return *this;
  }

  value_type const &operator*() const { return m_value; }

  /// Comparison only works for comparing to end().
  bool operator==(stream_from_input_iterator const &rhs) const
  {
    return m_home == rhs.m_home;
  }
  /// Comparison only works for comparing to end().
  bool operator!=(stream_from_input_iterator const &rhs) const
  {
    return not(*this == rhs);
  }

private:
  void advance()
  {
    if (m_home == nullptr)
      throw usage_error{"Moving stream_from iterator beyond end()."};
    if (not((*m_home) >> m_value))
      m_home = nullptr;
  }

  stream_t *m_home{nullptr};
  value_type m_value;
};


// C++20: Replace with generator?
/// Iteration over a @ref stream_query.
template<typename... TYPE> class stream_input_iteration
{
public:
  using stream_t = stream_from;
  using iterator = stream_from_input_iterator<TYPE...>;
  explicit stream_input_iteration(stream_t &home) : m_home{home} {}
  iterator begin() const { return iterator{m_home}; }
  iterator end() const { return {}; }

private:
  stream_t &m_home;
};
} // namespace pqxx::internal
#endif
