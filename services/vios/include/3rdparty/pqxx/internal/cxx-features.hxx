/** C++ feature test macros.
 *
 * Flags defining available C++ features.  These will progressively replace
 * feature tests at the build stage.
 *
 * Why define these and not use the standard C++ macros directly?  To avoid
 * annoying warnings.  The standard macros rely on te fact that an undefined
 * macro, used as a boolean in a preprocessor conditional, evaluates as false.
 * This is pretty fundamental to how the standard feature test macros work:
 * you just say `#if __cpp_lib_ssize`, for example, to test whether the
 * compiler supports `std::ssize_t`.  If it doesn't, then the macro will not
 * exist, and the `#if` decides that no, you don't have that feature.  But
 * some users may ask their compilers to report any use of an undefined macro
 * as an error.
 *
 * So, we define alternate macros here that you can safely use in `#if`.
 */
#if defined(__cpp_multidimensional_subscript)
#  if __cpp_multidimensional_subscript
#    define pqxx_have_multidim 1
#  endif // __cpp_multidimensional_subscript
#endif   // __cpp_multidimensional_subscript
#if !defined(pqxx_have_multidim)
#  define pqxx_have_multidim 0
#endif // pqxx_have_multidim

#if defined(__cpp_lib_source_location) && __cpp_lib_source_location
#  define pqxx_have_source_location 1
#else
#  define pqxx_have_source_location 0
#endif // __cpp_lib_source_location

#if defined(__cpp_lib_ssize) && __cpp_lib_ssize
#  define pqxx_have_ssize 1
#else
#  define pqxx_have_ssize 0
#endif // __cpp_lib_ssize

#if defined(__cpp_lib_unreachable) && __cpp_lib_unreachable
#  define pqxx_have_unreachable 1
#else
#  define pqxx_have_unreachable 0
#endif // __cpp_lib_unreachable
