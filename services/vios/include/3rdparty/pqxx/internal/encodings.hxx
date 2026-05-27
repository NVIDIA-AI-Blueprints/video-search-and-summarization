/** Internal string encodings support for libpqxx
 *
 * Copyright (c) 2000-2023, Jeroen T. Vermeulen.
 *
 * See COPYING for copyright license.  If you did not receive a file called
 * COPYING with this source code, please notify the distributor of this
 * mistake, or contact the author.
 */
#ifndef PQXX_H_ENCODINGS
#define PQXX_H_ENCODINGS

#include <iomanip>
#include <string>
#include <string_view>

#include "pqxx/internal/concat.hxx"
#include "pqxx/internal/encoding_group.hxx"


namespace pqxx
{
PQXX_DECLARE_ENUM_CONVERSION(pqxx::internal::encoding_group);
} // namespace pqxx


namespace pqxx::internal
{
/// Return PostgreSQL's name for encoding enum value.
PQXX_PURE char const *name_encoding(int encoding_id);

/// Convert libpq encoding enum value to its libpqxx group.
PQXX_LIBEXPORT encoding_group enc_group(int /* libpq encoding ID */);


/// Look up the glyph scanner function for a given encoding group.
/** To identify the glyph boundaries in a buffer, call this to obtain the
 * scanner function appropriate for the buffer's encoding.  Then, repeatedly
 * call the scanner function to find the glyphs.
 */
PQXX_LIBEXPORT glyph_scanner_func *get_glyph_scanner(encoding_group);


// TODO: Get rid of this one.  Use compile-time-specialised version instead.
/// Find any of the ASCII characters `NEEDLE` in `haystack`.
/** Scans through `haystack` until it finds a single-byte character that
 * matches any value in `NEEDLE`.
 *
 * If it finds one, returns its offset.  If not, returns the end of the
 * haystack.
 */
template<char... NEEDLE>
inline std::size_t find_char(
  glyph_scanner_func *scanner, std::string_view haystack,
  std::size_t here = 0u)
{
  auto const sz{std::size(haystack)};
  auto const data{std::data(haystack)};
  while (here < sz)
  {
    auto next{scanner(data, sz, here)};
    // (For some reason gcc had a problem with a right-fold here.  But clang
    // was fine.)
    if ((... or (data[here] == NEEDLE)))
    {
      // Also check against a multibyte character starting with a bytes which
      // just happens to match one of the ASCII bytes we're looking for.  It'd
      // be cleaner to check that first, but either works.  So, let's apply the
      // most selective filter first and skip this check in almost all cases.
      if (next == here + 1)
        return here;
    }

    // Nope, no hit.  Move on.
    here = next;
  }
  return sz;
}


// TODO: Get rid of this one.  Use compile-time-specialised loop instead.
/// Iterate over the glyphs in a buffer.
/** Scans the glyphs in the buffer, and for each, passes its begin and its
 * one-past-end pointers to `callback`.
 */
template<typename CALLABLE>
inline void for_glyphs(
  encoding_group enc, CALLABLE callback, char const buffer[],
  std::size_t buffer_len, std::size_t start = 0)
{
  auto const scan{get_glyph_scanner(enc)};
  for (std::size_t here = start, next; here < buffer_len; here = next)
  {
    next = scan(buffer, buffer_len, here);
    callback(buffer + here, buffer + next);
  }
}


namespace
{
/// Extract byte from buffer, return as unsigned char.
constexpr PQXX_PURE unsigned char
get_byte(char const buffer[], std::size_t offset) noexcept
{
  return static_cast<unsigned char>(buffer[offset]);
}


[[noreturn]] PQXX_COLD void throw_for_encoding_error(
  char const *encoding_name, char const buffer[], std::size_t start,
  std::size_t count)
{
  std::stringstream s;
  s << "Invalid byte sequence for encoding " << encoding_name << " at byte "
    << start << ": " << std::hex << std::setw(2) << std::setfill('0');
  for (std::size_t i{0}; i < count; ++i)
  {
    s << "0x" << static_cast<unsigned int>(get_byte(buffer, start + i));
    if (i + 1 < count)
      s << " ";
  }
  throw pqxx::argument_error{s.str()};
}


/// Does value lie between bottom and top, inclusive?
constexpr PQXX_PURE bool
between_inc(unsigned char value, unsigned bottom, unsigned top)
{
  return value >= bottom and value <= top;
}
} // namespace


/// Wrapper struct template for "find next glyph" functions.
/** When we use this, all we really want is a function pointer.  But that
 * won't work, because the template specialisation we use will only work (under
 * current C++ rules) for a struct or class, not for a function.
 */
template<encoding_group> struct glyph_scanner
{
  // TODO: Convert to use string_view?
  /// Find the next glyph in `buffer` after position `start`.
  PQXX_PURE static std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start);
};


