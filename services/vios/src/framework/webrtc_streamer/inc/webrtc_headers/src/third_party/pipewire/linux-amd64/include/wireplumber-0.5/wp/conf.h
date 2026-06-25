/* WirePlumber
 *
 * Copyright © 2022 Collabora Ltd.
 *    @author Julian Bouzas <julian.bouzas@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_CONF_H__
#define __WIREPLUMBER_CONF_H__

#include "spa-json.h"
#include "properties.h"

G_BEGIN_DECLS

struct pw_context;

/*!
 * \brief The WpConf GType
 * \ingroup wpconf
 */
#define WP_TYPE_CONF (wp_conf_get_type ())

WP_API
G_DECLARE_FINAL_TYPE (WpConf, wp_conf, WP, CONF, GObject)

WP_API
WpConf * wp_conf_new (const gchar * name, WpProperties * properties);

WP_API
WpConf * wp_conf_new_open (const gchar * name, WpProperties * properties,
    GError ** error);

WP_API
gboolean wp_conf_open (WpConf * self, GError ** error);

WP_API
void wp_conf_close (WpConf * self);

WP_API
gboolean wp_conf_is_open (WpConf * self);

WP_API
const gchar * wp_conf_get_name (WpConf * self);

WP_API
WpSpaJson * wp_conf_get_section (WpConf *self, const gchar *section);

WP_API
gint wp_conf_section_update_props (WpConf * self, const gchar * section,
    WpProperties * props);

WP_API
void wp_conf_parse_pw_context_sections (WpConf * self,
    struct pw_context * context);

G_END_DECLS

#endif
