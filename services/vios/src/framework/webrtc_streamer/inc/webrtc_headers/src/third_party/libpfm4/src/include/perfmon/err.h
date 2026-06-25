/*
 * err.h: substitute header for compiling on Windows with MingGW
 *
 * Permission is hereby granted, free of charge, to any person obtaining a copy
 * of this software and associated documentation files (the "Software"), to deal
 * in the Software without restriction, including without limitation the rights
 * to use, copy, modify, merge, publish, distribute, sublicense, and/or sell copies
 * of the Software, and to permit persons to whom the Software is furnished to do so,
 * subject to the following conditions:
 *
 * The above copyright notice and this permission notice shall be included in all
 * copies or substantial portions of the Software.
 *
 * THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
 * INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
 * PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
 * HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
 * CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE
 * OR THE USE OR OTHER DEALINGS IN THE SOFTWARE.
  */
#ifndef __PFM_ERR_H__
#define __PFM_ERR_H__

#ifndef PFMLIB_WINDOWS
#include <err.h>
#else /* PFMLIB_WINDOWS */
#define warnx(...) do { \
        fprintf (stderr, __VA_ARGS__); \
        fprintf (stderr, "\n"); \
} while (0)

#define errx(code, ...) do { \
        fprintf (stderr, __VA_ARGS__); \
        fprintf (stderr, "\n"); \
        exit (code); \
} while (0)

#define err(code, ...) do { \
        fprintf (stderr, __VA_ARGS__); \
        fprintf (stderr, " : %s\n", strerror(errno)); \
        exit (code); \
} while (0)
#endif

#endif /* __PFM_ERR_H__ */
