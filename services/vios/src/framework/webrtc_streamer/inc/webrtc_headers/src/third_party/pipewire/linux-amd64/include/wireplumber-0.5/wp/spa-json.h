/* WirePlumber
 *
 * Copyright © 2022 Collabora Ltd.
 *    @author Julian Bouzas <julian.bouzas@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_SPA_JSON_H__
#define __WIREPLUMBER_SPA_JSON_H__

#include <gio/gio.h>

#include "defs.h"
#include "iterator.h"

G_BEGIN_DECLS

struct spa_json;

/*!
 * \brief The WpSpaJson GType
 * \ingroup wpspajson
 */
#define WP_TYPE_SPA_JSON (wp_spa_json_get_type ())
WP_API
GType wp_spa_json_get_type (void);

typedef struct _WpSpaJson WpSpaJson;

WP_API
WpSpaJson *wp_spa_json_ref (WpSpaJson *self);

WP_API
void wp_spa_json_unref (WpSpaJson *self);

WP_API
WpSpaJson * wp_spa_json_new_from_string (const gchar *json_str);

WP_API
WpSpaJson * wp_spa_json_new_from_stringn (const gchar *json_str, size_t len);

WP_API
WpSpaJson * wp_spa_json_new_wrap_string (const gchar *json_str);

WP_API
WpSpaJson * wp_spa_json_new_wrap_stringn (const gchar *json_str, size_t len);

WP_API
WpSpaJson * wp_spa_json_new_wrap (struct spa_json *json);

WP_API
const struct spa_json * wp_spa_json_get_spa_json (const WpSpaJson *self);

WP_API
const gchar * wp_spa_json_get_data (const WpSpaJson *self);

WP_API
size_t wp_spa_json_get_size (const WpSpaJson *self);

WP_API
gchar * wp_spa_json_to_string (const WpSpaJson *self);

WP_API
WpSpaJson *wp_spa_json_copy (WpSpaJson *other);

WP_API
gboolean wp_spa_json_is_unique_owner (WpSpaJson *self);

WP_API
WpSpaJson *wp_spa_json_ensure_unique_owner (WpSpaJson *self);

WP_API
WpSpaJson *wp_spa_json_new_null (void);

WP_API
WpSpaJson *wp_spa_json_new_boolean (gboolean value);

WP_API
WpSpaJson *wp_spa_json_new_int (gint value);

WP_API
WpSpaJson *wp_spa_json_new_float (float value);

WP_API
WpSpaJson *wp_spa_json_new_string (const gchar *value);

WP_API
WpSpaJson *wp_spa_json_new_array (const gchar *format, ...)
    G_GNUC_NULL_TERMINATED;

WP_API
WpSpaJson *wp_spa_json_new_array_valist (const gchar *format, va_list args);

WP_API
WpSpaJson *wp_spa_json_new_object (const gchar *key, const gchar *format, ...)
    G_GNUC_NULL_TERMINATED;

WP_API
WpSpaJson *wp_spa_json_new_object_valist (const gchar *key, const gchar *format,
    va_list args);

WP_API
gboolean wp_spa_json_is_null (WpSpaJson *self);

WP_API
gboolean wp_spa_json_is_boolean (WpSpaJson *self);

WP_API
gboolean wp_spa_json_is_int (WpSpaJson *self);

WP_API
gboolean wp_spa_json_is_float (WpSpaJson *self);

WP_API
gboolean wp_spa_json_is_string (WpSpaJson *self);

WP_API
gboolean wp_spa_json_is_array (WpSpaJson *self);

WP_API
gboolean wp_spa_json_is_container (WpSpaJson *self);

WP_API
gboolean wp_spa_json_is_object (WpSpaJson *self);

WP_API
gboolean wp_spa_json_parse_boolean (WpSpaJson *self, gboolean *value);

WP_API
gboolean wp_spa_json_parse_int (WpSpaJson *self, gint *value);

WP_API
gboolean wp_spa_json_parse_float (WpSpaJson *self, float *value);

WP_API
gchar *wp_spa_json_parse_string (WpSpaJson *self);

WP_API
gboolean wp_spa_json_parse_array (WpSpaJson *self, ...) G_GNUC_NULL_TERMINATED;

WP_API
gboolean wp_spa_json_parse_array_valist (WpSpaJson *self, va_list args);

WP_API
gboolean wp_spa_json_parse_object (WpSpaJson *self, ...) G_GNUC_NULL_TERMINATED;

WP_API
gboolean wp_spa_json_parse_object_valist (WpSpaJson *self, va_list args);

