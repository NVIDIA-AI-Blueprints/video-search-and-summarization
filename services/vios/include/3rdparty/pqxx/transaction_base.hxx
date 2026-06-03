/* Common code and definitions for the transaction classes.
 *
 * pqxx::transaction_base defines the interface for any abstract class that
 * represents a database transaction.
 *
 * DO NOT INCLUDE THIS FILE DIRECTLY; include pqxx/transaction_base instead.
 *
 * Copyright (c) 2000-2023, Jeroen T. Vermeulen.
 *
 * See COPYING for copyright license.  If you did not receive a file called
 * COPYING with this source code, please notify the distributor of this
 * mistake, or contact the author.
 */
#ifndef PQXX_H_TRANSACTION_BASE
#define PQXX_H_TRANSACTION_BASE

#if !defined(PQXX_HEADER_PRE)
#  error "Include libpqxx headers as <pqxx/header>, not <pqxx/header.hxx>."
#endif

#include <string_view>

/* End-user programs need not include this file, unless they define their own
 * transaction classes.  This is not something the typical program should want
 * to do.
 *
 * However, reading this file is worthwhile because it defines the public
 * interface for the available transaction classes such as transaction and
 * nontransaction.
 */

#include "pqxx/connection.hxx"
#include "pqxx/internal/concat.hxx"
#include "pqxx/internal/encoding_group.hxx"
#include "pqxx/internal/stream_query.hxx"
#include "pqxx/isolation.hxx"
#include "pqxx/result.hxx"
#include "pqxx/row.hxx"
#include "pqxx/util.hxx"

namespace pqxx::internal::gate
{
class transaction_subtransaction;
class transaction_sql_cursor;
class transaction_stream_to;
class transaction_transaction_focus;
} // namespace pqxx::internal::gate


namespace pqxx
{
using namespace std::literals;


class transaction_focus;


/**
 * @defgroup transactions Transaction classes
 *
 * All database access goes through instances of these classes.
 * However, not all implementations of this interface need to provide full
 * transactional integrity.
 *
 * You'll find several implementations of this interface in libpqxx, including
 * the plain @ref transaction class, the entirely unprotected
 * @ref nontransaction, and the more cautious @ref robusttransaction.
 *
 * Like most other things in libpqxx, transactions follow RAII principles:
 * creating a transaction object means to start the transaction, and to destroy
 * it means to end the transaction.  But there's one extra step: if you want to
 * make the transaction's changes permanent, you need to _commit_ it at the
 * end.  If you destroy the transaction object without committing it, or if you
 * call its `abort()` member function, then the transaction will roll back its
 * effects instead.
 *
 * The big exception to all this is @ref nontransaction.  It gives you the
 * transaction API but without actually starting a transaction on the database.
 */

/// Interface definition (and common code) for "transaction" classes.
/**
 * @ingroup transactions
 *
 * Abstract base class for all transaction types.
 */
class PQXX_LIBEXPORT PQXX_NOVTABLE transaction_base
{
public:
  transaction_base() = delete;
  transaction_base(transaction_base const &) = delete;
  transaction_base(transaction_base &&) = delete;
  transaction_base &operator=(transaction_base const &) = delete;
  transaction_base &operator=(transaction_base &&) = delete;

  virtual ~transaction_base() = 0;

  /// Commit the transaction.
  /** Make the effects of this transaction definite.  If you destroy a
   * transaction without invoking its @ref commit() first, that will implicitly
   * abort it.  (For the @ref nontransaction class though, "commit" and "abort"
   * really don't do anything, hence its name.)
   *
   * There is, however, a minute risk that you might lose your connection to
   * the database at just the wrong moment here.  In that case, libpqxx may be
   * unable to determine whether the database was able to complete the
   * transaction, or had to roll it back.  In that scenario, @ref commit() will
   * throw an in_doubt_error.  There is a different transaction class called
   * @ref robusttransaction which takes some special precautions to reduce this
   * risk.
   */
  void commit();

  /// Abort the transaction.
  /** No special effort is required to call this function; it will be called
   * implicitly when the transaction is destructed.
   */
  void abort();

  /**
   * @ingroup escaping-functions
   *
   * Use these when writing SQL queries that incorporate C++ values as SQL
   * constants.
   *
   * The functions you see here are just convenience shortcuts to the same
   * functions on the connection object.
   */
  //@{
  /// Escape string for use as SQL string literal in this transaction.
  template<typename... ARGS> [[nodiscard]] auto esc(ARGS &&...args) const
  {
    return conn().esc(std::forward<ARGS>(args)...);
  }

