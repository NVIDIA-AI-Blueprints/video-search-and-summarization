/* libjpeg-turbo build number */
#define BUILD  ""

/* Compiler's inline keyword */
#undef inline

/* How to obtain function inlining. */
#ifndef INLINE
#if defined(__GNUC__)
#define INLINE  inline __attribute__((always_inline))
#elif defined(_MSC_VER)
#define INLINE  __forceinline
#else
#define INLINE
#endif
#endif

/* How to obtain thread-local storage */
#if defined(_MSC_VER) && (defined(_WIN32) || defined(_WIN64))
#define THREAD_LOCAL  __declspec(thread)
#else
#define THREAD_LOCAL  __thread
#endif

/* Define to the full name of this package. */
#define PACKAGE_NAME  "libjpeg-turbo"

/* Version number of package */
#define VERSION  "2.1.5.1"

/* The size of `size_t', as computed by sizeof. */
#include <stdint.h>
#if __WORDSIZE==64 || defined(_WIN64)
#define SIZEOF_SIZE_T  8
#else
#define SIZEOF_SIZE_T  4
#endif

/* Define if your compiler has __builtin_ctzl() and sizeof(unsigned long) == sizeof(size_t). */
#if defined(__GNUC__)
#define HAVE_BUILTIN_CTZL
#endif

/* Define to 1 if you have the <intrin.h> header file. */
#if defined(_MSC_VER)
#define HAVE_INTRIN_H  1
#endif

#if defined(_MSC_VER) && defined(HAVE_INTRIN_H)
#if (SIZEOF_SIZE_T == 8)
#define HAVEBITSCANFORWARD64
#elif (SIZEOF_SIZE_T == 4)
#define HAVEBITSCANFORWARD
#endif
#endif

#if defined(__has_attribute)
#if __has_attribute(fallthrough)
#define FALLTHROUGH __attribute__((fallthrough));
#else
#define FALLTHROUGH
#endif
#else
#define FALLTHROUGH
#endif