namespace
{
/// Find any of the ASCII characters in `NEEDLE` in `haystack`.
/** Scans through `haystack` until it finds a single-byte character that
 * matches any of the values in `NEEDLE`.
 *
 * Returns the offset of the character it finds, or the end of the `haystack`
 * otherwise.
 */
template<encoding_group ENC, char... NEEDLE>
PQXX_PURE inline std::size_t
find_ascii_char(std::string_view haystack, std::size_t here)
{
  // We only know how to search for ASCII characters.  It's an optimisation
  // assumption in the code below.
  static_assert((... and ((NEEDLE & 0x80) == 0)));

  auto const sz{std::size(haystack)};
  auto const data{std::data(haystack)};
  while (here < sz)
  {
    // Look up the next character boundary.  This can be quite costly, so we
    // desperately want the call inlined.
    auto next{glyph_scanner<ENC>::call(data, sz, here)};

    // (For some reason gcc had a problem with a right-fold here.  But clang
    // was fine.)
    //
    // In all supported encodings, if a character's first byte is in the ASCII
    // range, that means it's a single-byte character.  It follows that when we
    // find a match, we do not need to check that we're in a single-byte
    // character:
    //
    // If this is an "ASCII-unsafe" encoding, e.g. SJIS, we're only checking
    // each character's first byte.  That first byte can only match NEEDLE if
    // it's a single-byte character.
    //
    // In an "ASCII-safe" encoding, e.g. UTF-8 or the ISO-8859 ones, we check
    // for a match at each byte in the text, because it's faster than finding
    // character boundaries first.  But in these encodings, a multichar byte
    // never contains any bytes in the ASCII range at all.
    if ((... or (data[here] == NEEDLE)))
      return here;

    // Nope, no hit.  Move on.
    here = next;
  }
  return sz;
}
} // namespace


/// Find first of `NEEDLE` ASCII chars in `haystack`.
/** @warning This assumes that one of the `NEEDLE` characters is actually
 * present.  It does not check for buffer overruns, so make sure that there's
 * a sentinel.
 */
template<encoding_group ENC, char... NEEDLE>
PQXX_PURE std::size_t
find_s_ascii_char(std::string_view haystack, std::size_t here)
{
  // We only know how to search for ASCII characters.  It's an optimisation
  // assumption in the code below.
  static_assert((... and ((NEEDLE >> 7) == 0)));

  auto const sz{std::size(haystack)};
  auto const data{std::data(haystack)};

  // No supported encoding has multibyte characters that start with an
  // ASCII-range byte.
  while ((... and (data[here] != NEEDLE)))
    here = glyph_scanner<ENC>::call(data, sz, here);
  return here;
}


template<> struct glyph_scanner<encoding_group::MONOBYTE>
{
  static PQXX_PURE constexpr std::size_t
  call(char const /* buffer */[], std::size_t buffer_len, std::size_t start)
  {
    // TODO: Don't bother with npos.  Let the caller check.
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;
    else
      return start + 1;
  }
};


// https://en.wikipedia.org/wiki/Big5#Organization
template<> struct glyph_scanner<encoding_group::BIG5>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (not between_inc(byte1, 0x81, 0xfe) or (start + 2 > buffer_len))
      PQXX_UNLIKELY
    throw_for_encoding_error("BIG5", buffer, start, 1);

    auto const byte2{get_byte(buffer, start + 1)};
    if (
      not between_inc(byte2, 0x40, 0x7e) and
      not between_inc(byte2, 0xa1, 0xfe))
      PQXX_UNLIKELY
    throw_for_encoding_error("BIG5", buffer, start, 2);

    return start + 2;
  }
};


/*
The PostgreSQL documentation claims that the EUC_* encodings are 1-3 bytes
each, but other documents explain that the EUC sets can contain 1-(2,3,4) bytes
depending on the specific extension:
    EUC_CN      : 1-2
    EUC_JP      : 1-3
    EUC_JIS_2004: 1-2
    EUC_KR      : 1-2
    EUC_TW      : 1-4
*/