  /// Escape binary data for use as SQL string literal in this transaction.
  /** Raw, binary data is treated differently from regular strings.  Binary
   * strings are never interpreted as text, so they may safely include byte
   * values or byte sequences that don't happen to represent valid characters
   * in the character encoding being used.
   *
   * The binary string does not stop at the first zero byte, as is the case
   * with textual strings.  Instead, it may contain zero bytes anywhere.  If
   * it happens to contain bytes that look like quote characters, or other
   * things that can disrupt their use in SQL queries, they will be replaced
   * with special escape sequences.
   */
  template<typename... ARGS> [[nodiscard]] auto esc_raw(ARGS &&...args) const
  {
    return conn().esc_raw(std::forward<ARGS>(args)...);
  }

  /// Unescape binary data, e.g. from a table field or notification payload.
  /** Takes a binary string as escaped by PostgreSQL, and returns a restored
   * copy of the original binary data.
   */
  [[nodiscard, deprecated("Use unesc_bin() instead.")]] std::string
  unesc_raw(zview text) const
  {
#include "pqxx/internal/ignore-deprecated-pre.hxx"
    return conn().unesc_raw(text);
#include "pqxx/internal/ignore-deprecated-post.hxx"
  }

  /// Unescape binary data, e.g. from a table field or notification payload.
  /** Takes a binary string as escaped by PostgreSQL, and returns a restored
   * copy of the original binary data.
   */
  [[nodiscard]] std::basic_string<std::byte> unesc_bin(zview text)
  {
    return conn().unesc_bin(text);
  }

  /// Unescape binary data, e.g. from a table field or notification payload.
  /** Takes a binary string as escaped by PostgreSQL, and returns a restored
   * copy of the original binary data.
   */
  [[nodiscard, deprecated("Use unesc_bin() instead.")]] std::string
  unesc_raw(char const *text) const
  {
#include "pqxx/internal/ignore-deprecated-pre.hxx"
    return conn().unesc_raw(text);
#include "pqxx/internal/ignore-deprecated-post.hxx"
  }

  /// Unescape binary data, e.g. from a table field or notification payload.
  /** Takes a binary string as escaped by PostgreSQL, and returns a restored
   * copy of the original binary data.
   */
  [[nodiscard]] std::basic_string<std::byte> unesc_bin(char const text[])
  {
    return conn().unesc_bin(text);
  }

  /// Represent object as SQL string, including quoting & escaping.
  /** Nulls are recognized and represented as SQL nulls. */
  template<typename T> [[nodiscard]] std::string quote(T const &t) const
  {
    return conn().quote(t);
  }

  [[deprecated(
    "Use std::basic_string<std::byte> instead of binarystring.")]] std::string
  quote(binarystring const &t) const
  {
    return conn().quote(t.bytes_view());
  }

  /// Binary-escape and quote a binary string for use as an SQL constant.
  [[deprecated("Use quote(std::basic_string_view<std::byte>).")]] std::string
  quote_raw(unsigned char const bin[], std::size_t len) const
  {
    return quote(binary_cast(bin, len));
  }

  /// Binary-escape and quote a binary string for use as an SQL constant.
  [[deprecated("Use quote(std::basic_string_view<std::byte>).")]] std::string
  quote_raw(zview bin) const;

#if defined(PQXX_HAVE_CONCEPTS)
  /// Binary-escape and quote a binary string for use as an SQL constant.
  /** For binary data you can also just use @ref quote(data). */
  template<binary DATA>
  [[nodiscard]] std::string quote_raw(DATA const &data) const
  {
    return conn().quote_raw(data);
  }
#endif

  /// Escape an SQL identifier for use in a query.
  [[nodiscard]] std::string quote_name(std::string_view identifier) const
  {
    return conn().quote_name(identifier);
  }

  /// Escape string for literal LIKE match.
  [[nodiscard]] std::string
  esc_like(std::string_view bin, char escape_char = '\\') const
  {
    return conn().esc_like(bin, escape_char);
  }
  //@}

