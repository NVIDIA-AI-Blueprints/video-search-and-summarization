/* Definition of the pqxx::internal::stream_query class.
 *
 * Enables optimized batch reads from a database table.
 *
 * DO NOT INCLUDE THIS FILE DIRECTLY; include pqxx/stream_query instead.
 *
 * Copyright (c) 2000-2023, Jeroen T. Vermeulen.
 *
 * See COPYING for copyright license.  If you did not receive a file called
 * COPYING with this source code, please notify the distributor of this
 * mistake, or contact the author.
 */
#ifndef PQXX_H_STREAM_QUERY
#define PQXX_H_STREAM_QUERY

#if !defined(PQXX_HEADER_PRE)
#  error "Include libpqxx headers as <pqxx/header>, not <pqxx/header.hxx>."
#endif

#include <cassert>
#include <functional>
#include <variant>

#include "pqxx/connection.hxx"
#include "pqxx/except.hxx"
#include "pqxx/internal/concat.hxx"
#include "pqxx/internal/encoding_group.hxx"
#include "pqxx/internal/encodings.hxx"
#include "pqxx/internal/gates/connection-stream_from.hxx"
#include "pqxx/internal/stream_iterator.hxx"
#include "pqxx/separated_list.hxx"
#include "pqxx/transaction_base.hxx"
#include "pqxx/transaction_focus.hxx"
#include "pqxx/util.hxx"


namespace pqxx
{
class transaction_base;
} // namespace pqxx


