/* WirePlumber
 *
 * Copyright © 2019 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_CORE_H__
#define __WIREPLUMBER_CORE_H__

#include "object.h"
#include "properties.h"
#include "spa-json.h"
#include "conf.h"

G_BEGIN_DECLS

struct pw_context;
struct pw_core;
typedef struct _WpObjectManager WpObjectManager;

/*!
 * \brief Flags to be used as WpObjectFeatures on WpCore
 * \ingroup wpcore
 */
typedef enum { /*< flags >*/
  /*! connects to pipewire */
  WP_CORE_FEATURE_CONNECTED = (1 << 0),
  /*! loads components defined in the configuration */
  WP_CORE_FEATURE_COMPONENTS = (1 << 1),
} WpCoreFeatures;

/*!
 * \brief The WpCore GType
 * \ingroup wpcore
 */
#define WP_TYPE_CORE (wp_core_get_type ())
WP_API
G_DECLARE_FINAL_TYPE (WpCore, wp_core, WP, CORE, WpObject)

/* Basic */

WP_API
WpCore * wp_core_new (GMainContext * context, WpConf * conf,
    WpProperties * properties);

WP_API
WpCore * wp_core_clone (WpCore * self);

WP_API
WpCore * wp_core_get_export_core (WpCore * self);

WP_API
WpConf * wp_core_get_conf (WpCore * self);

WP_API
GMainContext * wp_core_get_g_main_context (WpCore * self);

WP_API
struct pw_context * wp_core_get_pw_context (WpCore * self);

WP_API
struct pw_core * wp_core_get_pw_core (WpCore * self);

WP_API
gchar *wp_core_get_vm_type (WpCore *self);

/* Connection */

WP_API
gboolean wp_core_connect (WpCore *self);

WP_API
gboolean wp_core_connect_fd (WpCore *self, int fd);

WP_API
void wp_core_disconnect (WpCore *self);

WP_API
gboolean wp_core_is_connected (WpCore * self);

/* Properties */

WP_API
guint32 wp_core_get_own_bound_id (WpCore * self);

WP_API
guint32 wp_core_get_remote_cookie (WpCore * self);

WP_API
const gchar * wp_core_get_remote_name (WpCore * self);

WP_API
const gchar * wp_core_get_remote_user_name (WpCore * self);

WP_API
const gchar * wp_core_get_remote_host_name (WpCore * self);

WP_API
const gchar * wp_core_get_remote_version (WpCore * self);

WP_API
WpProperties * wp_core_get_remote_properties (WpCore * self);

WP_API
WpProperties * wp_core_get_properties (WpCore * self);

WP_API
void wp_core_update_properties (WpCore * self, WpProperties * updates);

/* Callback */

WP_API
void wp_core_idle_add (WpCore * self, GSource **source, GSourceFunc function,
    gpointer data, GDestroyNotify destroy);

WP_API
void wp_core_idle_add_closure (WpCore * self, GSource **source,
    GClosure * closure);

WP_API
void wp_core_timeout_add (WpCore * self, GSource **source, guint timeout_ms,
    GSourceFunc function, gpointer data, GDestroyNotify destroy);

WP_API
void wp_core_timeout_add_closure (WpCore * self, GSource **source,
    guint timeout_ms, GClosure * closure);

WP_API
gboolean wp_core_sync (WpCore * self, GCancellable * cancellable,
    GAsyncReadyCallback callback, gpointer user_data);

WP_API
gboolean wp_core_sync_closure (WpCore * self, GCancellable * cancellable,
    GClosure * closure);

WP_API
gboolean wp_core_sync_finish (WpCore * self, GAsyncResult * res,
    GError ** error);

/* Object Registry */

WP_API
gpointer wp_core_find_object (WpCore * self, GEqualFunc func,
    gconstpointer data);

WP_API
void wp_core_register_object (WpCore * self, gpointer obj);

WP_API
void wp_core_remove_object (WpCore * self, gpointer obj);

/* Object Manager */

WP_API
void wp_core_install_object_manager (WpCore * self, WpObjectManager * om);

/* Global Features */

WP_API
gboolean wp_core_test_feature (WpCore * self, const gchar * feature);

G_END_DECLS

#endif
