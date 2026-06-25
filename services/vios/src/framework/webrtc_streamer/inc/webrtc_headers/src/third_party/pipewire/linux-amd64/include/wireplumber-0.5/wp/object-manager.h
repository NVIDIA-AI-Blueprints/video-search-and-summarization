/* WirePlumber
 *
 * Copyright © 2019 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_OBJECT_MANAGER_H__
#define __WIREPLUMBER_OBJECT_MANAGER_H__

#include <glib-object.h>
#include "object.h"
#include "iterator.h"
#include "object-interest.h"

G_BEGIN_DECLS

/*!
 * \brief The WpObjectManager GType
 * \ingroup wpobjectmanager
 */
#define WP_TYPE_OBJECT_MANAGER (wp_object_manager_get_type ())
WP_API
G_DECLARE_FINAL_TYPE (WpObjectManager, wp_object_manager, WP, OBJECT_MANAGER, GObject)

WP_API
WpObjectManager * wp_object_manager_new (void);

/* installation */

WP_API
gboolean wp_object_manager_is_installed (WpObjectManager * self);

/* interest */

WP_API
void wp_object_manager_add_interest (WpObjectManager * self,
    GType gtype, ...) G_GNUC_NULL_TERMINATED;

WP_API
void wp_object_manager_add_interest_full (WpObjectManager * self,
    WpObjectInterest * interest);

/* object features */

WP_API
void wp_object_manager_request_object_features (WpObjectManager *self,
    GType object_type, WpObjectFeatures wanted_features);

/* object inspection */

WP_API
guint wp_object_manager_get_n_objects (WpObjectManager * self);

WP_API
WpIterator * wp_object_manager_new_iterator (WpObjectManager * self);

WP_API
WpIterator * wp_object_manager_new_filtered_iterator (WpObjectManager * self,
    GType gtype, ...);

WP_API
WpIterator * wp_object_manager_new_filtered_iterator_full (
    WpObjectManager * self, WpObjectInterest * interest);

WP_API
gpointer wp_object_manager_lookup (WpObjectManager * self,
    GType gtype, ...) G_GNUC_NULL_TERMINATED;

WP_API
gpointer wp_object_manager_lookup_full (WpObjectManager * self,
    WpObjectInterest * interest);

/* private */

typedef struct _WpGlobal WpGlobal;

WP_PRIVATE_API
void wp_object_manager_maybe_objects_changed (WpObjectManager * self);

WP_PRIVATE_API
void wp_object_manager_add_object (WpObjectManager * self, gpointer object);

WP_PRIVATE_API
void wp_object_manager_rm_object (WpObjectManager * self, gpointer object);

WP_PRIVATE_API
void wp_object_manager_add_global (WpObjectManager * self, WpGlobal * global);

G_END_DECLS

#endif
