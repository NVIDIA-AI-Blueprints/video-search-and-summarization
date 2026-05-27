/** Transaction focus: types which monopolise a transaction's attention.
 *
 * Copyright (c) 2000-2023, Jeroen T. Vermeulen.
 *
 * See COPYING for copyright license.  If you did not receive a file called
 * COPYING with this source code, please notify the distributor of this
 * mistake, or contact the author.
 */
#ifndef PQXX_H_TRANSACTION_FOCUS
#define PQXX_H_TRANSACTION_FOCUS

#if !defined(PQXX_HEADER_PRE)
#  error "Include libpqxx headers as <pqxx/header>, not <pqxx/header.hxx>."
#endif

#include "pqxx/util.hxx"

namespace pqxx
{
/// Base class for things that monopolise a transaction's attention.
/** You probably won't need to use this class.  But it can be useful to _know_
 * that a given libpqxx class is derived from it.
 *
 * Pipelines, SQL statements, and data streams are examples of classes derived
 * from `transaction_focus`.  In any given transaction, only one object of
 * such a class can be active at any given time.
 */
class PQXX_LIBEXPORT transaction_focus
{
public:
  transaction_focus(
    transaction_base &t, std::string_view cname, std::string_view oname) :
          m_trans{&t}, m_classname{cname}, m_name{oname}
  {}

  transaction_focus(
    transaction_base &t, std::string_view cname, std::string &&oname) :
          m_trans{&t}, m_classname{cname}, m_name{std::move(oname)}
  {}

  transaction_focus(transaction_base &t, std::string_view cname) :
          m_trans{&t}, m_classname{cname}
  {}

  transaction_focus() = delete;
  transaction_focus(transaction_focus const &) = delete;
  transaction_focus &operator=(transaction_focus const &) = delete;

  /// Class name, for human consumption.
  [[nodiscard]] constexpr std::string_view classname() const noexcept
  {
    return m_classname;
  }

  /// Name for this object, if the caller passed one; empty string otherwise.
  [[nodiscard]] std::string_view name() const &noexcept { return m_name; }

  [[nodiscard]] std::string description() const
  {
    return pqxx::internal::describe_object(m_classname, m_name);
  }

  transaction_focus(transaction_focus &&other) :
          m_trans{other.m_trans},
          m_registered{other.m_registered},
          m_classname{other.m_classname},
          // We can't move the name until later.
          m_name{}
  {
    // This is a bit more complicated than you might expect.  The transaction
    // has a backpointer to the focus, and we need to transfer that to the new
    // focus.
    move_name_and_registration(other);
  }

  transaction_focus &operator=(transaction_focus &&other)
  {
    if (&other != this)
    {
      if (m_registered)
        unregister_me();
      m_trans = other.m_trans;
      m_classname = other.m_classname;
      move_name_and_registration(other);
    }
    return *this;
  }

protected:
  void register_me();
  void unregister_me() noexcept;
  void reg_pending_error(std::string const &) noexcept;
  bool registered() const noexcept { return m_registered; }

  transaction_base *m_trans;

private:
  bool m_registered = false;
  std::string_view m_classname;
  std::string m_name;

  /// Perform part of a move operation.
  void move_name_and_registration(transaction_focus &other)
  {
    bool const reg{other.m_registered};
    // Unregister the original while it still owns its name.
    if (reg)
      other.unregister_me();
    // Now!  Quick!  Steal that name.
    m_name = std::move(other.m_name);
    // Now that we own the name, register ourselves instead.
    if (reg)
      this->register_me();
  }
};
} // namespace pqxx
#endif
