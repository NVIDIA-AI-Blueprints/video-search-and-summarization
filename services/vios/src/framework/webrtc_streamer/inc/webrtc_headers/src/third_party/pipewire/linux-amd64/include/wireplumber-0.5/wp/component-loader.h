/* WirePlumber
 *
 * Copyright © 2021 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_COMPONENT_LOADER_H__
#define __WIREPLUMBER_COMPONENT_LOADER_H__

#include "plugin.h"
#include "spa-json.h"

G_BEGIN_DECLS

/*!
 * \brief The WpComponentLoader GType
 * \ingroup wpcomponentloader
 */
#define WP_TYPE_COMPONENT_LOADER (wp_component_loader_get_type ())
WP_API
G_DECLARE_INTERFACE (WpComponentLoader, wp_component_loader,
                     WP, COMPONENT_LOADER, GObject)

struct _WpComponentLoaderInterface
{
  GTypeInterface interface;

  gboolean (*supports_type) (WpComponentLoader * self, const gchar * type);

  void (*load) (WpComponentLoader * self, WpCore * core,
      const gchar * component, const gchar * type, WpSpaJson * args,
      GCancellable * cancellable, GAsyncReadyCallback callback, gpointer data);

  GObject * (*load_finish) (WpComponentLoader * self, GAsyncResult * res,
      GError ** error);

  /*< private >*/
  WP_PADDING(5)
};

WP_API
void wp_core_load_component (WpCore * self, const gchar * component,
    const gchar * type, WpSpaJson * args, const gchar * provides,
    GCancellable * cancellable, GAsyncReadyCallback callback, gpointer data);

WP_API
gboolean wp_core_load_component_finish (WpCore * self, GAsyncResult * res,
    GError ** error);

G_END_DECLS

#endif
