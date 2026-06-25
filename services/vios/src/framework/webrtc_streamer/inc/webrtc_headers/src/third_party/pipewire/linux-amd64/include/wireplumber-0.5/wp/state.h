/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author Julian Bouzas <julian.bouzas@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_STATE_H__
#define __WIREPLUMBER_STATE_H__

#include "properties.h"
#include "core.h"

G_BEGIN_DECLS

/* WpState */

/*!
 * \brief The WpState GType
 * \ingroup wpstate
 */
#define WP_TYPE_STATE (wp_state_get_type ())
WP_API
G_DECLARE_FINAL_TYPE (WpState, wp_state, WP, STATE, GObject)

WP_API
WpState * wp_state_new (const gchar *name);

WP_API
const gchar * wp_state_get_name (WpState *self);

WP_API
const gchar * wp_state_get_location (WpState *self);

WP_API
void wp_state_clear (WpState *self);

WP_API
gboolean wp_state_save (WpState *self, WpProperties *props, GError ** error);

WP_API
void wp_state_save_after_timeout (WpState *self, WpCore *core,
    WpProperties *props);

WP_API
WpProperties * wp_state_load (WpState *self);

G_END_DECLS

#endif
