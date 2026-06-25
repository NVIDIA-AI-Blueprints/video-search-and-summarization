/*
 * Copyright (c) 2009 Google, Inc
 * Contributed by Stephane Eranian <eranian@gmail.com>
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
#ifndef __PFMLIB_PERF_EVENTS_H__
#define __PFMLIB_PERF_EVENTS_H__

#include <perfmon/pfmlib.h>
#include <perfmon/perf_event.h>

#pragma GCC visibility push(default)

#ifdef __cplusplus
extern "C" {
#endif

/*
 * use with PFM_OS_PERF, PFM_OS_PERF_EXT for pfm_get_os_event_encoding()
 */
typedef struct {
	struct perf_event_attr *attr;	/* in/out: perf_event struct pointer */
	char **fstr;			/* out/in: fully qualified event string */
	size_t size;			/* sizeof struct */
	int idx;			/* out: opaque event identifier */
	int cpu;			/* out: cpu to program, -1 = not set */
	int flags;			/* out: perf_event_open() flags */
	int pad0;			/* explicit 64-bit mode padding */
} pfm_perf_encode_arg_t;

#if __WORDSIZE == 64
#define PFM_PERF_ENCODE_ABI0	40	/* includes 4-byte padding */
#else
#define PFM_PERF_ENCODE_ABI0	28
#endif
/*
 * old interface, maintained for backward compatibility with older versions o
 * the library. Should use pfm_get_os_event_encoding() now
 */
extern pfm_err_t pfm_get_perf_event_encoding(const char *str,
					     int dfl_plm,
					     struct perf_event_attr *output,
					     char **fstr,
					     int *idx);

#ifdef __cplusplus /* extern C */
}
#endif

#pragma GCC visibility pop

#endif /* __PFMLIB_PERF_EVENT_H__ */