// https://en.wikipedia.org/wiki/GB_2312#EUC-CN
template<> struct glyph_scanner<encoding_group::EUC_CN>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (not between_inc(byte1, 0xa1, 0xf7) or start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("EUC_CN", buffer, start, 1);

    auto const byte2{get_byte(buffer, start + 1)};
    if (not between_inc(byte2, 0xa1, 0xfe))
      PQXX_UNLIKELY
    throw_for_encoding_error("EUC_CN", buffer, start, 2);

    return start + 2;
  }
};


// EUC-JP and EUC-JIS-2004 represent slightly different code points but iterate
// the same:
//
// https://en.wikipedia.org/wiki/Extended_Unix_Code#EUC-JP
// http://x0213.org/codetable/index.en.html
template<> struct glyph_scanner<encoding_group::EUC_JP>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("EUC_JP", buffer, start, 1);

    auto const byte2{get_byte(buffer, start + 1)};
    if (byte1 == 0x8e)
    {
      if (not between_inc(byte2, 0xa1, 0xfe))
        PQXX_UNLIKELY
      throw_for_encoding_error("EUC_JP", buffer, start, 2);

      return start + 2;
    }

    if (between_inc(byte1, 0xa1, 0xfe))
    {
      if (not between_inc(byte2, 0xa1, 0xfe))
        PQXX_UNLIKELY
      throw_for_encoding_error("EUC_JP", buffer, start, 2);

      return start + 2;
    }

    if (byte1 == 0x8f and start + 3 <= buffer_len)
    {
      auto const byte3{get_byte(buffer, start + 2)};
      if (
        not between_inc(byte2, 0xa1, 0xfe) or
        not between_inc(byte3, 0xa1, 0xfe))
        PQXX_UNLIKELY
      throw_for_encoding_error("EUC_JP", buffer, start, 3);

      return start + 3;
    }

    throw_for_encoding_error("EUC_JP", buffer, start, 1);
  }
};


// https://en.wikipedia.org/wiki/Extended_Unix_Code#EUC-KR
template<> struct glyph_scanner<encoding_group::EUC_KR>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (not between_inc(byte1, 0xa1, 0xfe) or start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("EUC_KR", buffer, start, 1);

    auto const byte2{get_byte(buffer, start + 1)};
    if (not between_inc(byte2, 0xa1, 0xfe))
      PQXX_UNLIKELY
    throw_for_encoding_error("EUC_KR", buffer, start, 1);

    return start + 2;
  }
};


// https://en.wikipedia.org/wiki/Extended_Unix_Code#EUC-TW
template<> struct glyph_scanner<encoding_group::EUC_TW>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY
    return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("EUC_KR", buffer, start, 1);

    auto const byte2{get_byte(buffer, start + 1)};
    if (between_inc(byte1, 0xa1, 0xfe))
    {
      if (not between_inc(byte2, 0xa1, 0xfe))
        PQXX_UNLIKELY
      throw_for_encoding_error("EUC_KR", buffer, start, 2);

      return start + 2;
    }

    if (byte1 != 0x8e or start + 4 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("EUC_KR", buffer, start, 1);

    if (
      between_inc(byte2, 0xa1, 0xb0) and
      between_inc(get_byte(buffer, start + 2), 0xa1, 0xfe) and
      between_inc(get_byte(buffer, start + 3), 0xa1, 0xfe))
      return start + 4;

    PQXX_UNLIKELY
    throw_for_encoding_error("EUC_KR", buffer, start, 4);
  }
};


// https://en.wikipedia.org/wiki/GB_18030#Mapping
template<> struct glyph_scanner<encoding_group::GB18030>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;
    if (byte1 == 0x80)
      throw_for_encoding_error("GB18030", buffer, start, buffer_len - start);

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("GB18030", buffer, start, buffer_len - start);

    auto const byte2{get_byte(buffer, start + 1)};
    if (between_inc(byte2, 0x40, 0xfe))
    {
      if (byte2 == 0x7f)
        PQXX_UNLIKELY
      throw_for_encoding_error("GB18030", buffer, start, 2);

      return start + 2;
    }

    if (start + 4 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("GB18030", buffer, start, buffer_len - start);

    if (
      between_inc(byte2, 0x30, 0x39) and
      between_inc(get_byte(buffer, start + 2), 0x81, 0xfe) and
      between_inc(get_byte(buffer, start + 3), 0x30, 0x39))
      return start + 4;

    PQXX_UNLIKELY
    throw_for_encoding_error("GB18030", buffer, start, 4);
  }
};


