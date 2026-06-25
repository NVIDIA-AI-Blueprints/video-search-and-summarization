/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author Julian Bouzas <julian.bouzas@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_SPA_TYPE_H__
#define __WIREPLUMBER_SPA_TYPE_H__

#include "defs.h"
#include "iterator.h"

G_BEGIN_DECLS

/*!
 * \ingroup wpspatype
 */
typedef guint32 WpSpaType;
/*!
 * \ingroup wpspatype
 */
typedef gconstpointer WpSpaIdTable;
/*!
 * \ingroup wpspatype
 */
typedef gconstpointer WpSpaIdValue;
struct spa_type_info;

/* WpSpaType */

/*!
 * \brief The WpSpaType GType
 * \ingroup wpspatype
 */
#define WP_TYPE_SPA_TYPE (wp_spa_type_get_type ())
WP_API
GType wp_spa_type_get_type (void);

/*!
 * \brief Type id representing an invalid SPA type
 * \ingroup wpspatype
 */
#define WP_SPA_TYPE_INVALID 0xffffffff

WP_API
WpSpaType wp_spa_type_from_name (const gchar *name);

WP_API
WpSpaType wp_spa_type_parent (WpSpaType type);

WP_API
const gchar * wp_spa_type_name (WpSpaType type);

WP_API
gboolean wp_spa_type_is_fundamental (WpSpaType type);

WP_API
gboolean wp_spa_type_is_id (WpSpaType type);

WP_API
gboolean wp_spa_type_is_object (WpSpaType type);

WP_API
WpSpaIdTable wp_spa_type_get_object_id_values_table (WpSpaType type);

WP_API
WpSpaIdTable wp_spa_type_get_values_table (WpSpaType type);

/*!
 * \brief The WpSpaIdTable GType
 * \ingroup wpspatype
 */
#define WP_TYPE_SPA_ID_TABLE (wp_spa_id_table_get_type ())
WP_API
GType wp_spa_id_table_get_type (void);

WP_API
WpSpaIdTable wp_spa_id_table_from_name (const gchar *name);

WP_API
WpIterator * wp_spa_id_table_new_iterator (WpSpaIdTable table);

WP_API
WpSpaIdValue wp_spa_id_table_find_value (WpSpaIdTable table, guint value);

WP_API
WpSpaIdValue wp_spa_id_table_find_value_from_name (WpSpaIdTable table,
    const gchar * name);

WP_API
WpSpaIdValue wp_spa_id_table_find_value_from_short_name (WpSpaIdTable table,
    const gchar * short_name);

/*!
 * \brief The WpSpaIdValue GType
 * \ingroup wpspatype
 */
#define WP_TYPE_SPA_ID_VALUE (wp_spa_id_value_get_type ())
WP_API
GType wp_spa_id_value_get_type (void);

WP_API
WpSpaIdValue wp_spa_id_value_from_name (const gchar * name);

WP_API
WpSpaIdValue wp_spa_id_value_from_short_name (const gchar * table_name,
    const gchar * short_name);

WP_API
WpSpaIdValue wp_spa_id_value_from_number (const gchar * table_name, guint id);

WP_API
guint wp_spa_id_value_number (WpSpaIdValue id);

WP_API
const gchar * wp_spa_id_value_name (WpSpaIdValue id);

WP_API
const gchar * wp_spa_id_value_short_name (WpSpaIdValue id);

WP_API
WpSpaType wp_spa_id_value_get_value_type (WpSpaIdValue id, WpSpaIdTable *table);

WP_API
WpSpaType wp_spa_id_value_array_get_item_type (WpSpaIdValue id,
    WpSpaIdTable *table);


/* Dynamic type registration */

WP_API
void wp_spa_dynamic_type_init (void);

WP_API
void wp_spa_dynamic_type_deinit (void);

WP_API
WpSpaType wp_spa_dynamic_type_register (const gchar *name, WpSpaType parent,
    const struct spa_type_info * values);

WP_API
WpSpaIdTable wp_spa_dynamic_id_table_register (const gchar *name,
    const struct spa_type_info * values);

G_END_DECLS

#endif
