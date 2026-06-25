/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_PLUGIN_H__
#define __WIREPLUMBER_PLUGIN_H__

#include "object.h"

G_BEGIN_DECLS

/*!
 * \brief Flags to be used as WpObjectFeatures on WpPlugin subclasses.
 * \ingroup wpplugin
 */
typedef enum { /*< flags >*/
  /*! enables the plugin */
  WP_PLUGIN_FEATURE_ENABLED = (1 << 0),
} WpPluginFeatures;

/*!
 * \brief The WpPlugin GType
 * \ingroup wpplugin
 */
#define WP_TYPE_PLUGIN (wp_plugin_get_type ())
WP_API
G_DECLARE_DERIVABLE_TYPE (WpPlugin, wp_plugin, WP, PLUGIN, WpObject)

struct _WpPluginClass
{
  WpObjectClass parent_class;

  void (*enable) (WpPlugin * self, WpTransition * transition);
  void (*disable) (WpPlugin * self);

  /*< private >*/
  WP_PADDING(6)
};

WP_API
WpPlugin * wp_plugin_find (WpCore * core, const gchar * plugin_name);

WP_API
const gchar * wp_plugin_get_name (WpPlugin * self);

G_END_DECLS

#endif
