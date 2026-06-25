/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_GLOBAL_PROXY_H__
#define __WIREPLUMBER_GLOBAL_PROXY_H__

#include "proxy.h"
#include "properties.h"

G_BEGIN_DECLS

/*!
 * \brief The WpGlobalProxy GType
 * \ingroup wpglobalproxy
 */
#define WP_TYPE_GLOBAL_PROXY (wp_global_proxy_get_type ())
WP_API
G_DECLARE_DERIVABLE_TYPE (WpGlobalProxy, wp_global_proxy,
                          WP, GLOBAL_PROXY, WpProxy)

struct _WpGlobalProxyClass
{
  WpProxyClass parent_class;

  /*< private >*/
  WP_PADDING(4)
};

WP_API
void wp_global_proxy_request_destroy (WpGlobalProxy * self);

WP_API
guint32 wp_global_proxy_get_permissions (WpGlobalProxy * self);

WP_API
WpProperties * wp_global_proxy_get_global_properties (
    WpGlobalProxy * self);

WP_API
gboolean wp_global_proxy_bind (WpGlobalProxy * self);

G_END_DECLS

#endif
