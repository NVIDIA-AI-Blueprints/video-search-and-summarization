/* WirePlumber
 *
 * Copyright © 2022 Collabora Ltd.
 *    @author Ashok Sidipotu <ashok.sidipotu@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_SETTINGS_H__
#define __WIREPLUMBER_SETTINGS_H__

#include "object.h"
#include "spa-json.h"

#define WP_SETTINGS_SCHEMA_METADATA_NAME_PREFIX "schema-"
#define WP_SETTINGS_PERSISTENT_METADATA_NAME_PREFIX "persistent-"

G_BEGIN_DECLS

/*!
 * \brief The different spec types of a setting
 * \ingroup wpsettings
 */
typedef enum {
  WP_SETTINGS_SPEC_TYPE_UNKNOWN,
  WP_SETTINGS_SPEC_TYPE_BOOL,
  WP_SETTINGS_SPEC_TYPE_INT,
  WP_SETTINGS_SPEC_TYPE_FLOAT,
  WP_SETTINGS_SPEC_TYPE_STRING,
  WP_SETTINGS_SPEC_TYPE_ARRAY,
  WP_SETTINGS_SPEC_TYPE_OBJECT,
} WpSettingsSpecType;

typedef struct _WpSettingsSpec WpSettingsSpec;

/*!
 * \brief The WpSettingsSpec GType
 * \ingroup wpsettings
 */
#define WP_TYPE_SETTINGS_SPEC (wp_settings_spec_get_type ())
WP_API
GType wp_settings_spec_get_type (void);

WP_API
WpSettingsSpec *wp_settings_spec_ref (WpSettingsSpec * self);

WP_API
void wp_settings_spec_unref (WpSettingsSpec * self);

WP_API
const gchar * wp_settings_spec_get_name (WpSettingsSpec * self);

WP_API
const gchar * wp_settings_spec_get_description (WpSettingsSpec * self);

WP_API
WpSettingsSpecType wp_settings_spec_get_value_type (WpSettingsSpec * self);

WP_API
WpSpaJson * wp_settings_spec_get_default_value (WpSettingsSpec * self);

WP_API
WpSpaJson * wp_settings_spec_get_min_value (WpSettingsSpec * self);

WP_API
WpSpaJson * wp_settings_spec_get_max_value (WpSettingsSpec * self);

WP_API
gboolean wp_settings_spec_check_value (WpSettingsSpec * self, WpSpaJson *value);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpSettingsSpec, wp_settings_spec_unref)

/*!
 * \brief The WpSettingsItem GType
 * \ingroup wpsettings
 */
#define WP_TYPE_SETTINGS_ITEM (wp_settings_item_get_type ())
WP_API
GType wp_settings_item_get_type (void);

typedef struct _WpSettingsItem WpSettingsItem;

WP_API
WpSettingsItem *wp_settings_item_ref (WpSettingsItem *self);

WP_API
void wp_settings_item_unref (WpSettingsItem *self);

WP_API
const gchar * wp_settings_item_get_key (WpSettingsItem * self);

WP_API
WpSpaJson * wp_settings_item_get_value (WpSettingsItem * self);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpSettingsItem, wp_settings_item_unref)

/*!
 * \brief Flags to be used as WpObjectFeatures on WpSettings subclasses.
 * \ingroup wpsettings
 */
typedef enum { /*< flags >*/
  /*! Loads the settings */
  WP_SETTINGS_LOADED = (1 << 0),
} WpSettingsFeatures;

/*!
 * \brief The WpSettings GType
 * \ingroup wpsettings
 */
#define WP_TYPE_SETTINGS (wp_settings_get_type ())

WP_API
G_DECLARE_FINAL_TYPE (WpSettings, wp_settings, WP, SETTINGS, WpObject)

WP_API
WpSettings * wp_settings_new (WpCore * core, const gchar * metadata_name);

WP_API
WpSettings * wp_settings_find (WpCore * core, const gchar * metadata_name);

/*!
 * \brief callback conveying the changed setting and its json value
 *
 * \ingroup wpsettings
 * \param obj the wpsettings object
 * \param setting the changed setting
 * \param value json value of the the changed setting
 * \param user_data data passed in the \a wp_settings_subscribe
 */
typedef void (*WpSettingsChangedCallback) (WpSettings *obj,
    const gchar *setting, WpSpaJson *value, gpointer user_data);

WP_API
guintptr wp_settings_subscribe (WpSettings *self,
    const gchar *pattern, WpSettingsChangedCallback callback,
    gpointer user_data);

WP_API
guintptr wp_settings_subscribe_closure (WpSettings *self,
    const gchar *pattern,  GClosure * closure);

WP_API
gboolean wp_settings_unsubscribe (WpSettings *self,
    guintptr subscription_id);

WP_API
WpSpaJson * wp_settings_get (WpSettings *self, const gchar *name);

WP_API
WpSpaJson * wp_settings_get_saved (WpSettings *self, const gchar *name);

WP_API
WpSettingsSpec * wp_settings_get_spec (WpSettings *self, const gchar *name);

WP_API
gboolean wp_settings_set (WpSettings *self, const gchar *name,
    WpSpaJson *value);

WP_API
gboolean wp_settings_reset (WpSettings *self, const char *name);

WP_API
gboolean wp_settings_save (WpSettings *self, const char *name);

WP_API
gboolean wp_settings_delete (WpSettings *self, const char *name);

WP_API
void wp_settings_reset_all (WpSettings *self);

WP_API
void wp_settings_save_all (WpSettings *self);

WP_API
void wp_settings_delete_all (WpSettings *self);

WP_API
WpIterator * wp_settings_new_iterator (WpSettings *self);

G_END_DECLS

#endif
