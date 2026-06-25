/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_PROXY_INTERFACES_H__
#define __WIREPLUMBER_PROXY_INTERFACES_H__

#include "proxy.h"
#include "properties.h"
#include "spa-pod.h"

G_BEGIN_DECLS

/*!
 * \brief The WpPipewireObject GType
 * \ingroup wppipewireobject
 */
#define WP_TYPE_PIPEWIRE_OBJECT (wp_pipewire_object_get_type ())
WP_API
G_DECLARE_INTERFACE (WpPipewireObject, wp_pipewire_object,
                     WP, PIPEWIRE_OBJECT, WpProxy)

struct _WpPipewireObjectInterface
{
  GTypeInterface parent_iface;

  gconstpointer (*get_native_info) (WpPipewireObject * self);

  WpProperties * (*get_properties) (WpPipewireObject * self);

  GVariant * (*get_param_info) (WpPipewireObject * self);

  void (*enum_params) (WpPipewireObject * self, const gchar * id,
      WpSpaPod * filter, GCancellable * cancellable,
      GAsyncReadyCallback callback, gpointer user_data);

  WpIterator * (*enum_params_finish) (WpPipewireObject * self,
      GAsyncResult * res, GError ** error);

  WpIterator * (*enum_params_sync) (WpPipewireObject * self,
      const gchar * id, WpSpaPod * filter);

  gboolean (*set_param) (WpPipewireObject * self, const gchar * id,
      guint32 flags, WpSpaPod * param);

  /*< private >*/
  WP_PADDING(5)
};

WP_API
gconstpointer wp_pipewire_object_get_native_info (WpPipewireObject * self);

WP_API
WpProperties * wp_pipewire_object_get_properties (WpPipewireObject * self);

WP_API
WpIterator * wp_pipewire_object_new_properties_iterator (
    WpPipewireObject * self);

WP_API
const gchar * wp_pipewire_object_get_property (WpPipewireObject * self,
    const gchar * key);

WP_API
GVariant * wp_pipewire_object_get_param_info (WpPipewireObject * self);

WP_API
void wp_pipewire_object_enum_params (WpPipewireObject * self, const gchar * id,
    WpSpaPod *filter, GCancellable * cancellable,
    GAsyncReadyCallback callback, gpointer user_data);

WP_API
WpIterator * wp_pipewire_object_enum_params_finish (WpPipewireObject * self,
    GAsyncResult * res, GError ** error);

WP_API
WpIterator * wp_pipewire_object_enum_params_sync (WpPipewireObject * self,
    const gchar * id, WpSpaPod * filter);

WP_API
gboolean wp_pipewire_object_set_param (WpPipewireObject * self,
    const gchar * id, guint32 flags, WpSpaPod * param);


G_END_DECLS

#endif
