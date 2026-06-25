/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_SESSION_ITEM_H__
#define __WIREPLUMBER_SESSION_ITEM_H__

#include "object.h"
#include "proxy.h"
#include "properties.h"

G_BEGIN_DECLS

/*!
 * \brief Flags to be used as WpObjectFeatures for WpSessionItem subclasses.
 * \ingroup wpsessionitem
 */
typedef enum { /*< flags >*/
  /* main features */
  WP_SESSION_ITEM_FEATURE_ACTIVE       = (1 << 0),
  WP_SESSION_ITEM_FEATURE_EXPORTED     = (1 << 1),

  WP_SESSION_ITEM_FEATURE_CUSTOM_START = (1 << 16), /*< skip >*/
} WpSessionItemFeatures;

/*!
 * \brief The WpSessionItem GType
 * \ingroup wpsessionitem
 */
#define WP_TYPE_SESSION_ITEM (wp_session_item_get_type ())
WP_API
G_DECLARE_DERIVABLE_TYPE (WpSessionItem, wp_session_item,
                          WP, SESSION_ITEM, WpObject)

struct _WpSessionItemClass
{
  WpObjectClass parent_class;

  /*! See wp_session_item_reset() */
  void (*reset) (WpSessionItem * self);
  /*! See wp_session_item_configure() */
  gboolean (*configure) (WpSessionItem * self, WpProperties * props);
  /*! See wp_session_item_get_associated_proxy() */
  gpointer (*get_associated_proxy) (WpSessionItem * self, GType proxy_type);

  /*! disables the active feature of the session item */
  void (*disable_active) (WpSessionItem * self);
  /*! disables the exported feature of the session item */
  void (*disable_exported) (WpSessionItem * self);
  /*! enables the active feature of the session item */
  void (*enable_active) (WpSessionItem * self, WpTransition * transition);
  /*! enables the exported feature of the session item */
  void (*enable_exported) (WpSessionItem * self, WpTransition * transition);

  /*< private >*/
  WP_PADDING(5)
};

/* configuration */

WP_API
void wp_session_item_reset (WpSessionItem * self);

WP_API
gboolean wp_session_item_configure (WpSessionItem * self, WpProperties * props);

WP_API
gboolean wp_session_item_is_configured (WpSessionItem * self);

/* associated proxies */

WP_API
gpointer wp_session_item_get_associated_proxy (WpSessionItem * self,
    GType proxy_type);

WP_API
guint32 wp_session_item_get_associated_proxy_id (WpSessionItem * self,
    GType proxy_type);

/* registry */

WP_API
void wp_session_item_register (WpSessionItem * self);

WP_API
void wp_session_item_remove (WpSessionItem * self);

/* properties */

WP_API
WpProperties * wp_session_item_get_properties (WpSessionItem * self);

WP_API
const gchar * wp_session_item_get_property (WpSessionItem * self,
    const gchar *key);

/* for subclasses only */

WP_API
void wp_session_item_set_properties (WpSessionItem * self, WpProperties *props);

WP_API
void wp_session_item_handle_proxy_destroyed (WpProxy * proxy,
    WpSessionItem * item);

G_END_DECLS

#endif
