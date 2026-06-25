/* WirePlumber
 *
 * Copyright © 2019 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_PROPERTIES_H__
#define __WIREPLUMBER_PROPERTIES_H__

#include <glib-object.h>
#include "defs.h"
#include "iterator.h"
#include "spa-json.h"

G_BEGIN_DECLS

struct pw_properties;
struct spa_dict;

/*!
 * \brief The WpProperties GType
 * \ingroup wpproperties
 */
#define WP_TYPE_PROPERTIES (wp_properties_get_type ())
WP_API
GType wp_properties_get_type (void);

typedef struct _WpProperties WpProperties;

WP_API
WpProperties * wp_properties_new_empty (void);

WP_API
WpProperties * wp_properties_new (const gchar * key, ...) G_GNUC_NULL_TERMINATED;

WP_API
WpProperties * wp_properties_new_valist (const gchar * key, va_list args);

WP_API
WpProperties * wp_properties_new_string (const gchar * str);

WP_API
WpProperties * wp_properties_new_json (const WpSpaJson * json);

WP_API
WpProperties * wp_properties_new_wrap (const struct pw_properties * props);

WP_API
WpProperties * wp_properties_new_take (struct pw_properties * props);

WP_API
WpProperties * wp_properties_new_copy (const struct pw_properties * props);

WP_API
WpProperties * wp_properties_new_wrap_dict (const struct spa_dict * dict);

WP_API
WpProperties * wp_properties_new_copy_dict (const struct spa_dict * dict);

WP_API
WpProperties * wp_properties_copy (WpProperties * other);

/* ref counting */

WP_API
WpProperties * wp_properties_ref (WpProperties * self);

WP_API
void wp_properties_unref (WpProperties * self);

WP_API
WpProperties * wp_properties_ensure_unique_owner (WpProperties * self);

/* update */

WP_API
gint wp_properties_update (WpProperties * self, WpProperties * props);

WP_API
gint wp_properties_update_from_dict (WpProperties * self,
    const struct spa_dict * dict);

WP_API
gint wp_properties_update_from_json (WpProperties * self, const WpSpaJson * json);

/* add */

WP_API
gint wp_properties_add (WpProperties * self, WpProperties * props);

WP_API
gint wp_properties_add_from_dict (WpProperties * self,
    const struct spa_dict * dict);

/* update keys */

WP_API
gint wp_properties_update_keys (WpProperties * self, WpProperties * props,
    const gchar * key1, ...) G_GNUC_NULL_TERMINATED;

WP_API
gint wp_properties_update_keys_from_dict (WpProperties * self,
    const struct spa_dict * dict, const gchar * key1, ...) G_GNUC_NULL_TERMINATED;

WP_API
gint wp_properties_update_keys_array (WpProperties * self, WpProperties * props,
    const gchar * keys[]);

/* add keys */

WP_API
gint wp_properties_add_keys (WpProperties * self, WpProperties * props,
    const gchar * key1, ...) G_GNUC_NULL_TERMINATED;

WP_API
gint wp_properties_add_keys_from_dict (WpProperties * self,
    const struct spa_dict * dict, const gchar * key1, ...) G_GNUC_NULL_TERMINATED;

WP_API
gint wp_properties_add_keys_array (WpProperties * self, WpProperties * props,
    const gchar * keys[]);

/* get/set */

WP_API
const gchar * wp_properties_get (WpProperties * self, const gchar * key);

WP_API
gint wp_properties_set (WpProperties * self, const gchar * key,
    const gchar * value);

WP_API
gint wp_properties_setf (WpProperties * self, const gchar * key,
    const gchar * format, ...) G_GNUC_PRINTF(3, 4);

WP_API
gint wp_properties_setf_valist (WpProperties * self, const gchar * key,
    const gchar * format, va_list args) G_GNUC_PRINTF(3, 0);

/* iterate */

#define WP_TYPE_PROPERTIES_ITEM (wp_properties_item_get_type ())
WP_API
GType wp_properties_item_get_type (void);

typedef struct _WpPropertiesItem WpPropertiesItem;

WP_API
WpPropertiesItem *wp_properties_item_ref (WpPropertiesItem *self);

WP_API
void wp_properties_item_unref (WpPropertiesItem *self);

WP_API
const gchar * wp_properties_item_get_key (WpPropertiesItem * self);

WP_API
const gchar * wp_properties_item_get_value (WpPropertiesItem * self);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpPropertiesItem, wp_properties_item_unref)

WP_API
WpIterator * wp_properties_new_iterator (WpProperties * self);

/* count */

WP_API
guint wp_properties_get_count (WpProperties * self);


/* sort */

WP_API
void wp_properties_sort (WpProperties * self);

/* convert */

WP_API
const struct spa_dict * wp_properties_peek_dict (WpProperties * self);

WP_API
struct pw_properties * wp_properties_to_pw_properties (WpProperties * self);

WP_API
struct pw_properties * wp_properties_unref_and_take_pw_properties (
    WpProperties * self);

/* comparison */

WP_API
gboolean wp_properties_matches (WpProperties * self, WpProperties *other);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpProperties, wp_properties_unref)

G_END_DECLS

#endif
