/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_SI_FACTORY_H__
#define __WIREPLUMBER_SI_FACTORY_H__

#include "core.h"
#include "session-item.h"

G_BEGIN_DECLS

/*!
 * \brief The WpSiFactory GType
 * \ingroup wpsifactory
 */
#define WP_TYPE_SI_FACTORY (wp_si_factory_get_type ())
WP_API
G_DECLARE_DERIVABLE_TYPE (WpSiFactory, wp_si_factory, WP, SI_FACTORY, GObject)

struct _WpSiFactoryClass
{
  GObjectClass parent_class;

  WpSessionItem * (*construct) (WpSiFactory * self, WpCore * core);

  /*< private >*/
  WP_PADDING(7)
};

WP_API
WpSiFactory * wp_si_factory_new_simple (const gchar * factory_name,
    GType si_type);

WP_API
const gchar * wp_si_factory_get_name (WpSiFactory * self);

WP_API
WpSessionItem * wp_si_factory_construct (WpSiFactory * self, WpCore * core);

WP_API
WpSiFactory * wp_si_factory_find (WpCore * core, const gchar * factory_name);

WP_API
WpSessionItem * wp_session_item_make (WpCore * core, const gchar * factory_name);

G_END_DECLS

#endif
