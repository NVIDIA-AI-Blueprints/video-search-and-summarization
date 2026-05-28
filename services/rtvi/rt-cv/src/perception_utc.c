/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
* SPDX-License-Identifier: Apache-2.0
*
* Licensed under the Apache License, Version 2.0 (the "License");
* you may not use this file except in compliance with the License.
* You may obtain a copy of the License at
*
* http://www.apache.org/licenses/LICENSE-2.0
*
* Unless required by applicable law or agreed to in writing, software
* distributed under the License is distributed on an "AS IS" BASIS,
* WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
* See the License for the specific language governing permissions and
* limitations under the License.
*/


#define _DEFAULT_SOURCE
#define _XOPEN_SOURCE
#include <stdio.h>
#include <stdlib.h>
#include <stdint.h>
#include <string.h>
#include <time.h>

#include "metropolis_perception_app.h"

/** URI sample:
 * HWY_20_AND_BRYANT__WB__4_11_2018_4_59_59_485_AM_UTC-07_00.mp4
 *
 * Specification format:
 * __M_DD_YYYY_H_MIN_SEC_MSEC_AM/PM_UTC<Offset>.mp4
 * a) Offset is:
 * <+/-HOURS_MIN>
 * b) In ..AM/PM_TIMEZONE<Offset>.mp4
 * TIMEZONE will always be UTC
 *
 * The __M_DD_YYYY_H_MIN_SEC_MSEC_AM/PM time
 * shall <Offset-period> behind UTC
 *
 */
#define URI_UTC_START_DELIM "__"
#define URI_UTC_END_DELIM "_UTC"
#define MAX_UTC_STRING_LEN (256)
#define LENGTH__AMPM_UTC (3)  //"_AM"
#define LENGTH_UTC_END_DELIM (4)

static gboolean extract_ms_from_utc(gchar *utc, guint32 *ms) {
  /** find _UTC delim */
  gchar *utc_delim = (gchar *)strstr(utc, URI_UTC_END_DELIM);
  gint32 utc_string_length;
  if (!utc_delim) {
    return FALSE;
  }

  /** find the immediately preceeding '_' */
  utc_string_length =
      (guint32)(((size_t)utc_delim) - ((size_t)utc)) - LENGTH__AMPM_UTC - 1;
  gint32 i_ms = 0; /**< index into utc at which ms is */
  for (i_ms = utc_string_length; ((i_ms >= 0) && (utc[i_ms] != '_')); i_ms--);
  if (i_ms < 0) {
    return FALSE;
  }

  // Skip the leading underscore
  if (utc[i_ms] != '_') {
    return FALSE;
  }
  i_ms++; // Move past the underscore

  // Check if we have digits followed by underscore
  char *endptr;
  unsigned long long value = strtoull(&utc[i_ms], &endptr, 10);
  // Verify that conversion was successful and ended with underscore
  if (endptr == &utc[i_ms] || *endptr != '_' || value > UINT32_MAX) {
    return FALSE;
  }

  *ms = (guint32)value;

  /** also remove the ms part from utc string */
  g_strlcpy(&utc[i_ms], (utc_delim - LENGTH__AMPM_UTC), MAX_UTC_STRING_LEN - utc_string_length + 1);

  return TRUE;
}

/**
 * @brief  Extracts and returns the <Offset> in nanoseconds
 */
static gboolean extract_offset_from_utc(gchar *utc, gint64 *offset_nsec) {
  /** find _UTC delim */
  gchar *utc_offset = (gchar *)strstr(utc, URI_UTC_END_DELIM);
  if (!utc_offset) {
    return FALSE;
  }
  utc_offset += LENGTH_UTC_END_DELIM;

  // Parse hours
  char *endptr;
  long hours = strtol(utc_offset, &endptr, 10);

  // Validate hours parsing and check for underscore
  if (endptr == utc_offset || *endptr != '_' || hours < -23 || hours > 23) {
    return FALSE;
  }

  // Move past underscore to parse minutes
  const char *min_str = endptr + 1;
  long minutes = strtol(min_str, &endptr, 10);

  // Validate minutes
  if (endptr == min_str || minutes < 0 || minutes > 59) {
    return FALSE;
  }

  *offset_nsec = ((ABS(hours) * 60 + minutes) * 60) * GST_SECOND;
  if (hours > 0) {
    /** if positive, offset shall be subtracted from
     * __M_DD_YYYY_H_MIN_SEC_MSEC_AM/PM to arrive at UTC
     * Otherwise added
     */
    *offset_nsec = (*offset_nsec) * -1;
  }
  return TRUE;
}

struct timespec extract_utc_from_uri(gchar *uri) {
  gchar utc_string[MAX_UTC_STRING_LEN + 1];  // +1 for null terminator
  struct tm utc_tmbroken = {0};
  struct timespec utc_timespec = {0};
  gchar *utc_iter = uri;
  gchar *utc = NULL;
  guint32 ms = 0;
  gint64 time_ns = 0;

  // Initialize the buffer with zeros
  memset(utc_string, 0, sizeof(utc_string));

  /** Find the beginning of UTC time field in URI
   * The URI delimiter
   */
  do {
    utc = utc_iter;
    utc_iter = (gchar *)strstr(utc, URI_UTC_START_DELIM);
    if (utc_iter) {
      /** skip the starting delim */
      utc_iter += strlen(URI_UTC_START_DELIM);
    }
  } while (utc_iter);

  if (utc == uri) {
    /** Invalid URI */
    return utc_timespec;
  }

  /** extract ms and remove ms_ */
  g_strlcpy(utc_string, utc, MAX_UTC_STRING_LEN);

  gboolean ok = extract_ms_from_utc(utc_string, &ms);
  if (!ok) {
    /** Invalid URI */
    return utc_timespec;
  }

  /** First generate the time tm structure from provided string;
   * Note: Assuming UTC always */
  gchar *first_char_not_processed =
      strptime((const char *)utc_string, "%m_%d_%Y_%I_%M_%S_%p", &utc_tmbroken);
  if (!first_char_not_processed ||
      strncmp(first_char_not_processed, URI_UTC_END_DELIM,
              LENGTH_UTC_END_DELIM) != 0) {
    /** first_char_not_processed should be URI_UTC_END_DELIM
     * Otherwise, it is an error condition */
    return utc_timespec;
  }

  ok = extract_offset_from_utc(utc_string, &time_ns);
  if (!ok) {
    /** Invalid URI */
    return utc_timespec;
  }

  /** mktime(): Now convert the broken down tm struct to time_t, calendar time
   * representation*/
  utc_timespec.tv_sec = timegm(&utc_tmbroken);
  utc_timespec.tv_nsec = ms * GST_MSECOND;

  /** final time is */
  time_ns = (gint64)(GST_TIMESPEC_TO_TIME(utc_timespec)) + time_ns;

  GST_TIME_TO_TIMESPEC(time_ns, utc_timespec);

  return utc_timespec;
}