  /**
   * @name Command execution
   *
   * There are many functions for executing (or "performing") a command (or
   * "query").  This is the most fundamental thing you can do in libpqxx, and
   * it always starts at a transaction class.
   *
   * Command execution can throw many types of exception, including sql_error,
   * broken_connection, and many sql_error subtypes such as
   * feature_not_supported or insufficient_privilege.  But any exception thrown
   * by the C++ standard library may also occur here.  All exceptions you will
   * see libpqxx throw are derived from std::exception.
   *
   * Most of the differences between the query execution functions are in how
   * they return the query's results.
   *
   * * The "query" functions run your query, wait for it to complete, and load
   *   all of the results into memory on the client side.  You can then access
   *   rows of result data, converted to C++ types that you request.
   * * The "stream" functions execute your query in a completely different way.
   *   Called _streaming queries,_ these don't support quite the full range of
   *   SQL queries, and they're a bit slower to start.  But they are
   *   significantly *   _faster_ for queries that return larger numbers of
   *   rows.  They don't load the entire result set, so you can start
   *   processing data as soon as the first row of data comes in from the
   *   database.  This can save you a lot of time.  Processing itself may also
   *   be faster.  And of course, it also means you don't need enough memory to
   *   hold the entire result set, just the row you're working on.
   * * The "exec" functions are a more low-level interface.  Most of them
   *   return a pqxx::result object.  This is an object that contains all
   *   information abouut the query's result: the data itself, but also the
   *   number of rows in the result, the column names, the number of rows that
   *   your query may have modified, and so on.
   *
   * Some of these functions also give you the option to specify how many rows
   * of data you expect to get: `exec0()` reports a failure if the query
   * returns any rows of data at all, `exec1()` expects a single row of data
   * (and so returns a pqxx::row rather than a pqxx::result), `exec_n()` lets
   * you specify the number of rows you expect, and so on.
   */
  //@{

  /// Execute a command.
  /**
   * @param query Query or command to execute.
   * @param desc Optional identifier for query, to help pinpoint SQL errors.
   * @return A result set describing the query's or command's result.
   */
  [[deprecated("The desc parameter is going away.")]] result
  exec(std::string_view query, std::string_view desc);

  /// Execute a command.
  /**
   * @param query Query or command to execute.
   * @return A result set describing the query's or command's result.
   */
  result exec(std::string_view query)
  {
#include "pqxx/internal/ignore-deprecated-pre.hxx"
    return exec(query, std::string_view{});
#include "pqxx/internal/ignore-deprecated-post.hxx"
  }

  /// Execute a command.
  /**
   * @param query Query or command to execute.
   * @param desc Optional identifier for query, to help pinpoint SQL errors.
   * @return A result set describing the query's or command's result.
   */
  [[deprecated(
    "Pass your query as a std::string_view, not stringstream.")]] result
  exec(std::stringstream const &query, std::string_view desc)
  {
#include "pqxx/internal/ignore-deprecated-pre.hxx"
    return exec(query.str(), desc);
#include "pqxx/internal/ignore-deprecated-post.hxx"
  }

  /// Execute command, which should return zero rows of data.
  /** Works like @ref exec, but fails if the result contains data.  It still
   * returns a result, however, which may contain useful metadata.
   *
   * @throw unexpected_rows If the query returned the wrong number of rows.
   */
  [[deprecated("The desc parameter is going away.")]] result
  exec0(zview query, std::string_view desc)
  {
#include "pqxx/internal/ignore-deprecated-pre.hxx"
    return exec_n(0, query, desc);
#include "pqxx/internal/ignore-deprecated-post.hxx"
  }

  /// Execute command, which should return zero rows of data.
  /** Works like @ref exec, but fails if the result contains data.  It still
   * returns a result, however, which may contain useful metadata.
   *
   * @throw unexpected_rows If the query returned the wrong number of rows.
   */
  result exec0(zview query) { return exec_n(0, query); }

  /// Execute command returning a single row of data.
  /** Works like @ref exec, but requires the result to contain exactly one row.
   * The row can be addressed directly, without the need to find the first row
   * in a result set.
   *
   * @throw unexpected_rows If the query returned the wrong number of rows.
   */
  [[deprecated("The desc parameter is going away.")]] row
  exec1(zview query, std::string_view desc)
  {
#include "pqxx/internal/ignore-deprecated-pre.hxx"
    return exec_n(1, query, desc).front();
#include "pqxx/internal/ignore-deprecated-post.hxx"
  }

  /// Execute command returning a single row of data.
  /** Works like @ref exec, but requires the result to contain exactly one row.
   * The row can be addressed directly, without the need to find the first row
   * in a result set.
   *
   * @throw unexpected_rows If the query returned the wrong number of rows.
   */
  row exec1(zview query) { return exec_n(1, query).front(); }

