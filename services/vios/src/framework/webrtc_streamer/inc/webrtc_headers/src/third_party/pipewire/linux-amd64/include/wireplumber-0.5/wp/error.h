/* WirePlumber
 *
 * Copyright © 2019 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WP_ERROR_H__
#define __WP_ERROR_H__

#include <glib.h>
#include "defs.h"

G_BEGIN_DECLS

/*!
 * \brief A GError domain for errors that occurred within the context of the
 * WirePlumber library.
 * \ingroup wperror
 */
#define WP_DOMAIN_LIBRARY (wp_domain_library_quark ())
WP_API
GQuark wp_domain_library_quark (void);

/*!
 * \brief Error codes that can appear in a GError when the error domain
 * is WP_DOMAIN_LIBRARY
 * \ingroup wperror
 */
typedef enum {
  /*! an invariant check failed; this most likely
   *  indicates a programming error */
  WP_LIBRARY_ERROR_INVARIANT,
  /*! an unexpected/invalid argument was given */
  WP_LIBRARY_ERROR_INVALID_ARGUMENT,
  /*! an operation failed */
  WP_LIBRARY_ERROR_OPERATION_FAILED,
  /*! a required external service was unavailable */
  WP_LIBRARY_ERROR_SERVICE_UNAVAILABLE,
} WpLibraryErrorEnum;

G_END_DECLS

#endif