// https://en.wikipedia.org/wiki/GBK_(character_encoding)#Encoding
template<> struct glyph_scanner<encoding_group::GBK>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("GBK", buffer, start, 1);

    auto const byte2{get_byte(buffer, start + 1)};
    if (
      (between_inc(byte1, 0xa1, 0xa9) and between_inc(byte2, 0xa1, 0xfe)) or
      (between_inc(byte1, 0xb0, 0xf7) and between_inc(byte2, 0xa1, 0xfe)) or
      (between_inc(byte1, 0x81, 0xa0) and between_inc(byte2, 0x40, 0xfe) and
       byte2 != 0x7f) or
      (between_inc(byte1, 0xaa, 0xfe) and between_inc(byte2, 0x40, 0xa0) and
       byte2 != 0x7f) or
      (between_inc(byte1, 0xa8, 0xa9) and between_inc(byte2, 0x40, 0xa0) and
       byte2 != 0x7f) or
      (between_inc(byte1, 0xaa, 0xaf) and between_inc(byte2, 0xa1, 0xfe)) or
      (between_inc(byte1, 0xf8, 0xfe) and between_inc(byte2, 0xa1, 0xfe)) or
      (between_inc(byte1, 0xa1, 0xa7) and between_inc(byte2, 0x40, 0xa0) and
       byte2 != 0x7f))
      return start + 2;

    PQXX_UNLIKELY
    throw_for_encoding_error("GBK", buffer, start, 2);
  }
};


/*
The PostgreSQL documentation claims that the JOHAB encoding is 1-3 bytes, but
"CJKV Information Processing" describes it (actually just the Hangul portion)
as "three five-bit segments" that reside inside 16 bits (2 bytes).

CJKV Information Processing by Ken Lunde, pg. 269:

  https://bit.ly/2BEOu5V
*/
template<> struct glyph_scanner<encoding_group::JOHAB>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("JOHAB", buffer, start, 1);

    auto const byte2{get_byte(buffer, start)};
    if (
      (between_inc(byte1, 0x84, 0xd3) and
       (between_inc(byte2, 0x41, 0x7e) or between_inc(byte2, 0x81, 0xfe))) or
      ((between_inc(byte1, 0xd8, 0xde) or between_inc(byte1, 0xe0, 0xf9)) and
       (between_inc(byte2, 0x31, 0x7e) or between_inc(byte2, 0x91, 0xfe))))
      return start + 2;

    PQXX_UNLIKELY
    throw_for_encoding_error("JOHAB", buffer, start, 2);
  }
};


/*
PostgreSQL's MULE_INTERNAL is the emacs rather than Xemacs implementation;
see the server/mb/pg_wchar.h PostgreSQL header file.
This is implemented according to the description in said header file, but I was
unable to get it to successfully iterate a MULE-encoded test CSV generated
using PostgreSQL 9.2.23.  Use this at your own risk.
*/
template<> struct glyph_scanner<encoding_group::MULE_INTERNAL>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("MULE_INTERNAL", buffer, start, 1);

    auto const byte2{get_byte(buffer, start + 1)};
    if (between_inc(byte1, 0x81, 0x8d) and byte2 >= 0xa0)
      return start + 2;

    if (start + 3 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("MULE_INTERNAL", buffer, start, 2);

    if (
      ((byte1 == 0x9a and between_inc(byte2, 0xa0, 0xdf)) or
       (byte1 == 0x9b and between_inc(byte2, 0xe0, 0xef)) or
       (between_inc(byte1, 0x90, 0x99) and byte2 >= 0xa0)) and
      (byte2 >= 0xa0))
      return start + 3;

    if (start + 4 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("MULE_INTERNAL", buffer, start, 3);

    if (
      ((byte1 == 0x9c and between_inc(byte2, 0xf0, 0xf4)) or
       (byte1 == 0x9d and between_inc(byte2, 0xf5, 0xfe))) and
      get_byte(buffer, start + 2) >= 0xa0 and
      get_byte(buffer, start + 4) >= 0xa0)
      return start + 4;

    PQXX_UNLIKELY
    throw_for_encoding_error("MULE_INTERNAL", buffer, start, 4);
  }
};