  /// Execute command, expect given number of rows.
  /** Works like @ref exec, but checks that the result has exactly the expected
   * number of rows.
   *
   * @throw unexpected_rows If the query returned the wrong number of rows.
   */
  [[deprecated("The desc parameter is going away.")]] result
  exec_n(result::size_type rows, zview query, std::string_view desc);

  /// Execute command, expect given number of rows.
  /** Works like @ref exec, but checks that the result has exactly the expected
   * number of rows.
   *
   * @throw unexpected_rows If the query returned the wrong number of rows.
   */
  result exec_n(result::size_type rows, zview query)
  {
#include "pqxx/internal/ignore-deprecated-pre.hxx"
    return exec_n(rows, query, std::string_view{});
#include "pqxx/internal/ignore-deprecated-post.hxx"
  }

  /// Perform query, expecting exactly 1 row with 1 field, and convert it.
  /** This is convenience shorthand for querying exactly one value from the
   * database.  It returns that value, converted to the type you specify.
   */
  template<typename TYPE>
  [[deprecated("The desc parameter is going away.")]] TYPE
  query_value(zview query, std::string_view desc)
  {
#include "pqxx/internal/ignore-deprecated-pre.hxx"
    row const r{exec1(query, desc)};
#include "pqxx/internal/ignore-deprecated-post.hxx"
    if (std::size(r) != 1)
      throw usage_error{internal::concat(
        "Queried single value from result with ", std::size(r), " columns.")};
    return r[0].as<TYPE>();
  }

  /// Perform query, expecting exactly 1 row with 1 field, and convert it.
  /** This is convenience shorthand for querying exactly one value from the
   * database.  It returns that value, converted to the type you specify.
   *
   * @throw unexpected_rows If the query did not return exactly 1 row.
   * @throw usage_error If the row did not contain exactly 1 field.
   */
  template<typename TYPE> TYPE query_value(zview query)
  {
    row const r{exec1(query)};
    if (std::size(r) != 1)
      throw usage_error{internal::concat(
        "Queried single value from result with ", std::size(r), " columns.")};
    return r[0].as<TYPE>();
  }

  /// Perform query returning exactly one row, and convert its fields.
  /** This is a convenient way of querying one row's worth of data, and
   * converting its fields to a tuple of the C++-side types you specify.
   *
   * @throw unexpected_rows If the query did not return exactly 1 row.
   * @throw usage_error If the number of columns in the result does not match
   * the number of fields in the tuple.
   */
  template<typename... TYPE>
  [[nodiscard]] std::tuple<TYPE...> query1(zview query)
  {
    return exec1(query).as<TYPE...>();
  }

  /// Query at most one row of data, and if there is one, convert it.
  /** If the query produced a row of data, this converts it to a tuple of the
   * C++ types you specify.  Otherwise, this returns no tuple.
   *
   * @throw unexpected_rows If the query returned more than 1 row.
   * @throw usage_error If the number of columns in the result does not match
   * the number of fields in the tuple.
   */
  template<typename... TYPE>
  [[nodiscard]] std::optional<std::tuple<TYPE...>> query01(zview query)
  {
    result res{exec(query)};
    auto const rows{std::size(res)};
    switch (rows)
    {
    case 0: return {};
    case 1: return {res[0].as<TYPE...>()};
    default:
      throw unexpected_rows{internal::concat(
        "Expected at most one row of data, got "sv, rows, "."sv)};
    }
  }

