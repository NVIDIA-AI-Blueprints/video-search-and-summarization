/* Definition of libpqxx exception classes.
 *
 * pqxx::sql_error, pqxx::broken_connection, pqxx::in_doubt_error, ...
 *
 * DO NOT INCLUDE THIS FILE DIRECTLY; include pqxx/except instead.
 *
 * Copyright (c) 2000-2023, Jeroen T. Vermeulen.
 *
 * See COPYING for copyright license.  If you did not receive a file called
 * COPYING with this source code, please notify the distributor of this
 * mistake, or contact the author.
 */
#ifndef PQXX_H_EXCEPT
#define PQXX_H_EXCEPT

#if !defined(PQXX_HEADER_PRE)
#  error "Include libpqxx headers as <pqxx/header>, not <pqxx/header.hxx>."
#endif

#if pqxx_have_source_location
#  include <source_location>
#endif

#include <stdexcept>
#include <string>


namespace pqxx
{
/**
 * @addtogroup exception Exception classes
 *
 * These exception classes follow, roughly, the two-level hierarchy defined by
 * the PostgreSQL SQLSTATE error codes (see Appendix A of the PostgreSQL
 * documentation corresponding to your server version).  This is not a complete
 * mapping though.  There are other differences as well, e.g. the error code
 * for `statement_completion_unknown` has a separate status in libpqxx as
 * @ref in_doubt_error, and `too_many_connections` is classified as a
 * `broken_connection` rather than a subtype of `insufficient_resources`.
 *
 * @see http://www.postgresql.org/docs/9.4/interactive/errcodes-appendix.html
 *
 * @{
 */

/// Run-time failure encountered by libpqxx, similar to std::runtime_error.
struct PQXX_LIBEXPORT failure : std::runtime_error
{
#if pqxx_have_source_location
  explicit failure(
    std::string const &,
    std::source_location = std::source_location::current());
  std::source_location location;
#else
  explicit failure(std::string const &);
#endif
};


/// Exception class for lost or failed backend connection.
/**
 * @warning When this happens on Unix-like systems, you may also get a SIGPIPE
 * signal.  That signal aborts the program by default, so if you wish to be
 * able to continue after a connection breaks, be sure to disarm this signal.
 *
 * If you're working on a Unix-like system, see the manual page for
 * `signal` (2) on how to deal with SIGPIPE.  The easiest way to make this
 * signal harmless is to make your program ignore it:
 *
 * ```cxx
 * #include <csignal>
 *
 * int main()
 * {
 *   std::signal(SIGPIPE, SIG_IGN);
 *   // ...
 * }
 * ```
 */
struct PQXX_LIBEXPORT broken_connection : failure
{
  broken_connection();
  explicit broken_connection(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// Exception class for micommunication with the server.
/** This happens when the conversation between libpq and the server gets messed
 * up.  There aren't many situations where this happens, but one known instance
 * is when you call a parameterised or prepared statement with th ewrong number
 * of parameters.
 *
 * So even though this is a `broken_connection`, it signals that retrying is
 * _not_ likely to make the problem go away.
 */
struct PQXX_LIBEXPORT protocol_violation : broken_connection
{
  explicit protocol_violation(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// The caller attempted to set a variable to null, which is not allowed.
struct PQXX_LIBEXPORT variable_set_to_null : failure
{
  explicit variable_set_to_null(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// Exception class for failed queries.
/** Carries, in addition to a regular error message, a copy of the failed query
 * and (if available) the SQLSTATE value accompanying the error.
 */
class PQXX_LIBEXPORT sql_error : public failure
{
  /// Query string.  Empty if unknown.
  std::string const m_query;
  /// SQLSTATE string describing the error type, if known; or empty string.
  std::string const m_sqlstate;

public:
  explicit sql_error(
    std::string const &whatarg = "", std::string const &Q = "",
    char const sqlstate[] = nullptr
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
  virtual ~sql_error() noexcept override;

  /// The query whose execution triggered the exception
  [[nodiscard]] PQXX_PURE std::string const &query() const noexcept;

  /// SQLSTATE error code if known, or empty string otherwise.
  [[nodiscard]] PQXX_PURE std::string const &sqlstate() const noexcept;
};


/// "Help, I don't know whether transaction was committed successfully!"
/** Exception that might be thrown in rare cases where the connection to the
 * database is lost while finishing a database transaction, and there's no way
 * of telling whether it was actually executed by the backend.  In this case
 * the database is left in an indeterminate (but consistent) state, and only
 * manual inspection will tell which is the case.
 */
struct PQXX_LIBEXPORT in_doubt_error : failure
{
  explicit in_doubt_error(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// The backend saw itself forced to roll back the ongoing transaction.
struct PQXX_LIBEXPORT transaction_rollback : sql_error
{
  explicit transaction_rollback(
    std::string const &whatarg, std::string const &q = "",
    char const sqlstate[] = nullptr
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// Transaction failed to serialize.  Please retry it.
/** Can only happen at transaction isolation levels REPEATABLE READ and
 * SERIALIZABLE.
 *
 * The current transaction cannot be committed without violating the guarantees
 * made by its isolation level.  This is the effect of a conflict with another
 * ongoing transaction.  The transaction may still succeed if you try to
 * perform it again.
 */
struct PQXX_LIBEXPORT serialization_failure : transaction_rollback
{
  explicit serialization_failure(
    std::string const &whatarg, std::string const &q,
    char const sqlstate[] = nullptr
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// We can't tell whether our last statement succeeded.
struct PQXX_LIBEXPORT statement_completion_unknown : transaction_rollback
{
  explicit statement_completion_unknown(
    std::string const &whatarg, std::string const &q,
    char const sqlstate[] = nullptr
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// The ongoing transaction has deadlocked.  Retrying it may help.
struct PQXX_LIBEXPORT deadlock_detected : transaction_rollback
{
  explicit deadlock_detected(
    std::string const &whatarg, std::string const &q,
    char const sqlstate[] = nullptr
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// Internal error in libpqxx library
struct PQXX_LIBEXPORT internal_error : std::logic_error
{
  explicit internal_error(std::string const &);
};


/// Error in usage of libpqxx library, similar to std::logic_error
struct PQXX_LIBEXPORT usage_error : std::logic_error
{
  explicit usage_error(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );

#if pqxx_have_source_location
  std::source_location location;
#endif
};


/// Invalid argument passed to libpqxx, similar to std::invalid_argument
struct PQXX_LIBEXPORT argument_error : std::invalid_argument
{
  explicit argument_error(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );

#if pqxx_have_source_location
  std::source_location location;
#endif
};


/// Value conversion failed, e.g. when converting "Hello" to int.
struct PQXX_LIBEXPORT conversion_error : std::domain_error
{
  explicit conversion_error(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );

#if pqxx_have_source_location
  std::source_location location;
#endif
};


/// Could not convert null value: target type does not support null.
struct PQXX_LIBEXPORT unexpected_null : conversion_error
{
  explicit unexpected_null(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// Could not convert value to string: not enough buffer space.
struct PQXX_LIBEXPORT conversion_overrun : conversion_error
{
  explicit conversion_overrun(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );
};


/// Something is out of range, similar to std::out_of_range
struct PQXX_LIBEXPORT range_error : std::out_of_range
{
  explicit range_error(
    std::string const &
#if pqxx_have_source_location
    ,
    std::source_location = std::source_location::current()
#endif
  );

#if pqxx_have_source_location
  std::source_location location;
#endif
};


/// Query returned an unexpected number of rows.
struct PQXX_LIBEXPORT unexpected_rows : public range_error
{
#if pqxx_have_source_location
  explicit unexpected_rows(
    std::string const &msg,
    std::source_location loc = std::source_location::current()) :
          range_error{msg, loc}
  {}
#else
  explicit unexpected_rows(std::string const &msg) : range_error{msg} {}
#endif
};


/// Database feature not supported in current setup.
struct PQXX_LIBEXPORT feature_not_supported : sql_error
{
#if pqxx_have_source_location
  explicit feature_not_supported(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit feature_not_supported(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

/// Error in data provided to SQL statement.
struct PQXX_LIBEXPORT data_exception : sql_error
{
#if pqxx_have_source_location
  explicit data_exception(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit data_exception(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT integrity_constraint_violation : sql_error
{
#if pqxx_have_source_location
  explicit integrity_constraint_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit integrity_constraint_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT restrict_violation : integrity_constraint_violation
{
#if pqxx_have_source_location
  explicit restrict_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          integrity_constraint_violation{err, Q, sqlstate, loc}
  {}
#else
  explicit restrict_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          integrity_constraint_violation{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT not_null_violation : integrity_constraint_violation
{
#if pqxx_have_source_location
  explicit not_null_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          integrity_constraint_violation{err, Q, sqlstate, loc}
  {}
#else
  explicit not_null_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          integrity_constraint_violation{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT foreign_key_violation : integrity_constraint_violation
{
#if pqxx_have_source_location
  explicit foreign_key_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          integrity_constraint_violation{err, Q, sqlstate, loc}
  {}
#else
  explicit foreign_key_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          integrity_constraint_violation{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT unique_violation : integrity_constraint_violation
{
#if pqxx_have_source_location
  explicit unique_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          integrity_constraint_violation{err, Q, sqlstate, loc}
  {}
#else
  explicit unique_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          integrity_constraint_violation{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT check_violation : integrity_constraint_violation
{
#if pqxx_have_source_location
  explicit check_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          integrity_constraint_violation{err, Q, sqlstate, loc}
  {}
#else
  explicit check_violation(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          integrity_constraint_violation{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT invalid_cursor_state : sql_error
{
#if pqxx_have_source_location
  explicit invalid_cursor_state(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit invalid_cursor_state(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT invalid_sql_statement_name : sql_error
{
#if pqxx_have_source_location
  explicit invalid_sql_statement_name(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit invalid_sql_statement_name(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT invalid_cursor_name : sql_error
{
#if pqxx_have_source_location
  explicit invalid_cursor_name(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit invalid_cursor_name(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT syntax_error : sql_error
{
  /// Approximate position in string where error occurred, or -1 if unknown.
  int const error_position;

#if pqxx_have_source_location
  explicit syntax_error(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr, int pos = -1,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}, error_position{pos}
  {}
#else
  explicit syntax_error(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr, int pos = -1) :
          sql_error{err, Q, sqlstate}, error_position{pos}
  {}
#endif
};

struct PQXX_LIBEXPORT undefined_column : syntax_error
{
#if pqxx_have_source_location
  explicit undefined_column(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          // TODO: Can we get the column?
          syntax_error{err, Q, sqlstate, -1, loc}
  {}
#else
  explicit undefined_column(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          syntax_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT undefined_function : syntax_error
{
#if pqxx_have_source_location
  explicit undefined_function(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          // TODO: Can we get the column?
          syntax_error{err, Q, sqlstate, -1, loc}
  {}
#else
  explicit undefined_function(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          syntax_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT undefined_table : syntax_error
{
#if pqxx_have_source_location
  explicit undefined_table(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          // TODO: Can we get the column?
          syntax_error{err, Q, sqlstate, -1, loc}
  {}
#else
  explicit undefined_table(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          syntax_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT insufficient_privilege : sql_error
{
#if pqxx_have_source_location
  explicit insufficient_privilege(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit insufficient_privilege(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

/// Resource shortage on the server
struct PQXX_LIBEXPORT insufficient_resources : sql_error
{
#if pqxx_have_source_location
  explicit insufficient_resources(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit insufficient_resources(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT disk_full : insufficient_resources
{
#if pqxx_have_source_location
  explicit disk_full(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          insufficient_resources{err, Q, sqlstate, loc}
  {}
#else
  explicit disk_full(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          insufficient_resources{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT out_of_memory : insufficient_resources
{
#if pqxx_have_source_location
  explicit out_of_memory(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          insufficient_resources{err, Q, sqlstate, loc}
  {}
#else
  explicit out_of_memory(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          insufficient_resources{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT too_many_connections : broken_connection
{
#if pqxx_have_source_location
  explicit too_many_connections(
    std::string const &err,
    std::source_location loc = std::source_location::current()) :
          broken_connection{err, loc}
  {}
#else
  explicit too_many_connections(std::string const &err) :
          broken_connection{err}
  {}
#endif
};

/// PL/pgSQL error
/** Exceptions derived from this class are errors from PL/pgSQL procedures.
 */
struct PQXX_LIBEXPORT plpgsql_error : sql_error
{
#if pqxx_have_source_location
  explicit plpgsql_error(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          sql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit plpgsql_error(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          sql_error{err, Q, sqlstate}
  {}
#endif
};

/// Exception raised in PL/pgSQL procedure
struct PQXX_LIBEXPORT plpgsql_raise : plpgsql_error
{
#if pqxx_have_source_location
  explicit plpgsql_raise(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          plpgsql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit plpgsql_raise(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          plpgsql_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT plpgsql_no_data_found : plpgsql_error
{
#if pqxx_have_source_location
  explicit plpgsql_no_data_found(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          plpgsql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit plpgsql_no_data_found(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          plpgsql_error{err, Q, sqlstate}
  {}
#endif
};

struct PQXX_LIBEXPORT plpgsql_too_many_rows : plpgsql_error
{
#if pqxx_have_source_location
  explicit plpgsql_too_many_rows(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr,
    std::source_location loc = std::source_location::current()) :
          plpgsql_error{err, Q, sqlstate, loc}
  {}
#else
  explicit plpgsql_too_many_rows(
    std::string const &err, std::string const &Q = "",
    char const sqlstate[] = nullptr) :
          plpgsql_error{err, Q, sqlstate}
  {}
#endif
};

/**
 * @}
 */
} // namespace pqxx
#endif
