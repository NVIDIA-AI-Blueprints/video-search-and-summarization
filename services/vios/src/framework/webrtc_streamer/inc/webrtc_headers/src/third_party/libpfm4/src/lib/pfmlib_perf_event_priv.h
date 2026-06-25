/*
 * pfmlib_perf_events_priv.h: perf_event public attributes
 *
 * Copyright (c) 2011 Google, Inc
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
#ifndef __PERF_EVENT_PRIV_H__
#define __PERF_EVENT_PRIV_H__
#include "pfmlib_priv.h"
#include "perfmon/pfmlib_perf_event.h"

#define PERF_ATTR_U	0	/* monitor at user privilege levels */
#define PERF_ATTR_K	1	/* monitor at kernel privilege levels */
#define PERF_ATTR_H	2	/* monitor at hypervisor levels */
#define PERF_ATTR_PE	3	/* sampling period */
#define PERF_ATTR_FR	4	/* average target sampling rate */
#define PERF_ATTR_PR	5	/* precise sampling mode */
#define PERF_ATTR_EX	6	/* exclusive event */
#define PERF_ATTR_MG	7	/* monitor guest execution */
#define PERF_ATTR_MH	8	/* monitor host execution */
#define PERF_ATTR_CPU	9	/* CPU to program */
#define PERF_ATTR_PIN  10	/* pin event to CPU */
#define PERF_ATTR_HWS  11	/* hardware sampling */

#define _PERF_ATTR_U  (1 << PERF_ATTR_U)
#define _PERF_ATTR_K  (1 << PERF_ATTR_K)
#define _PERF_ATTR_H  (1 << PERF_ATTR_H)
#define _PERF_ATTR_PE (1 << PERF_ATTR_PE)
#define _PERF_ATTR_FR (1 << PERF_ATTR_FR)
#define _PERF_ATTR_PR (1 << PERF_ATTR_PR)
#define _PERF_ATTR_EX (1 << PERF_ATTR_EX)
#define _PERF_ATTR_MG (1 << PERF_ATTR_MG)
#define _PERF_ATTR_MH (1 << PERF_ATTR_MH)
#define _PERF_ATTR_CPU (1 << PERF_ATTR_CPU)
#define _PERF_ATTR_PIN (1 << PERF_ATTR_PIN)
#define _PERF_ATTR_HWS (1 << PERF_ATTR_HWS)

#define PERF_PLM_ALL (PFM_PLM0|PFM_PLM3|PFM_PLMH)

extern int pfm_perf_find_pmu_type(void *this, int *type);

#define SYSFS_PMU_DEVICES_DIR	"/sys/bus/event_source/devices"

#endif