  /// Execute a query, and loop over the results row by row.
  /** Converts the rows to `std::tuple`, of the column types you specify.
   *
   * Use this with a range-based "for" loop.  It executes the query, and
   * directly maps the resulting rows onto a `std::tuple` of the types you
   * specify.  Unlike with the "exec" functions, processing can start before
   * all the data from the server is in.
   *
   * As a special case, tuple may contain `std::string_view` fields, but the
   * strings to which they point will only remain valid until you extract the
   * next row.  After that, the memory holding the string may be overwritten or
   * deallocated.
   *
   * If any of the columns can be null, and the C++ type to which you're
   * translating it does not have a null value, wrap the type in a
   * `std::optional<>` (or if you prefer, a `std::shared_ptr<>` or a
   * `std::unique_ptr`).  These templates do support null values, and libpqxx
   * will know how to convert to them.
   *
   * The stream lives entirely within the lifetime of the transaction.  Make
   * sure you complete the stream before you destroy the transaction.  Until
   * the stream has finished, the transaction and the connection are in a
   * special state where they cannot be used for anything else.
   *
   * @warning If the stream fails, you will have to destroy the transaction
   * and the connection.  If this is a problem, use the "exec" functions
   * instead.
   *
   * Streaming your query is likely to be faster than the `exec()` methods for
   * larger results (but slower for small results), and start useful processing
   * sooner.  Also, `stream()` scales better in terms of memory usage: it only
   * needs to keep the current row in memory.  The "exec" functions read the
   * entire result into memory at once.
   *
   * Your query executes as part of a COPY command, not as a stand-alone query,
   * so there are limitations to what you can do in the query.  It can be
   * either a SELECT or VALUES query; or an INSERT, UPDATE, or DELETE with a
   * RETURNING clause.  See the documentation for PostgreSQL's
   * [COPY command](https://www.postgresql.org/docs/current/sql-copy.html) for
   * the exact restrictions.
   *
   * Iterating in this way does require each of the field types you pass to be
   * default-constructible, copy-constructible, and assignable.  These
   * requirements may loosen a bit once libpqxx moves on to C++20.
   */
  template<typename... TYPE>
  [[nodiscard]] auto stream(std::string_view query) &
  {
    return pqxx::internal::stream_query<TYPE...>{*this, query};
  }

  // C++20: Concept like std::invocable, but without specifying param types.
  /// Perform a streaming query, and for each result row, call `func`.
  /** Here, `func` can be a function, a `std::function`, a lambda, or an
   * object that supports the function call operator.  Of course `func` must
   * have an unambiguous signature; it can't be overloaded or generic.
   *
   * The `for_stream` function executes `query` in a stream similar to
   * @ref stream.  Every time a row of data comes in from the server, it
   * converts the row's fields to the types of `func`'s respective parameters,
   * and calls `func` with those values.
   *
   * This will not work for all queries, but straightforward `SELECT` and
   * `UPDATE ... RETURNING` queries should work.  Consult the documentation for
   * @ref pqxx::internal::stream_query and PostgreSQL's underlying `COPY`
   * command for the full details.
   *
   * Streaming a query like this is likely to be slower than the @ref exec()
   * functions for small result sets, but faster for larger result sets.  So if
   * performance matters, you'll want to use `for_stream` if you query large
   * amounts of data, but not if you do lots of queries with small outputs.
   *
   * However, the transaction and the connection are in a special state while
   * the iteration is ongoing.  If `func` throws an exception, or the iteration
   * fails in some way, the only way out is to destroy the transaction and the
   * connection.
   */
  template<typename CALLABLE>
  auto for_stream(std::string_view query, CALLABLE &&func)
  {
    using param_types =
      pqxx::internal::strip_types_t<pqxx::internal::args_t<CALLABLE>>;
    param_types const *const sample{nullptr};
    auto data_stream{stream_like(query, sample)};
    for (auto const &fields : data_stream) std::apply(func, fields);
  }

  template<typename CALLABLE>
  [[deprecated(
    "pqxx::transaction_base::for_each is now called for_stream.")]] auto
  for_each(std::string_view query, CALLABLE &&func)
  {
    return for_stream(query, std::forward<CALLABLE>(func));
  }

  /// Execute query, read full results, then iterate rows of data.
  /** Converts each row of the result to a `std::tuple` of the types you pass
   * as template arguments.  (The number of template arguments must match the
   * number of columns in the query's result.)
   *
   * Example:
   *
   * ```cxx
   *     for (
   *         auto [name, salary] :
   *             tx.query<std::string_view, int>(
   *                 "SELECT name, salary FROM employee"
                 )
   *     )
   *         std::cout << name << " earns " << salary << ".\n";
   * ```
   *
   * You can't normally convert a field value to `std::string_view`, but this
   * is one of the places where you can.  The underlying string to which the
   * `string_view` points exists only for the duration of the one iteration.
   * After that, the buffer that holds the actual string may have disappeared,
   * or it may contain a new string value.
   *
   * If you expect a lot of rows from your query, it's probably faster to use
   * transaction_base::stream() instead.  Or if you need to access metadata of
   * the result, such as the number of rows in the result, or the number of
   * rows that your query updates, then you'll need to use
   * transaction_base::exec() instead.
   *
   * @return Something you can iterate using "range `for`" syntax.  The actual
   * type details may change.
   */
  template<typename... TYPE> auto query(zview query)
  {
    return exec(query).iter<TYPE...>();
  }

