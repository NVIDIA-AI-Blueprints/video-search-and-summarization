/* WirePlumber
 *
 * Copyright © 2019 Collabora Ltd.
 *    @author Julian Bouzas <julian.bouzas@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_PORT_H__
#define __WIREPLUMBER_PORT_H__

#include "global-proxy.h"

G_BEGIN_DECLS

/*!
 * \brief The different directions that a port can have
 * \ingroup wpport
 */
typedef enum {
  WP_DIRECTION_INPUT, /*!< a sink, consuming input */
  WP_DIRECTION_OUTPUT, /*!< a source, producing output */
} WpDirection;

/*!
 * \brief The WpPort GType
 * \ingroup wpport
 */
#define WP_TYPE_PORT (wp_port_get_type ())
WP_API
G_DECLARE_FINAL_TYPE (WpPort, wp_port, WP, PORT, WpGlobalProxy)

WP_API
WpDirection wp_port_get_direction (WpPort * self);

G_END_DECLS

#endif
