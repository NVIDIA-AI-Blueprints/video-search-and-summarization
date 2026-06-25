/* Code for parts of pqxx::internal::stream_query.
 *
 * These definitions needs to be in a separate file in order to iron out
 * circular dependencies between headers.
 */
#if !defined(PQXX_H_STREAM_QUERY_IMPL)
#  define PQXX_H_STREAM_QUERY_IMPL

namespace pqxx::internal
{
template<typename... TYPE>
inline stream_query<TYPE...>::stream_query(
  transaction_base &tx, std::string_view query) :
        transaction_focus{tx, "stream_query"}, m_char_finder{get_finder(tx)}
{
  auto const r{tx.exec0(internal::concat("COPY (", query, ") TO STDOUT"))};
  if (r.columns() != sizeof...(TYPE))
    throw usage_error{concat(
      "Parsing query stream with wrong number of columns: "
      "code expects ",
      sizeof...(TYPE), " but query returns ", r.columns(), ".")};
  register_me();
}


template<typename... TYPE>
inline char_finder_func *
stream_query<TYPE...>::get_finder(transaction_base const &tx)
{
  auto const group{enc_group(tx.conn().encoding_id())};
  return get_s_char_finder<'\t', '\\'>(group);
}


// C++20: Replace with generator?  Could be faster (local vars vs. members).
/// Input iterator for stream_query.
/** Just barely enough to support range-based "for" loops on stream_from.
 * Don't assume that any of the usual behaviour works beyond that.
 */
template<typename... TYPE> class stream_query_input_iterator
{
  using stream_t = stream_query<TYPE...>;

public:
  using value_type = std::tuple<TYPE...>;

  explicit stream_query_input_iterator(stream_t &home) :
          m_home(home),
          m_line{typename stream_query<TYPE...>::line_handle(
            nullptr, pqxx::internal::pq::pqfreemem)}
  {
    consume_line();
  }
  stream_query_input_iterator(stream_query_input_iterator const &) = default;
  stream_query_input_iterator(stream_query_input_iterator &&) = default;

  /// Pre-increment.  (There's no post-increment.)
  stream_query_input_iterator &operator++() &
  {
    assert(not done());
    consume_line();
    return *this;
  }

  /// Dereference.  There's no caching in here, so don't repeat calls.
  value_type operator*() const
  {
    return m_home.parse_line(zview{m_line.get(), m_line_size});
  }

  /// Are we at the end?
  bool operator==(stream_query_end_iterator) const noexcept { return done(); }
  /// Comparison only works for comparing to end().
  bool operator!=(stream_query_end_iterator) const noexcept
  {
    return not done();
  }

private:
  /// Have we finished?
  bool done() const noexcept { return m_home.done(); }

  /// Read a line from the stream, store it in the iterator.
  void consume_line() &
  {
    auto [line, size]{m_home.read_line()};
    m_line = std::move(line);
    m_line_size = size;
    if (size > 0)
    {
      // We know how many fields to expect.  Replace the newline at the end
      // with the field separator, so the parsing loop only needs to scan for a
      // tab, not a tab or a newline.
      char *const ptr{m_line.get()};
      assert(ptr[size] == '\n');
      ptr[size] = '\t';
    }
  }

  stream_t &m_home;

  /// Last COPY line we read, allocated by libpq.
  typename stream_t::line_handle m_line;

  /// Length of the last COPY line we read.
  std::size_t m_line_size;
};


template<typename... TYPE>
inline bool operator==(
  stream_query_end_iterator, stream_query_input_iterator<TYPE...> const &i)
{
  return i.done();
}


template<typename... TYPE>
inline bool operator!=(
  stream_query_end_iterator, stream_query_input_iterator<TYPE...> const &i)
{
  return not i.done();
}


template<typename... TYPE> inline auto stream_query<TYPE...>::begin() &
{
  return stream_query_input_iterator<TYPE...>{*this};
}


template<typename... TYPE>
inline std::pair<typename stream_query<TYPE...>::line_handle, std::size_t>
stream_query<TYPE...>::read_line() &
{
  assert(not done());

  internal::gate::connection_stream_from gate{m_trans->conn()};
  try
  {
    auto line{gate.read_copy_line()};
    // Check for completion.
    if (not line.first)
      PQXX_UNLIKELY close();
    return line;
  }
  catch (std::exception const &)
  {
    close();
    throw;
  }
}
} // namespace pqxx::internal
#endif