  /// Perform query, expect given number of rows, iterate results.
  /** Works like @ref query, but checks that the result has exactly the
   * expected number of rows.
   *
   * @throw unexpected_rows If the query returned the wrong number of rows.
   *
   * @return Something you can iterate using "range `for`" syntax.  The actual
   * type details may change.
   */
  template<typename... TYPE> auto query_n(result::size_type rows, zview query)
  {
    return exec_n(rows, query).iter<TYPE...>();
  }

  // C++20: Concept like std::invocable, but without specifying param types.
  /// Execute a query, load the full result, and perform `func` for each row.
  /** Converts each row to data types matching `func`'s parameter types.  The
   * number of columns in the result set must match the number of parameters.
   *
   * This is a lot like for_stream().  The differences are:
   * 1. It can execute some unusual queries that for_stream() can't.
   * 2. The `exec` functions are faster for small results, but slower for large
   *    results.
   */
  template<typename CALLABLE> void for_query(zview query, CALLABLE &&func)
  {
    exec(query).for_each(std::forward<CALLABLE>(func));
  }

  /**
   * @name Parameterized statements
   *
   * You'll often need parameters in the queries you execute: "select the
   * car with this licence plate."  If the parameter is a string, you need to
   * quote it and escape any special characters inside it, or it may become a
   * target for an SQL injection attack.  If it's an integer (for example),
   * you need to convert it to a string, but in the database's format, without
   * locale-specific niceties like "," separators between the thousands.
   *
   * Parameterised statements are an easier and safer way to do this.  They're
   * like prepared statements, but for a single use.  You don't need to name
   * them, and you don't need to prepare them first.
   *
   * Your query will include placeholders like `$1` and `$2` etc. in the places
   * where you want the arguments to go.  Then, you pass the argument values
   * and the actual query is constructed for you.
   *
   * Pass the exact right number of parameters, and in the right order.  The
   * parameters in the query don't have to be neatly ordered from `$1` to
   * `$2` to `$3` - but you must pass the argument for `$1` first, the one
   * for `$2` second, etc.
   *
   * @warning Beware of "nul" bytes.  Any string you pass as a parameter will
   * end at the first char with value zero.  If you pass a string that contains
   * a zero byte, the last byte in the value will be the one just before the
   * zero.
   */
  //@{
  /// Execute an SQL statement with parameters.
  template<typename... Args> result exec_params(zview query, Args &&...args)
  {
    params pp(args...);
    return internal_exec_params(query, pp.make_c_params());
  }

  // Execute parameterised statement, expect a single-row result.
  /** @throw unexpected_rows if the result does not consist of exactly one row.
   */
  template<typename... Args> row exec_params1(zview query, Args &&...args)
  {
    return exec_params_n(1, query, std::forward<Args>(args)...).front();
  }

  // Execute parameterised statement, expect a result with zero rows.
  /** @throw unexpected_rows if the result contains rows.
   */
  template<typename... Args> result exec_params0(zview query, Args &&...args)
  {
    return exec_params_n(0, query, std::forward<Args>(args)...);
  }

  // Execute parameterised statement, expect exactly a given number of rows.
  /** @throw unexpected_rows if the result contains the wrong number of rows.
   */
  template<typename... Args>
  result exec_params_n(std::size_t rows, zview query, Args &&...args)
  {
    auto const r{exec_params(query, std::forward<Args>(args)...)};
    check_rowcount_params(rows, std::size(r));
    return r;
  }
  //@}

  /**
   * @name Prepared statements
   *
   * These are very similar to parameterised statements.  The difference is
   * that you prepare them in advance, giving them identifying names.  You can
   * then call them by these names, passing in the argument values appropriate
   * for that call.
   *
   * You prepare a statement on the connection, using
   * @ref pqxx::connection::prepare().  But you then call the statement in a
   * transaction, using the functions you see here.
   *
   * Never try to prepare, execute, or unprepare a prepared statement manually
   * using direct SQL queries when you also use the libpqxx equivalents.  For
   * any given statement, either prepare, manage, and execute it through the
   * dedicated libpqxx functions; or do it all directly in SQL.  Don't mix the
   * two, or the code may get confused.
   *
   * See \ref prepared for a full discussion.
   *
   * @warning Beware of "nul" bytes.  Any string you pass as a parameter will
   * end at the first char with value zero.  If you pass a string that contains
   * a zero byte, the last byte in the value will be the one just before the
   * zero.  If you need a zero byte, you're dealing with binary strings, not
   * regular strings.  Represent binary strings on the SQL side as `BYTEA`
   * (or as large objects).  On the C++ side, use types like
   * `std::basic_string<std::byte>` or `std::basic_string_view<std::byte>`
   * or (in C++20) `std::vector<std::byte>`.  Also, consider large objects on
   * the SQL side and @ref blob on the C++ side.
   *
   * @warning Passing the wrong number of parameters to a prepared or
   * parameterised statement will _break the connection._  The usual exception
   * that occurs in this situation is @ref pqxx::protocol_violation.  It's a
   * subclass of @ref pqxx::broken_connection, but where `broken_connection`
   * usually indicates a networking problem, `protocol_violation` indicates
   * that the communication with the server has deviated from protocol.  Once
   * something like that happens, communication is broken and there is nothing
   * for it but to discard the connection.  A networking problem is usually
   * worth retrying, but a protocol violation is not.  The same violation will
   * probably just happen again.
   */
  //@{