// As far as I can tell, for the purposes of iterating the only difference
// between SJIS and SJIS-2004 is increased range in the first byte of two-byte
// sequences (0xEF increased to 0xFC).  Officially, that is; apparently the
// version of SJIS used by Postgres has the same range as SJIS-2004.  They both
// have increased range over the documented versions, not having the even/odd
// restriction for the first byte in 2-byte sequences.
//
// https://en.wikipedia.org/wiki/Shift_JIS#Shift_JIS_byte_map
// http://x0213.org/codetable/index.en.html
template<> struct glyph_scanner<encoding_group::SJIS>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80 or between_inc(byte1, 0xa1, 0xdf))
      return start + 1;

    if (
      not between_inc(byte1, 0x81, 0x9f) and
      not between_inc(byte1, 0xe0, 0xfc))
      PQXX_UNLIKELY
    throw_for_encoding_error("SJIS", buffer, start, 1);

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("SJIS", buffer, start, buffer_len - start);

    auto const byte2{get_byte(buffer, start + 1)};
    if (byte2 == 0x7f)
      PQXX_UNLIKELY
    throw_for_encoding_error("SJIS", buffer, start, 2);

    if (between_inc(byte2, 0x40, 0x9e) or between_inc(byte2, 0x9f, 0xfc))
      return start + 2;

    PQXX_UNLIKELY
    throw_for_encoding_error("SJIS", buffer, start, 2);
  }
};


// https://en.wikipedia.org/wiki/Unified_Hangul_Code
template<> struct glyph_scanner<encoding_group::UHC>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("UHC", buffer, start, buffer_len - start);

    auto const byte2{get_byte(buffer, start + 1)};
    if (between_inc(byte1, 0x80, 0xc6))
    {
      if (
        between_inc(byte2, 0x41, 0x5a) or between_inc(byte2, 0x61, 0x7a) or
        between_inc(byte2, 0x80, 0xfe))
        return start + 2;

      PQXX_UNLIKELY
      throw_for_encoding_error("UHC", buffer, start, 2);
    }

    if (between_inc(byte1, 0xa1, 0xfe))
    {
      if (not between_inc(byte2, 0xa1, 0xfe))
        PQXX_UNLIKELY
      throw_for_encoding_error("UHC", buffer, start, 2);

      return start + 2;
    }

    throw_for_encoding_error("UHC", buffer, start, 1);
  }
};


