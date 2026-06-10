#if !defined(PQXX_ARRAY_COMPOSITE_HXX)
#  define PQXX_ARRAY_COMPOSITE_HXX

#  include <cassert>

#  include "pqxx/internal/encodings.hxx"
#  include "pqxx/strconv.hxx"

namespace pqxx::internal
{
// Find the end of a double-quoted string.
/** `input[pos]` must be the opening double quote.
 *
 * The backend double-quotes strings in composites or arrays, when needed.
 * Special characters are escaped using backslashes.
 *
 * Returns the offset of the first position after the closing quote.
 */
template<encoding_group ENC>
inline std::size_t scan_double_quoted_string(
  char const input[], std::size_t size, std::size_t pos)
{
  // TODO: find_char<'"', '\\'>().
  using scanner = glyph_scanner<ENC>;
  auto next{scanner::call(input, size, pos)};
  bool at_quote{false};
  pos = next;
  next = scanner::call(input, size, pos);
  while (pos < size)
  {
    if (at_quote)
    {
      if (next - pos == 1 and input[pos] == '"')
      {
        // We just read a pair of double quotes.  Carry on.
        at_quote = false;
      }
      else
      {
        // We just read one double quote, and now we're at a character that's
        // not a second double quote.  Ergo, that last character was the
        // closing double quote and this is the position right after it.
        return pos;
      }
    }
    else if (next - pos == 1)
    {
      switch (input[pos])
      {
      case '\\':
        // Backslash escape.  Skip ahead by one more character.
        pos = next;
        next = scanner::call(input, size, pos);
        break;

      case '"':
        // This is either the closing double quote, or the first of a pair of
        // double quotes.
        at_quote = true;
        break;
      }
    }
    else
    {
      // Multibyte character.  Carry on.
    }
    pos = next;
    next = scanner::call(input, size, pos);
  }
  if (not at_quote)
    throw argument_error{
      "Missing closing double-quote: " + std::string{input}};
  return pos;
}


// TODO: Needs version with caller-supplied buffer.
/// Un-quote and un-escape a double-quoted SQL string.
template<encoding_group ENC>
inline std::string parse_double_quoted_string(
  char const input[], std::size_t end, std::size_t pos)
{
  std::string output;
  // Maximum output size is same as the input size, minus the opening and
  // closing quotes.  Or in the extreme opposite case, the real number could be
  // half that.  Usually it'll be a pretty close estimate.
  output.reserve(std::size_t(end - pos - 2));

  // TODO: Use find_char<...>().
  using scanner = glyph_scanner<ENC>;
  auto here{scanner::call(input, end, pos)},
    next{scanner::call(input, end, here)};
  while (here < end - 1)
  {
    // A backslash here is always an escape.  So is a double-quote, since we're
    // inside the double-quoted string.  In either case, we can just ignore the
    // escape character and use the next character.  This is the one redeeming
    // feature of SQL's escaping system.
    if ((next - here == 1) and (input[here] == '\\' or input[here] == '"'))
    {
      // Skip escape.
      here = next;
      next = scanner::call(input, end, here);
    }
    output.append(input + here, input + next);
    here = next;
    next = scanner::call(input, end, here);
  }
  return output;
}


/// Find the end of an unquoted string in an array or composite-type value.
/** Stops when it gets to the end of the input; or when it sees any of the
 * characters in STOP which has not been escaped.
 *
 * For array values, STOP is an array element separator (typically comma, or
 * semicolon), or a closing brace.  For a value of a composite type, STOP is a
 * comma or a closing parenthesis.
 */
template<pqxx::internal::encoding_group ENC, char... STOP>
inline std::size_t
scan_unquoted_string(char const input[], std::size_t size, std::size_t pos)
{
  using scanner = glyph_scanner<ENC>;
  auto next{scanner::call(input, size, pos)};
  while ((pos < size) and ((next - pos) > 1 or ((input[pos] != STOP) and ...)))
  {
    pos = next;
    next = scanner::call(input, size, pos);
  }
  return pos;
}


// XXX: Retire this.  Just construct a string_view directly now!
/// Parse an unquoted array entry or cfield of a composite-type field.
template<pqxx::internal::encoding_group ENC>
inline std::string
parse_unquoted_string(char const input[], std::size_t end, std::size_t pos)
{
  return {&input[pos], end - pos};
}


/// Parse a field of a composite-type value.
/** `T` is the C++ type of the field we're parsing, and `index` is its
 * zero-based number.
 *
 * Strip off the leading parenthesis or bracket yourself before parsing.
 * However, this function will parse the lcosing parenthesis or bracket.
 *
 * After a successful parse, `pos` will point at `std::end(text)`.
 *
 * For the purposes of parsing, ranges and arrays count as compositve values,
 * so this function supports parsing those.  If you specifically need a closing
 * parenthesis, check afterwards that `text` did not end in a bracket instead.
 *
 * @param index Index of the current field, zero-based.  It will increment for
 *     the next field.
 * @param input Full input text for the entire composite-type value.
 * @param pos Starting position (in `input`) of the field that we're parsing.
 *     After parsing, this will point at the beginning of the next field if
 *     there is one, or one position past the last character otherwise.
 * @param field Destination for the parsed value.
 * @param scan Glyph scanning function for the relevant encoding type.
 * @param last_field Number of the last field in the value (zero-based).  When
 *     parsing the last field, this will equal `index`.
 */
template<encoding_group ENC, typename T>
inline void parse_composite_field(
  std::size_t &index, std::string_view input, std::size_t &pos, T &field,
  std::size_t last_field)
{
  assert(index <= last_field);
  auto next{glyph_scanner<ENC>::call(std::data(input), std::size(input), pos)};
  if ((next - pos) != 1)
    throw conversion_error{"Non-ASCII character in composite-type syntax."};

  // Expect a field.
  switch (input[pos])
  {
  case ',':
  case ')':
  case ']':
    // The field is empty, i.e, null.
    if constexpr (nullness<T>::has_null)
      field = nullness<T>::null();
    else
      throw conversion_error{
        "Can't read composite field " + to_string(index) + ": C++ type " +
        type_name<T> + " does not support nulls."};
    break;

  case '"': {
    auto const stop{
      scan_double_quoted_string<ENC>(std::data(input), std::size(input), pos)};
    auto const text{
      parse_double_quoted_string<ENC>(std::data(input), stop, pos)};
    field = from_string<T>(text);
    pos = stop;
  }
  break;

  default: {
    auto const stop{scan_unquoted_string<ENC, ',', ')', ']'>(
      std::data(input), std::size(input), pos)};
    field =
      from_string<T>(std::string_view{std::data(input) + pos, stop - pos});
    pos = stop;
  }
  break;
  }

  // Expect a comma or a closing parenthesis.
  next = glyph_scanner<ENC>::call(std::data(input), std::size(input), pos);

  if ((next - pos) != 1)
    throw conversion_error{
      "Unexpected non-ASCII character after composite field: " +
      std::string{input}};

  if (index < last_field)
  {
    if (input[pos] != ',')
      throw conversion_error{
        "Found '" + std::string{input[pos]} +
        "' in composite value where comma was expected: " + std::data(input)};
  }
  else
  {
    if (input[pos] == ',')
      throw conversion_error{
        "Composite value contained more fields than the expected " +
        to_string(last_field) + ": " + std::data(input)};
    if (input[pos] != ')' and input[pos] != ']')
      throw conversion_error{
        "Composite value has unexpected characters where closing parenthesis "
        "was expected: " +
        std::string{input}};
    if (next != std::size(input))
      throw conversion_error{
        "Composite value has unexpected text after closing parenthesis: " +
        std::string{input}};
  }

  pos = next;
  ++index;
}


/// Pointer to an encoding-specific specialisation of parse_composite_field.
template<typename T>
using composite_field_parser = void (*)(
  std::size_t &index, std::string_view input, std::size_t &pos, T &field,
  std::size_t last_field);


/// Look up implementation of parse_composite_field for ENC.
template<typename T>
composite_field_parser<T> specialize_parse_composite_field(encoding_group enc)
{
  switch (enc)
  {
  case encoding_group::MONOBYTE:
    return parse_composite_field<encoding_group::MONOBYTE>;
  case encoding_group::BIG5:
    return parse_composite_field<encoding_group::BIG5>;
  case encoding_group::EUC_CN:
    return parse_composite_field<encoding_group::EUC_CN>;
  case encoding_group::EUC_JP:
    return parse_composite_field<encoding_group::EUC_JP>;
  case encoding_group::EUC_KR:
    return parse_composite_field<encoding_group::EUC_KR>;
  case encoding_group::EUC_TW:
    return parse_composite_field<encoding_group::EUC_TW>;
  case encoding_group::GB18030:
    return parse_composite_field<encoding_group::GB18030>;
  case encoding_group::GBK: return parse_composite_field<encoding_group::GBK>;
  case encoding_group::JOHAB:
    return parse_composite_field<encoding_group::JOHAB>;
  case encoding_group::MULE_INTERNAL:
    return parse_composite_field<encoding_group::MULE_INTERNAL>;
  case encoding_group::SJIS:
    return parse_composite_field<encoding_group::SJIS>;
  case encoding_group::UHC: return parse_composite_field<encoding_group::UHC>;
  case encoding_group::UTF8:
    return parse_composite_field<encoding_group::UTF8>;
  }
  throw internal_error{concat("Unexpected encoding group code: ", enc, ".")};
}


/// Conservatively estimate buffer size needed for a composite field.
template<typename T>
inline std::size_t size_composite_field_buffer(T const &field)
{
  if constexpr (is_unquoted_safe<T>)
  {
    // Safe to copy, without quotes or escaping.  Drop the terminating zero.
    return size_buffer(field) - 1;
  }
  else
  {
    // + Opening quote.
    // + Field budget.
    // - Terminating zero.
    // + Escaping for each byte in the field's string representation.
    // - Escaping for terminating zero.
    // + Closing quote.
    return 1 + 2 * (size_buffer(field) - 1) + 1;
  }
}


template<typename T>
inline void write_composite_field(char *&pos, char *end, T const &field)
{
  if constexpr (is_unquoted_safe<T>)
  {
    // No need for quoting or escaping.  Convert it straight into its final
    // place in the buffer, and "backspace" the trailing zero.
    pos = string_traits<T>::into_buf(pos, end, field) - 1;
  }
  else
  {
    // The field may need escaping, which means we need an intermediate buffer.
    // To avoid allocating that at run time, we use the end of the buffer that
    // we have.
    auto const budget{size_buffer(field)};
    *pos++ = '"';

    // Now escape buf into its final position.
    for (char const c : string_traits<T>::to_buf(end - budget, end, field))
    {
      if ((c == '"') or (c == '\\'))
        *pos++ = '\\';

      *pos++ = c;
    }

    *pos++ = '"';
  }

  *pos++ = ',';
}
} // namespace pqxx::internal
#endif