  /// Execute a prepared statement, with optional arguments.
  template<typename... Args>
  result exec_prepared(zview statement, Args &&...args)
  {
    params pp(args...);
    return internal_exec_prepared(statement, pp.make_c_params());
  }

  /// Execute a prepared statement, and expect a single-row result.
  /** @throw pqxx::unexpected_rows if the result was not exactly 1 row.
   */
  template<typename... Args>
  row exec_prepared1(zview statement, Args &&...args)
  {
    return exec_prepared_n(1, statement, std::forward<Args>(args)...).front();
  }

  /// Execute a prepared statement, and expect a result with zero rows.
  /** @throw pqxx::unexpected_rows if the result contained rows.
   */
  template<typename... Args>
  result exec_prepared0(zview statement, Args &&...args)
  {
    return exec_prepared_n(0, statement, std::forward<Args>(args)...);
  }

  /// Execute a prepared statement, expect a result with given number of rows.
  /** @throw pqxx::unexpected_rows if the result did not contain exactly the
   *  given number of rows.
   */
  template<typename... Args>
  result
  exec_prepared_n(result::size_type rows, zview statement, Args &&...args)
  {
    auto const r{exec_prepared(statement, std::forward<Args>(args)...)};
    check_rowcount_prepared(statement, rows, std::size(r));
    return r;
  }

  //@}

  /**
   * @name Error/warning output
   */
  //@{
  /// Have connection process a warning message.
  void process_notice(char const msg[]) const { m_conn.process_notice(msg); }
  /// Have connection process a warning message.
  void process_notice(zview msg) const { m_conn.process_notice(msg); }
  //@}

  /// The connection in which this transaction lives.
  [[nodiscard]] constexpr connection &conn() const noexcept { return m_conn; }

  /// Set session variable using SQL "SET" command.
  /** @deprecated To set a transaction-local variable, execute an SQL `SET`
   * command.  To set a session variable, use the connection's
   * @ref set_session_var function.
   *
   * @warning When setting a string value, you must make sure that the string
   * is "safe."  If you call @ref quote() on the string, it will return a
   * safely escaped and quoted version for use as an SQL literal.
   *
   * @warning This function executes SQL.  Do not try to set or get variables
   * while a pipeline or table stream is active.
   *
   * @param var The variable to set.
   * @param value The new value to store in the variable.  This can be any SQL
   * expression.
   */
  [[deprecated(
    "Set transaction-local variables using SQL SET statements.")]] void
  set_variable(std::string_view var, std::string_view value);

  /// Read session variable using SQL "SHOW" command.
  /** @warning This executes SQL.  Do not try to set or get variables while a
   * pipeline or table stream is active.
   */
  [[deprecated("Read variables using SQL SHOW statements.")]] std::string
    get_variable(std::string_view);

  // C++20: constexpr.
  /// Transaction name, if you passed one to the constructor; or empty string.
  [[nodiscard]] std::string_view name() const &noexcept { return m_name; }

protected:
  /// Create a transaction (to be called by implementation classes only).
  /** The name, if nonempty, must begin with a letter and may contain letters
   * and digits only.
   */
  transaction_base(
    connection &c, std::string_view tname,
    std::shared_ptr<std::string> rollback_cmd) :
          m_conn{c}, m_name{tname}, m_rollback_cmd{rollback_cmd}
  {}

  /// Create a transaction (to be called by implementation classes only).
  /** Its rollback command will be "ROLLBACK".
   *
   * The name, if nonempty, must begin with a letter and may contain letters
   * and digits only.
   */
  transaction_base(connection &c, std::string_view tname);