WP_API
gboolean wp_spa_json_object_get (WpSpaJson *self, ...) G_GNUC_NULL_TERMINATED;

WP_API
gboolean wp_spa_json_object_get_valist (WpSpaJson *self, va_list args);

WP_API
WpIterator *wp_spa_json_new_iterator (WpSpaJson *self);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpSpaJson, wp_spa_json_unref)

/*!
 * \brief The WpSpaJsonBuilder GType
 * \ingroup wpspajson
 */
#define WP_TYPE_SPA_JSON_BUILDER (wp_spa_json_builder_get_type ())
WP_API
GType wp_spa_json_builder_get_type (void);

typedef struct _WpSpaJsonBuilder WpSpaJsonBuilder;

WP_API
WpSpaJsonBuilder *wp_spa_json_builder_ref (WpSpaJsonBuilder *self);

WP_API
void wp_spa_json_builder_unref (WpSpaJsonBuilder *self);

WP_API
WpSpaJsonBuilder *wp_spa_json_builder_new_array (void);

WP_API
WpSpaJsonBuilder *wp_spa_json_builder_new_object (void);

WP_API
void wp_spa_json_builder_add_property (WpSpaJsonBuilder *self, const gchar *key);

WP_API
void wp_spa_json_builder_add_null (WpSpaJsonBuilder *self);

WP_API
void wp_spa_json_builder_add_boolean (WpSpaJsonBuilder *self, gboolean value);

WP_API
void wp_spa_json_builder_add_int (WpSpaJsonBuilder *self, gint value);

WP_API
void wp_spa_json_builder_add_float (WpSpaJsonBuilder *self, float value);

WP_API
void wp_spa_json_builder_add_string (WpSpaJsonBuilder *self, const gchar *value);

WP_API
void wp_spa_json_builder_add_json (WpSpaJsonBuilder *self, WpSpaJson *json);

WP_API
void wp_spa_json_builder_add_from_string (WpSpaJsonBuilder *self,
    const gchar *json_str);

WP_API
void wp_spa_json_builder_add_from_stringn (WpSpaJsonBuilder *self,
    const gchar *json_str, size_t len);

WP_API
void wp_spa_json_builder_add (WpSpaJsonBuilder *self, ...)
    G_GNUC_NULL_TERMINATED;

WP_API
void wp_spa_json_builder_add_valist (WpSpaJsonBuilder *self, va_list args);

WP_API
WpSpaJson *wp_spa_json_builder_end (WpSpaJsonBuilder *self);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpSpaJsonBuilder, wp_spa_json_builder_unref)

/*!
 * \brief The WpSpaJsonParser GType
 * \ingroup wpspajson
 */
#define WP_TYPE_SPA_JSON_PARSER (wp_spa_json_parser_get_type ())
WP_API
GType wp_spa_json_parser_get_type (void);

typedef struct _WpSpaJsonParser WpSpaJsonParser;

WP_API
WpSpaJsonParser *wp_spa_json_parser_ref (WpSpaJsonParser *self);

WP_API
void wp_spa_json_parser_unref (WpSpaJsonParser *self);

WP_API
WpSpaJsonParser *wp_spa_json_parser_new_array (WpSpaJson *json);

WP_API
WpSpaJsonParser *wp_spa_json_parser_new_object (WpSpaJson *json);

WP_API
WpSpaJsonParser *wp_spa_json_parser_new_undefined (WpSpaJson *json);

WP_API
gboolean wp_spa_json_parser_get_null (WpSpaJsonParser *self);

WP_API
gboolean wp_spa_json_parser_get_boolean (WpSpaJsonParser *self,
    gboolean *value);

WP_API
gboolean wp_spa_json_parser_get_int (WpSpaJsonParser *self, gint *value);

WP_API
gboolean wp_spa_json_parser_get_float (WpSpaJsonParser *self, float *value);

WP_API
gchar *wp_spa_json_parser_get_string (WpSpaJsonParser *self);

WP_API
WpSpaJson *wp_spa_json_parser_get_json (WpSpaJsonParser *self);

WP_API
gboolean wp_spa_json_parser_get (WpSpaJsonParser *self, ...)
    G_GNUC_NULL_TERMINATED;

WP_API
gboolean wp_spa_json_parser_get_valist (WpSpaJsonParser *self, va_list args);

WP_API
void wp_spa_json_parser_end (WpSpaJsonParser *self);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpSpaJsonParser, wp_spa_json_parser_unref)

G_END_DECLS

#endif