namespace pqxx::internal
{
/// The `end()` iterator for a `stream_query`.
class stream_query_end_iterator
{};


// C++20: Can we use generators, and maybe get speedup from HALO?
/// Stream query results from the database.  Used by transaction_base::stream.
/** For larger data sets, retrieving data this way is likely to be faster than
 * executing a query and then iterating and converting the rows' fields.  You
 * will also be able to start processing before all of the data has come in.
 * (For smaller result sets though, a stream is likely to be a bit slower.)
 *
 * A `stream_query` stream is strongly typed.  You specify the columns' types
 * while instantiating the stream_query template.
 *
 * Not all kinds of query will work in a stream.  But straightforward `SELECT`
 * and `UPDATE ... RETURNING` queries should work.  The class uses PostgreSQL's
 * `COPY` command, so see the documentation for that command to get the full
 * details.
 *
 * There are other downsides.  If the stream encounters an error, it may leave
 * the entire connection in an unusable state, so you'll have to give the
 * whole thing up.  Finally, opening a stream puts the connection in a special
 * state, so you won't be able to do many other things with the connection or
 * the transaction while the stream is open.
 *
 * Usually you'll want the `stream` convenience wrapper in
 * @ref transaction_base, * so you don't need to deal with this class directly.
 *
 * @warning While a stream is active, you cannot execute queries, open a
 * pipeline, etc. on the same transaction.  A transaction can have at most one
 * object of a type derived from @ref pqxx::transaction_focus active on it at a
 * time.
 */
template<typename... TYPE> class stream_query : transaction_focus
{
public:
  using line_handle = std::unique_ptr<char, void (*)(void const *)>;

  /// Execute `query` on `tx`, stream results.
  inline stream_query(transaction_base &tx, std::string_view query);

  stream_query(stream_query &&) = delete;
  stream_query &operator=(stream_query &&) = delete;

  ~stream_query() noexcept
  {
    try
    {
      close();
    }
    catch (std::exception const &e)
    {
      reg_pending_error(e.what());
    }
  }

  /// Has this stream reached the end of its data?
  bool done() const &noexcept { return m_char_finder == nullptr; }

  /// Begin iterator.  Only for use by "range for."
  inline auto begin() &;
  /// End iterator.  Only for use by "range for."
  /** The end iterator is a different type than the regular iterator.  It
   * simplifies the comparisons: we know at compile time that we're comparing
   * to the end pointer.
   */
  auto end() const & { return stream_query_end_iterator{}; }

  /// Parse and convert the latest line of data we received.
  std::tuple<TYPE...> parse_line(zview line) &
  {
    assert(not done());

    auto const line_size{std::size(line)};

    // This function uses m_row as a buffer, across calls.  The only reason for
    // it to carry over across calls is to avoid reallocation.

    // Make room for unescaping the line.  It's a pessimistic size.
    // Unusually, we're storing terminating zeroes *inside* the string.
    // This is the only place where we modify m_row.  MAKE SURE THE BUFFER DOES
    // NOT GET RESIZED while we're working, because we're working with views
    // into its buffer.
    m_row.resize(line_size + 1);

    std::size_t offset{0u};
    char *write{m_row.data()};

    // DO NOT shrink m_row to fit.  We're carrying views pointing into the
    // buffer.  (Also, how useful would shrinking really be?)

    // Folding expression: scan and unescape each field, and convert it to its
    // requested type.
    std::tuple<TYPE...> data{parse_field<TYPE>(line, offset, write)...};

    assert(offset == line_size + 1u);
    return data;
  }

  /// Read a COPY line from the server.
  std::pair<line_handle, std::size_t> read_line() &;

private:
  /// Look up a char_finder_func.
  /** This is the only encoding-dependent code in the class.  All we need to
   * store after that is this function pointer.
   */
  static inline char_finder_func *get_finder(transaction_base const &tx);

  /// Scan and unescape a field into the row buffer.
  /** The row buffer is `m_row`.
   *
   * @param line The line of COPY output.
   * @param offset The current scanning position inside `line`.
   * @param write The current writing position in the row buffer.
   *
   * @return new `offset`; new `write`; and a zview on the unescaped
   * field text in the row buffer.
   *
   * The zview's data pointer will be nullptr for a null field.
   *
   * After reading the final field in a row, if all goes well, offset should be
   * one greater than the size of the line, pointing at the terminating zero.
   */
  std::tuple<std::size_t, char *, zview>
  read_field(zview line, std::size_t offset, char *write)
  {
#if !defined(NDEBUG)
    auto const line_size{std::size(line)};
#endif

    assert(offset < line_size);

    char const *lp{std::data(line)};

    // The COPY line now ends in a tab.  (We replace the trailing newline with
    // that to simplify the loop here.)
    assert(lp[line_size] == '\t');
    assert(lp[line_size + 1] == '\0');

    if ((lp[offset] == '\\') and (lp[offset + 1] == 'N'))
    {
      // Null field.
      assert(lp[offset + 2] == '\t');
      // Consume the "\N" and the field separator.
      offset += 3;
      // Return a null value.  There's nothing to write into m_row.
      return {offset, write, {}};
    }

    // Beginning of the field text in the row buffer.
    char const *const field_begin{write};

    // We're relying on several assumptions just for making the main loop
    // condition work:
    // * The COPY line ends in a newline.
    // * Multibyte characters never start with an ASCII-range byte.
    // * We can index a view beyond its bounds (but within its address space).
    //
    // Effectively, the newline acts as a final field separator.
    while (lp[offset] != '\t')
    {
      assert(lp[offset] != '\0');

      // Beginning of the next character of interest (or the end of the line).
      auto const stop_char{m_char_finder(line, offset)};
      assert(stop_char < (line_size + 1));

      // Copy the text we have so far.  It's got no special characters in it.
      std::memcpy(write, &lp[offset], stop_char - offset);
      write += (stop_char - offset);
      offset = stop_char;

      // We're still within the line.
      char const special{lp[offset]};
      if (special == '\\')
      {
        // Escape sequence.
        // Consume the backslash.
        ++offset;
        assert(offset < line_size);

        // The database will only escape ASCII characters, so we assume that
        // we're dealing with a single-byte character.
        char const escaped{lp[offset]};
        assert((escaped >> 7) == 0);
        ++offset;
        *write++ = unescape_char(escaped);
      }
      else
      {
        // Field separator.  Fall out of the loop.
        assert(special == '\t');
      }
    }

    // Hit the end of the field.
    assert(lp[offset] == '\t');
    *write = '\0';
    ++write;
    ++offset;
    return {offset, write, {field_begin, write - field_begin - 1}};
  }

  /// Parse the next field.
  /** Unescapes the field into the row buffer (m_row), and converts it to its
   * TARGET type.
   *
   * Using non-const reference parameters here, so we can propagate side
   * effects across a fold expression.
   *
   * @param line The latest COPY line.
   * @param offset The current parsing offset in `line`.  The function will
   *   update this value.
   * @param write The current writing position in the row buffer.  The
   *   function will update this value.
   * @return Field value converted to TARGET type.
   */
  template<typename TARGET>
  TARGET parse_field(zview line, std::size_t &offset, char *&write)
  {
    using field_type = strip_t<TARGET>;
    using nullity = nullness<field_type>;

    assert(offset < std::size(line));

    auto [new_offset, new_write, text]{read_field(line, offset, write)};
    offset = new_offset;
    write = new_write;
    if constexpr (nullity::always_null)
    {
      if (std::data(text) != nullptr)
        throw conversion_error{concat(
          "Streaming a non-null value into a ", type_name<field_type>,
          ", which must always be null.")};
    }
    else if (std::data(text) == nullptr)
    {
      if constexpr (nullity::has_null)
        return nullity::null();
      else
        internal::throw_null_conversion(type_name<field_type>);
    }
    else
    {
      // Don't ever try to convert a non-null value to nullptr_t!
      return from_string<field_type>(text);
    }
  }

  /// If this stream isn't already closed, close it now.
  void close() noexcept
  {
    if (not done())
    {
      m_char_finder = nullptr;
      unregister_me();
    }
  }

  /// Callback for finding next special character (or end of line).
  /** This pointer doubles as an indication that we're done.  We set it to
   * nullptr when the iteration is finished, and that's how we can know that
   * there are no more rows to be iterated.
   */
  char_finder_func *m_char_finder;

  /// Current row's fields' text, combined into one reusable string.
  /** We carry this buffer over from one invocation to the next, not because we
   * need the data, but just so we can re-use the space.  It saves us having to
   * re-allocate it every time.
   */
  std::string m_row;
};
} // namespace pqxx::internal
#endif