  /// Create a transaction (to be called by implementation classes only).
  explicit transaction_base(connection &c);

  /// Register this transaction with the connection.
  void register_transaction();

  /// End transaction.  To be called by implementing class' destructor.
  void close() noexcept;

  /// To be implemented by derived implementation class: commit transaction.
  virtual void do_commit() = 0;

  /// Transaction type-specific way of aborting a transaction.
  /** @warning This will become "final", since this function can be called
   * from the implementing class destructor.
   */
  virtual void do_abort();

  /// Set the rollback command.
  void set_rollback_cmd(std::shared_ptr<std::string> cmd)
  {
    m_rollback_cmd = cmd;
  }

  /// Execute query on connection directly.
  result direct_exec(std::string_view, std::string_view desc = ""sv);
  result
  direct_exec(std::shared_ptr<std::string>, std::string_view desc = ""sv);

private:
  enum class status
  {
    active,
    aborted,
    committed,
    in_doubt
  };

  PQXX_PRIVATE void check_pending_error();

  result
  internal_exec_prepared(zview statement, internal::c_params const &args);

  result internal_exec_params(zview query, internal::c_params const &args);

  /// Throw unexpected_rows if prepared statement returned wrong no. of rows.
  void check_rowcount_prepared(
    zview statement, result::size_type expected_rows,
    result::size_type actual_rows);

  /// Throw unexpected_rows if wrong row count from parameterised statement.
  void
  check_rowcount_params(std::size_t expected_rows, std::size_t actual_rows);

  /// Describe this transaction to humans, e.g. "transaction 'foo'".
  [[nodiscard]] std::string description() const;

  friend class pqxx::internal::gate::transaction_transaction_focus;
  PQXX_PRIVATE void register_focus(transaction_focus *);
  PQXX_PRIVATE void unregister_focus(transaction_focus *) noexcept;
  PQXX_PRIVATE void register_pending_error(zview) noexcept;
  PQXX_PRIVATE void register_pending_error(std::string &&) noexcept;

  /// Like @ref stream(), but takes a tuple rather than a parameter pack.
  template<typename... ARGS>
  auto stream_like(std::string_view query, std::tuple<ARGS...> const *)
  {
    return stream<ARGS...>(query);
  }

  connection &m_conn;

  /// Current "focus": a pipeline, a nested transaction, a stream...
  /** This pointer is used for only one purpose: sanity checks against mistakes
   * such as opening one while another is still active.
   */
  transaction_focus const *m_focus = nullptr;

  status m_status = status::active;
  bool m_registered = false;
  std::string m_name;
  std::string m_pending_error;

  /// SQL command for aborting this type of transaction.
  std::shared_ptr<std::string> m_rollback_cmd;

  static constexpr std::string_view s_type_name{"transaction"sv};
};


// C++20: Can borrowed_range help?
/// Forbidden specialisation: underlying buffer immediately goes out of scope.
template<>
std::string_view transaction_base::query_value<std::string_view>(
  zview query, std::string_view desc) = delete;
/// Forbidden specialisation: underlying buffer immediately goes out of scope.
template<>
zview transaction_base::query_value<zview>(
  zview query, std::string_view desc) = delete;

} // namespace pqxx


namespace pqxx::internal
{
/// The SQL command for starting a given type of transaction.
template<pqxx::isolation_level isolation, pqxx::write_policy rw>
extern const zview begin_cmd;

// These are not static members, so "constexpr" does not imply "inline".
template<>
inline constexpr zview begin_cmd<read_committed, write_policy::read_write>{
  "BEGIN"_zv};
template<>
inline constexpr zview begin_cmd<read_committed, write_policy::read_only>{
  "BEGIN READ ONLY"_zv};
template<>
inline constexpr zview begin_cmd<repeatable_read, write_policy::read_write>{
  "BEGIN ISOLATION LEVEL REPEATABLE READ"_zv};
template<>
inline constexpr zview begin_cmd<repeatable_read, write_policy::read_only>{
  "BEGIN ISOLATION LEVEL REPEATABLE READ READ ONLY"_zv};
template<>
inline constexpr zview begin_cmd<serializable, write_policy::read_write>{
  "BEGIN ISOLATION LEVEL SERIALIZABLE"_zv};
template<>
inline constexpr zview begin_cmd<serializable, write_policy::read_only>{
  "BEGIN ISOLATION LEVEL SERIALIZABLE READ ONLY"_zv};
} // namespace pqxx::internal

#include "pqxx/internal/stream_query_impl.hxx"
#endif