// https://en.wikipedia.org/wiki/UTF-8#Description
template<> struct glyph_scanner<encoding_group::UTF8>
{
  static PQXX_PURE std::size_t
  call(char const buffer[], std::size_t buffer_len, std::size_t start)
  {
    if (start >= buffer_len)
      PQXX_UNLIKELY return std::string::npos;

    auto const byte1{get_byte(buffer, start)};
    if (byte1 < 0x80)
      return start + 1;

    if (start + 2 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("UTF8", buffer, start, buffer_len - start);

    auto const byte2{get_byte(buffer, start + 1)};
    if (between_inc(byte1, 0xc0, 0xdf))
    {
      if (not between_inc(byte2, 0x80, 0xbf))
        PQXX_UNLIKELY
      throw_for_encoding_error("UTF8", buffer, start, 2);

      return start + 2;
    }

    if (start + 3 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("UTF8", buffer, start, buffer_len - start);

    auto const byte3{get_byte(buffer, start + 2)};
    if (between_inc(byte1, 0xe0, 0xef))
    {
      if (between_inc(byte2, 0x80, 0xbf) and between_inc(byte3, 0x80, 0xbf))
        return start + 3;

      PQXX_UNLIKELY
      throw_for_encoding_error("UTF8", buffer, start, 3);
    }

    if (start + 4 > buffer_len)
      PQXX_UNLIKELY
    throw_for_encoding_error("UTF8", buffer, start, buffer_len - start);

    if (between_inc(byte1, 0xf0, 0xf7))
    {
      if (
        between_inc(byte2, 0x80, 0xbf) and between_inc(byte3, 0x80, 0xbf) and
        between_inc(get_byte(buffer, start + 3), 0x80, 0xbf))
        return start + 4;

      PQXX_UNLIKELY
      throw_for_encoding_error("UTF8", buffer, start, 4);
    }

    PQXX_UNLIKELY
    throw_for_encoding_error("UTF8", buffer, start, 1);
  }
};


/// Just for searching an ASCII character, what encoding can we use here?
/** Maps an encoding group to an encoding group that we can apply for the
 * specific purpose of looking for a given ASCII character.
 *
 * The "difficult" encoding groups will map to themselves.  But the ones that
 * work like "ASCII supersets" have the wonderful property that even a
 * multibyte character cannot contain a byte that happens to be in the ASCII
 * range.  This holds for the single-byte encodings, for example, but also for
 * UTF-8.
 *
 * For those encodings, we can just pretend that we're dealing with a
 * single-byte encoding and scan byte-by-byte until we find a byte with the
 * value we're looking for.  We don't actually need to know where the
 * boundaries between the characters are.
 */
constexpr inline encoding_group
map_ascii_search_group(encoding_group enc) noexcept
{
  switch (enc)
  {
  case encoding_group::MONOBYTE:
  case encoding_group::EUC_CN:
  case encoding_group::EUC_JP:
  case encoding_group::EUC_KR:
  case encoding_group::EUC_TW:
  case encoding_group::MULE_INTERNAL:
  case encoding_group::UTF8:
    // All these encodings are "ASCII-safe," meaning that if we're looking
    // for a particular ASCII character, we can safely just go through the
    // string byte for byte.  Multibyte characters have the high bit set.
    return encoding_group::MONOBYTE;

  default: PQXX_UNLIKELY return enc;
  }
}


/// Look up a character search function for an encoding group.
/** We only define a few individual instantiations of this function, as needed.
 *
 * Returns a pointer to a function which looks for the first instance of any of
 * the ASCII characters in `NEEDLE`.  Returns its offset, or the end of the
 * `haystack` if it found none.
 */
template<char... NEEDLE>
PQXX_PURE constexpr inline char_finder_func *
get_char_finder(encoding_group enc)
{
  auto const as_if{map_ascii_search_group(enc)};
  switch (as_if)
  {
  case encoding_group::MONOBYTE:
    return pqxx::internal::find_ascii_char<
      encoding_group::MONOBYTE, NEEDLE...>;
  case encoding_group::BIG5:
    return pqxx::internal::find_ascii_char<encoding_group::BIG5, NEEDLE...>;
  case encoding_group::GB18030:
    return pqxx::internal::find_ascii_char<encoding_group::GB18030, NEEDLE...>;
  case encoding_group::GBK:
    return pqxx::internal::find_ascii_char<encoding_group::GBK, NEEDLE...>;
  case encoding_group::JOHAB:
    return pqxx::internal::find_ascii_char<encoding_group::JOHAB, NEEDLE...>;
  case encoding_group::SJIS:
    return pqxx::internal::find_ascii_char<encoding_group::SJIS, NEEDLE...>;
  case encoding_group::UHC:
    return pqxx::internal::find_ascii_char<encoding_group::UHC, NEEDLE...>;

  default:
    throw pqxx::internal_error{concat(
      "Unexpected encoding group: ", as_if, " (mapped from ", enc, ").")};
  }
}


/// Look up a "sentry" character search function for an encoding group.
/** This version returns a finder function that does not check buffer bounds.
 * It just assumes that one of the `NEEDLE` characters will be there.
 */
template<char... NEEDLE>
PQXX_PURE constexpr inline char_finder_func *
get_s_char_finder(encoding_group enc)
{
  auto const as_if{map_ascii_search_group(enc)};
  switch (as_if)
  {
  case encoding_group::MONOBYTE:
    return pqxx::internal::find_s_ascii_char<
      encoding_group::MONOBYTE, NEEDLE...>;
  case encoding_group::BIG5:
    return pqxx::internal::find_s_ascii_char<encoding_group::BIG5, NEEDLE...>;
  case encoding_group::GB18030:
    return pqxx::internal::find_s_ascii_char<
      encoding_group::GB18030, NEEDLE...>;
  case encoding_group::GBK:
    return pqxx::internal::find_s_ascii_char<encoding_group::GBK, NEEDLE...>;
  case encoding_group::JOHAB:
    return pqxx::internal::find_s_ascii_char<encoding_group::JOHAB, NEEDLE...>;
  case encoding_group::SJIS:
    return pqxx::internal::find_s_ascii_char<encoding_group::SJIS, NEEDLE...>;
  case encoding_group::UHC:
    return pqxx::internal::find_s_ascii_char<encoding_group::UHC, NEEDLE...>;

  default:
    throw pqxx::internal_error{concat(
      "Unexpected encoding group: ", as_if, " (mapped from ", enc, ").")};
  }
}
} // namespace pqxx::internal
#endif
