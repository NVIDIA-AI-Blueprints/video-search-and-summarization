/* WirePlumber
 *
 * Copyright © 2019 Collabora Ltd.
 *    @author Julian Bouzas <julian.bouzas@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_LINK_H__
#define __WIREPLUMBER_LINK_H__

#include "global-proxy.h"

G_BEGIN_DECLS

/*!
 * \brief The state of the link
 * \ingroup wplink
 *
 * \since 0.4.11
 */
typedef enum {
  WP_LINK_STATE_ERROR = -2,     /*!< the link is in error */
  WP_LINK_STATE_UNLINKED = -1,  /*!< the link is unlinked */
  WP_LINK_STATE_INIT = 0,       /*!< the link is initialized */
  WP_LINK_STATE_NEGOTIATING = 1,  /*!< the link is negotiating formats */
  WP_LINK_STATE_ALLOCATING = 2, /*!< the link is allocating buffers */
  WP_LINK_STATE_PAUSED = 3,     /*!< the link is paused */
  WP_LINK_STATE_ACTIVE = 4,     /*!< the link is active */
} WpLinkState;

/*!
 * \brief The WpLink GType
 * \ingroup wplink
 */
#define WP_TYPE_LINK (wp_link_get_type ())
WP_API
G_DECLARE_FINAL_TYPE (WpLink, wp_link, WP, LINK, WpGlobalProxy)

WP_API
WpLink * wp_link_new_from_factory (WpCore * core,
    const gchar * factory_name, WpProperties * properties);

WP_API
void wp_link_get_linked_object_ids (WpLink * self,
    guint32 * output_node, guint32 * output_port,
    guint32 * input_node, guint32 * input_port);

WP_API
WpLinkState wp_link_get_state (WpLink * self, const gchar ** error);

G_END_DECLS

#endif
