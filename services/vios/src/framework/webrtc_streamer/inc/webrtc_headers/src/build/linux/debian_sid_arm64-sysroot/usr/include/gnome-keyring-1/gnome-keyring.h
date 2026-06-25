/* -*- Mode: C; indent-tabs-mode: t; c-basic-offset: 8; tab-width: 8 -*- */
/* gnome-keyring.h - library for talking to the keyring daemon.

   Copyright (C) 2003 Red Hat, Inc

   The Gnome Keyring Library is free software; you can redistribute it and/or
   modify it under the terms of the GNU Library General Public License as
   published by the Free Software Foundation; either version 2 of the
   License, or (at your option) any later version.

   The Gnome Keyring Library is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
   Library General Public License for more details.

   You should have received a copy of the GNU Library General Public
   License along with the Gnome Library; see the file COPYING.LIB.  If not,
   write to the Free Software Foundation, Inc., 59 Temple Place - Suite 330,
   Boston, MA 02111-1307, USA.

   Author: Alexander Larsson <alexl@redhat.com>
*/

#ifndef GNOME_KEYRING_H
#define GNOME_KEYRING_H

#include <glib.h>
#include <glib-object.h>
#include <time.h>

#if !defined(GNOME_KEYRING_DEPRECATED)
#if !defined(GNOME_KEYRING_COMPILATION) && defined(G_DEPRECATED)
#define GNOME_KEYRING_DEPRECATED G_DEPRECATED
#define GNOME_KEYRING_DEPRECATED_FOR(x) G_DEPRECATED_FOR(x)
#else
#define GNOME_KEYRING_DEPRECATED
#define GNOME_KEYRING_DEPRECATED_FOR(x)
#endif
#endif

#include "gnome-keyring-result.h"

G_BEGIN_DECLS

#ifndef GNOME_KEYRING_DISABLE_DEPRECATED

#define GNOME_KEYRING_SESSION   "session"
#define GNOME_KEYRING_DEFAULT   NULL

typedef enum {

	/* The item types */
	GNOME_KEYRING_ITEM_GENERIC_SECRET = 0,
	GNOME_KEYRING_ITEM_NETWORK_PASSWORD,
	GNOME_KEYRING_ITEM_NOTE,
	GNOME_KEYRING_ITEM_CHAINED_KEYRING_PASSWORD,
	GNOME_KEYRING_ITEM_ENCRYPTION_KEY_PASSWORD,

	GNOME_KEYRING_ITEM_PK_STORAGE = 0x100,

	/* Not used, remains here only for compatibility */
	GNOME_KEYRING_ITEM_LAST_TYPE,

} GnomeKeyringItemType;

#define	GNOME_KEYRING_ITEM_TYPE_MASK 0x0000ffff
#define GNOME_KEYRING_ITEM_NO_TYPE GNOME_KEYRING_ITEM_TYPE_MASK
#define	GNOME_KEYRING_ITEM_APPLICATION_SECRET 0x01000000

typedef enum {
	GNOME_KEYRING_ATTRIBUTE_TYPE_STRING,
	GNOME_KEYRING_ATTRIBUTE_TYPE_UINT32
} GnomeKeyringAttributeType;

typedef GArray GnomeKeyringAttributeList;

typedef enum {
	GNOME_KEYRING_ITEM_INFO_BASICS = 0,
	GNOME_KEYRING_ITEM_INFO_SECRET = 1<<0
} GnomeKeyringItemInfoFlags;

/* Add flags here as they are added above */
#define GNOME_KEYRING_ITEM_INFO_ALL (GNOME_KEYRING_ITEM_INFO_BASICS | GNOME_KEYRING_ITEM_INFO_SECRET)

typedef struct GnomeKeyringInfo GnomeKeyringInfo;
typedef struct GnomeKeyringItemInfo GnomeKeyringItemInfo;

typedef struct {
	char *name;
	GnomeKeyringAttributeType type;
	union {
		char *string;
		guint32 integer;
	} value;
} GnomeKeyringAttribute;

typedef struct {
	char *keyring;
	guint item_id;
	GnomeKeyringAttributeList *attributes;
	char *secret;
} GnomeKeyringFound;

GNOME_KEYRING_DEPRECATED
void gnome_keyring_string_list_free (GList *strings);

typedef void (*GnomeKeyringOperationDoneCallback)           (GnomeKeyringResult result,
                                                             gpointer           user_data);
/**
 * GnomeKeyringOperationGetStringCallback:
 * @result: result of the operation
 * @string: (allow-none): the string, or %NULL
 * @user_data: user data
 */
typedef void (*GnomeKeyringOperationGetStringCallback)      (GnomeKeyringResult result,
                                                             const char        *string,
                                                             gpointer           user_data);
typedef void (*GnomeKeyringOperationGetIntCallback)         (GnomeKeyringResult result,
                                                             guint32            val,
                                                             gpointer           user_data);
typedef void (*GnomeKeyringOperationGetListCallback)        (GnomeKeyringResult result,
                                                             GList             *list,
                                                             gpointer           user_data);
typedef void (*GnomeKeyringOperationGetKeyringInfoCallback) (GnomeKeyringResult result,
                                                             GnomeKeyringInfo  *info,
                                                             gpointer           user_data);
typedef void (*GnomeKeyringOperationGetItemInfoCallback)    (GnomeKeyringResult   result,
                                                             GnomeKeyringItemInfo*info,
                                                             gpointer             user_data);
typedef void (*GnomeKeyringOperationGetAttributesCallback)  (GnomeKeyringResult   result,
                                                             GnomeKeyringAttributeList *attributes,
                                                             gpointer             user_data);

GNOME_KEYRING_DEPRECATED
GType                      gnome_keyring_attribute_get_type           (void) G_GNUC_CONST;
GNOME_KEYRING_DEPRECATED
const gchar*               gnome_keyring_attribute_get_string         (GnomeKeyringAttribute *attribute);
GNOME_KEYRING_DEPRECATED
guint32                    gnome_keyring_attribute_get_uint32         (GnomeKeyringAttribute *attribute);

#define GNOME_KEYRING_TYPE_ATTRIBUTE (gnome_keyring_attribute_get_type ())

#define gnome_keyring_attribute_list_index(a, i) g_array_index ((a), GnomeKeyringAttribute, (i))
GNOME_KEYRING_DEPRECATED_FOR(g_hash_table_replace)
void                       gnome_keyring_attribute_list_append_string (GnomeKeyringAttributeList *attributes,
                                                                       const char                *name,
                                                                       const char                *value);
GNOME_KEYRING_DEPRECATED
void                       gnome_keyring_attribute_list_append_uint32 (GnomeKeyringAttributeList *attributes,
                                                                       const char                *name,
                                                                       guint32                    value);
GNOME_KEYRING_DEPRECATED_FOR(g_hash_table_new)
GnomeKeyringAttributeList *gnome_keyring_attribute_list_new           (void);
GNOME_KEYRING_DEPRECATED_FOR(g_hash_table_unref)
void                       gnome_keyring_attribute_list_free          (GnomeKeyringAttributeList *attributes);
GNOME_KEYRING_DEPRECATED
GnomeKeyringAttributeList *gnome_keyring_attribute_list_copy          (GnomeKeyringAttributeList *attributes);
GNOME_KEYRING_DEPRECATED
GType                      gnome_keyring_attribute_list_get_type      (void) G_GNUC_CONST;
GNOME_KEYRING_DEPRECATED
GList                     *gnome_keyring_attribute_list_to_glist      (GnomeKeyringAttributeList *attributes);

#define GNOME_KEYRING_TYPE_ATTRIBUTE_LIST (gnome_keyring_attribute_list_get_type ())

GNOME_KEYRING_DEPRECATED
const gchar*               gnome_keyring_result_to_message            (GnomeKeyringResult res);

GNOME_KEYRING_DEPRECATED
gboolean gnome_keyring_is_available (void);

GNOME_KEYRING_DEPRECATED
void gnome_keyring_found_free               (GnomeKeyringFound *found);
GNOME_KEYRING_DEPRECATED
void gnome_keyring_found_list_free          (GList *found_list);
GNOME_KEYRING_DEPRECATED
GnomeKeyringFound* gnome_keyring_found_copy (GnomeKeyringFound *found);
GNOME_KEYRING_DEPRECATED
GType gnome_keyring_found_get_type          (void) G_GNUC_CONST;

#define GNOME_KEYRING_TYPE_FOUND (gnome_keyring_found_get_type ())

GNOME_KEYRING_DEPRECATED
void gnome_keyring_cancel_request (gpointer request);

GNOME_KEYRING_DEPRECATED_FOR(secret_service_set_alias)
gpointer           gnome_keyring_set_default_keyring      (const char                              *keyring,
                                                           GnomeKeyringOperationDoneCallback        callback,
                                                           gpointer                                 data,
                                                           GDestroyNotify                           destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_set_alias_sync)
GnomeKeyringResult gnome_keyring_set_default_keyring_sync (const char                              *keyring);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_for_alias)
gpointer           gnome_keyring_get_default_keyring      (GnomeKeyringOperationGetStringCallback   callback,
                                                           gpointer                                 data,
                                                           GDestroyNotify                           destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_for_alias_sync)
GnomeKeyringResult gnome_keyring_get_default_keyring_sync (char                                   **keyring);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_get_collections)
gpointer           gnome_keyring_list_keyring_names       (GnomeKeyringOperationGetListCallback     callback,
                                                           gpointer                                 data,
                                                           GDestroyNotify                           destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_get_collections)
GnomeKeyringResult gnome_keyring_list_keyring_names_sync  (GList                                  **keyrings);
GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_lock_all                 (GnomeKeyringOperationDoneCallback        callback,
                                                           gpointer                                 data,
                                                           GDestroyNotify                           destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_lock_all_sync            (void);


/* NULL password means ask user */
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_create)
gpointer           gnome_keyring_create             (const char                                   *keyring_name,
                                                     const char                                   *password,
                                                     GnomeKeyringOperationDoneCallback             callback,
                                                     gpointer                                      data,
                                                     GDestroyNotify                                destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_create_sync)
GnomeKeyringResult gnome_keyring_create_sync        (const char                                   *keyring_name,
                                                     const char                                   *password);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_unlock)
gpointer           gnome_keyring_unlock             (const char                                   *keyring,
                                                     const char                                   *password,
                                                     GnomeKeyringOperationDoneCallback             callback,
                                                     gpointer                                      data,
                                                     GDestroyNotify                                destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_unlock_sync)
GnomeKeyringResult gnome_keyring_unlock_sync        (const char                                   *keyring,
                                                     const char                                   *password);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_lock)
gpointer           gnome_keyring_lock               (const char                                   *keyring,
                                                     GnomeKeyringOperationDoneCallback             callback,
                                                     gpointer                                      data,
                                                     GDestroyNotify                                destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_lock_sync)
GnomeKeyringResult gnome_keyring_lock_sync          (const char                                   *keyring);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_delete)
gpointer           gnome_keyring_delete             (const char                                   *keyring,
                                                     GnomeKeyringOperationDoneCallback             callback,
                                                     gpointer                                      data,
                                                     GDestroyNotify                                destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_delete_sync)
GnomeKeyringResult gnome_keyring_delete_sync        (const char                                   *keyring);
GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_change_password             (const char                                   *keyring,
                                                     const char                                   *original,
                                                     const char                                   *password,
                                                     GnomeKeyringOperationDoneCallback             callback,
                                                     gpointer                                      data,
                                                     GDestroyNotify                                destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_change_password_sync        (const char                                   *keyring,
                                                         const char                                                        	   *original,
                                                     const char                                   *password);
GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_get_info           (const char                                   *keyring,
                                                     GnomeKeyringOperationGetKeyringInfoCallback   callback,
                                                     gpointer                                      data,
                                                     GDestroyNotify                                destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_get_info_sync      (const char                                   *keyring,
                                                     GnomeKeyringInfo                            **info);
GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_set_info           (const char                                   *keyring,
                                                     GnomeKeyringInfo                             *info,
                                                     GnomeKeyringOperationDoneCallback             callback,
                                                     gpointer                                      data,
                                                     GDestroyNotify                                destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_set_info_sync      (const char                                   *keyring,
                                                     GnomeKeyringInfo                             *info);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_get_items)
gpointer           gnome_keyring_list_item_ids      (const char                                   *keyring,
                                                     GnomeKeyringOperationGetListCallback          callback,
                                                     gpointer                                      data,
                                                     GDestroyNotify                                destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_get_items)
GnomeKeyringResult gnome_keyring_list_item_ids_sync (const char                                   *keyring,
                                                     GList                                       **ids);

GNOME_KEYRING_DEPRECATED
void              gnome_keyring_info_free             (GnomeKeyringInfo *keyring_info);
GNOME_KEYRING_DEPRECATED
GnomeKeyringInfo *gnome_keyring_info_copy             (GnomeKeyringInfo *keyring_info);
GNOME_KEYRING_DEPRECATED
GType             gnome_keyring_info_get_type         (void) G_GNUC_CONST;
GNOME_KEYRING_DEPRECATED
void              gnome_keyring_info_set_lock_on_idle (GnomeKeyringInfo *keyring_info,
                                                       gboolean          value);
GNOME_KEYRING_DEPRECATED
gboolean          gnome_keyring_info_get_lock_on_idle (GnomeKeyringInfo *keyring_info);
GNOME_KEYRING_DEPRECATED
void              gnome_keyring_info_set_lock_timeout (GnomeKeyringInfo *keyring_info,
                                                       guint32           value);
GNOME_KEYRING_DEPRECATED
guint32           gnome_keyring_info_get_lock_timeout (GnomeKeyringInfo *keyring_info);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_get_modified)
time_t            gnome_keyring_info_get_mtime        (GnomeKeyringInfo *keyring_info);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_get_created)
time_t            gnome_keyring_info_get_ctime        (GnomeKeyringInfo *keyring_info);
GNOME_KEYRING_DEPRECATED_FOR(secret_collection_get_locked)
gboolean          gnome_keyring_info_get_is_locked    (GnomeKeyringInfo *keyring_info);

#define GNOME_KEYRING_TYPE_INFO (gnome_keyring_info_get_type ())

GNOME_KEYRING_DEPRECATED_FOR(secret_service_search)
gpointer gnome_keyring_find_items  (GnomeKeyringItemType                  type,
                                    GnomeKeyringAttributeList            *attributes,
                                    GnomeKeyringOperationGetListCallback  callback,
                                    gpointer                              data,
                                    GDestroyNotify                        destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_search)
gpointer gnome_keyring_find_itemsv (GnomeKeyringItemType                  type,
                                    GnomeKeyringOperationGetListCallback  callback,
                                    gpointer                              data,
                                    GDestroyNotify                        destroy_data,
                                    ...);

GNOME_KEYRING_DEPRECATED_FOR(secret_service_search_sync)
GnomeKeyringResult gnome_keyring_find_items_sync  (GnomeKeyringItemType        type,
                                                   GnomeKeyringAttributeList  *attributes,
                                                   GList                     **found);
GNOME_KEYRING_DEPRECATED_FOR(secret_service_search_sync)
GnomeKeyringResult gnome_keyring_find_itemsv_sync (GnomeKeyringItemType        type,
                                                   GList                     **found,
                                                   ...);

GNOME_KEYRING_DEPRECATED_FOR(secret_item_create)
gpointer           gnome_keyring_item_create              (const char                                 *keyring,
                                                           GnomeKeyringItemType                        type,
                                                           const char                                 *display_name,
                                                           GnomeKeyringAttributeList                  *attributes,
                                                           const char                                 *secret,
                                                           gboolean                                    update_if_exists,
                                                           GnomeKeyringOperationGetIntCallback         callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_create_sync)
GnomeKeyringResult gnome_keyring_item_create_sync         (const char                                 *keyring,
                                                           GnomeKeyringItemType                        type,
                                                           const char                                 *display_name,
                                                           GnomeKeyringAttributeList                  *attributes,
                                                           const char                                 *secret,
                                                           gboolean                                    update_if_exists,
                                                           guint32                                    *item_id);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_delete)
gpointer           gnome_keyring_item_delete              (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringOperationDoneCallback           callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_delete_sync)
GnomeKeyringResult gnome_keyring_item_delete_sync         (const char                                 *keyring,
                                                           guint32                                     id);
GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_item_get_info            (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringOperationGetItemInfoCallback    callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_item_get_info_sync       (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringItemInfo                      **info);
GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_item_get_info_full       (const char                                 *keyring,
                                                           guint32                                     id,
                                                           guint32                                     flags,
                                                           GnomeKeyringOperationGetItemInfoCallback    callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_item_get_info_full_sync  (const char                                 *keyring,
                                                           guint32                                     id,
                                                           guint32                                     flags,
                                                            GnomeKeyringItemInfo                      **info);
GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_item_set_info            (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringItemInfo                       *info,
                                                           GnomeKeyringOperationDoneCallback           callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_item_set_info_sync       (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringItemInfo                       *info);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_get_attributes)
gpointer           gnome_keyring_item_get_attributes      (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringOperationGetAttributesCallback  callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_get_attributes)
GnomeKeyringResult gnome_keyring_item_get_attributes_sync (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringAttributeList                 **attributes);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_set_attributes)
gpointer           gnome_keyring_item_set_attributes      (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringAttributeList                  *attributes,
                                                           GnomeKeyringOperationDoneCallback           callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_set_attributes_sync)
GnomeKeyringResult gnome_keyring_item_set_attributes_sync (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringAttributeList                  *attributes);

GNOME_KEYRING_DEPRECATED
void                  gnome_keyring_item_info_free             (GnomeKeyringItemInfo *item_info);
GNOME_KEYRING_DEPRECATED
GnomeKeyringItemInfo *gnome_keyring_item_info_new              (void);
GNOME_KEYRING_DEPRECATED
GnomeKeyringItemInfo *gnome_keyring_item_info_copy             (GnomeKeyringItemInfo *item_info);
GNOME_KEYRING_DEPRECATED
GType                 gnome_keyring_item_info_get_gtype        (void) G_GNUC_CONST;
GNOME_KEYRING_DEPRECATED_FOR(secret_item_get_schema_name)
GnomeKeyringItemType  gnome_keyring_item_info_get_type         (GnomeKeyringItemInfo *item_info);
GNOME_KEYRING_DEPRECATED
void                  gnome_keyring_item_info_set_type         (GnomeKeyringItemInfo *item_info,
                                                                GnomeKeyringItemType  type);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_get_secret)
char *                gnome_keyring_item_info_get_secret       (GnomeKeyringItemInfo *item_info);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_set_secret)
void                  gnome_keyring_item_info_set_secret       (GnomeKeyringItemInfo *item_info,
                                                                const char           *value);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_get_label)
char *                gnome_keyring_item_info_get_display_name (GnomeKeyringItemInfo *item_info);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_set_label)
void                  gnome_keyring_item_info_set_display_name (GnomeKeyringItemInfo *item_info,
                                                                const char           *value);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_get_modified)
time_t                gnome_keyring_item_info_get_mtime        (GnomeKeyringItemInfo *item_info);
GNOME_KEYRING_DEPRECATED_FOR(secret_item_set_modified)
time_t                gnome_keyring_item_info_get_ctime        (GnomeKeyringItemInfo *item_info);

#define GNOME_KEYRING_TYPE_ITEM_INFO (gnome_keyring_item_info_get_gtype ())

/* ------------------------------------------------------------------------------
 * A Simpler API
 */

/*
 * This structure exists to help languages which have difficulty with
 * anonymous structures and is the same as the anonymous struct which
 * is defined in GnomeKeyringPasswordSchema, but it cannot be used
 * directly in GnomeKeyringPasswordSchema for API compatibility
 * reasons.
 */
typedef struct {
	const gchar* name;
	GnomeKeyringAttributeType type;
} GnomeKeyringPasswordSchemaAttribute;

typedef struct {
	GnomeKeyringItemType item_type;
	struct {
		const gchar* name;
		GnomeKeyringAttributeType type;
	} attributes[32];

	/* <private> */
	gpointer reserved1;
	gpointer reserved2;
	gpointer reserved3;
} GnomeKeyringPasswordSchema;

extern const GnomeKeyringPasswordSchema* GNOME_KEYRING_NETWORK_PASSWORD;

GNOME_KEYRING_DEPRECATED_FOR(secret_password_store)
gpointer                 gnome_keyring_store_password         (const GnomeKeyringPasswordSchema* schema,
                                                               const gchar *keyring,
                                                               const gchar *display_name,
                                                               const gchar *password,
                                                               GnomeKeyringOperationDoneCallback callback,
                                                               gpointer data,
                                                               GDestroyNotify destroy_data,
                                                               ...) G_GNUC_NULL_TERMINATED;

GNOME_KEYRING_DEPRECATED_FOR(secret_password_store_sync)
GnomeKeyringResult       gnome_keyring_store_password_sync    (const GnomeKeyringPasswordSchema* schema,
                                                               const gchar *keyring,
                                                               const gchar *display_name,
                                                               const gchar *password,
                                                               ...) G_GNUC_NULL_TERMINATED;

GNOME_KEYRING_DEPRECATED_FOR(secret_password_lookup)
gpointer                 gnome_keyring_find_password          (const GnomeKeyringPasswordSchema* schema,
                                                               GnomeKeyringOperationGetStringCallback callback,
                                                               gpointer data,
                                                               GDestroyNotify destroy_data,
                                                               ...) G_GNUC_NULL_TERMINATED;

GNOME_KEYRING_DEPRECATED_FOR(secret_password_lookup_sync)
GnomeKeyringResult       gnome_keyring_find_password_sync     (const GnomeKeyringPasswordSchema* schema,
                                                               gchar **password,
                                                               ...) G_GNUC_NULL_TERMINATED;

GNOME_KEYRING_DEPRECATED_FOR(secret_password_clear)
gpointer                 gnome_keyring_delete_password        (const GnomeKeyringPasswordSchema* schema,
                                                               GnomeKeyringOperationDoneCallback callback,
                                                               gpointer data,
                                                               GDestroyNotify destroy_data,
                                                               ...) G_GNUC_NULL_TERMINATED;

GNOME_KEYRING_DEPRECATED_FOR(secret_password_clear_sync)
GnomeKeyringResult       gnome_keyring_delete_password_sync   (const GnomeKeyringPasswordSchema* schema,
                                                               ...) G_GNUC_NULL_TERMINATED;

GNOME_KEYRING_DEPRECATED_FOR(secret_password_free)
void                     gnome_keyring_free_password          (gchar *password);

/* ------------------------------------------------------------------------------
 * Special Helpers for network password items
 */

typedef struct {
	char *keyring;
	guint32 item_id;

	char *protocol;
	char *server;
	char *object;
	char *authtype;
	guint32 port;

	char *user;
	char *domain;
	char *password;
} GnomeKeyringNetworkPasswordData;

GNOME_KEYRING_DEPRECATED
void gnome_keyring_network_password_free (GnomeKeyringNetworkPasswordData *data);
GNOME_KEYRING_DEPRECATED
void gnome_keyring_network_password_list_free (GList *list);

GNOME_KEYRING_DEPRECATED_FOR(SECRET_SCHEMA_COMPAT_NETWORK)
gpointer           gnome_keyring_find_network_password      (const char                            *user,
                                                             const char                            *domain,
                                                             const char                            *server,
                                                             const char                            *object,
                                                             const char                            *protocol,
                                                             const char                            *authtype,
                                                             guint32                                port,
                                                             GnomeKeyringOperationGetListCallback   callback,
                                                             gpointer                               data,
                                                             GDestroyNotify                         destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(SECRET_SCHEMA_COMPAT_NETWORK)
GnomeKeyringResult gnome_keyring_find_network_password_sync (const char                            *user,
                                                             const char                            *domain,
                                                             const char                            *server,
                                                             const char                            *object,
                                                             const char                            *protocol,
                                                             const char                            *authtype,
                                                             guint32                                port,
                                                             GList                                **results);
GNOME_KEYRING_DEPRECATED_FOR(SECRET_SCHEMA_COMPAT_NETWORK)
gpointer           gnome_keyring_set_network_password       (const char                            *keyring,
                                                             const char                            *user,
                                                             const char                            *domain,
                                                             const char                            *server,
                                                             const char                            *object,
                                                             const char                            *protocol,
                                                             const char                            *authtype,
                                                             guint32                                port,
                                                             const char                            *password,
                                                             GnomeKeyringOperationGetIntCallback    callback,
                                                             gpointer                               data,
                                                             GDestroyNotify                         destroy_data);
GNOME_KEYRING_DEPRECATED_FOR(SECRET_SCHEMA_COMPAT_NETWORK)
GnomeKeyringResult gnome_keyring_set_network_password_sync  (const char                            *keyring,
                                                             const char                            *user,
                                                             const char                            *domain,
                                                             const char                            *server,
                                                             const char                            *object,
                                                             const char                            *protocol,
                                                             const char                            *authtype,
                                                             guint32                                port,
                                                             const char                            *password,
                                                             guint32                               *item_id);

typedef enum {
	GNOME_KEYRING_ACCESS_ASK,
	GNOME_KEYRING_ACCESS_DENY,
	GNOME_KEYRING_ACCESS_ALLOW
} GnomeKeyringAccessRestriction;

typedef struct GnomeKeyringAccessControl GnomeKeyringAccessControl;
typedef struct GnomeKeyringApplicationRef GnomeKeyringApplicationRef;

typedef enum {
	GNOME_KEYRING_ACCESS_READ = 1<<0,
	GNOME_KEYRING_ACCESS_WRITE = 1<<1,
	GNOME_KEYRING_ACCESS_REMOVE = 1<<2
} GnomeKeyringAccessType;

GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_daemon_set_display_sync       (const char *display);

GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_daemon_prepare_environment_sync (void);

GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_item_grant_access_rights      (const gchar                       *keyring,
                                                                const gchar                       *display_name,
                                                                const gchar                       *full_path,
                                                                const guint32                      id,
                                                                const GnomeKeyringAccessType       rights,
                                                                GnomeKeyringOperationDoneCallback  callback,
                                                                gpointer                           data,
                                                                GDestroyNotify                     destroy_data);

GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_item_grant_access_rights_sync (const char                   *keyring,
                                                                const char                   *display_name,
                                                                const char                   *full_path,
                                                                const guint32                id,
                                                                const GnomeKeyringAccessType rights);


GNOME_KEYRING_DEPRECATED
GnomeKeyringApplicationRef * gnome_keyring_application_ref_new          (void);
GNOME_KEYRING_DEPRECATED
GnomeKeyringApplicationRef * gnome_keyring_application_ref_copy         (const GnomeKeyringApplicationRef *app);
GNOME_KEYRING_DEPRECATED
void                         gnome_keyring_application_ref_free         (GnomeKeyringApplicationRef       *app);
GNOME_KEYRING_DEPRECATED
GType                        gnome_keyring_application_ref_get_type     (void) G_GNUC_CONST;

#define GNOME_KEYRING_TYPE_APPLICATION_REF (gnome_keyring_application_ref_get_type ())

GNOME_KEYRING_DEPRECATED
GnomeKeyringAccessControl *  gnome_keyring_access_control_new  (const GnomeKeyringApplicationRef *application,
                                                                GnomeKeyringAccessType            types_allowed);
GNOME_KEYRING_DEPRECATED
GnomeKeyringAccessControl *  gnome_keyring_access_control_copy (GnomeKeyringAccessControl        *ac);
GNOME_KEYRING_DEPRECATED
GType                        gnome_keyring_access_control_get_type (void) G_GNUC_CONST;
GNOME_KEYRING_DEPRECATED
void                         gnome_keyring_access_control_free (GnomeKeyringAccessControl *ac);

#define GNOME_KEYRING_TYPE_ACCESS_CONTROL (gnome_keyring_access_control_get_type ())

GNOME_KEYRING_DEPRECATED
GList * gnome_keyring_acl_copy            (GList                     *list);
GNOME_KEYRING_DEPRECATED
void    gnome_keyring_acl_free            (GList                     *acl);

GNOME_KEYRING_DEPRECATED
char *                gnome_keyring_item_ac_get_display_name   (GnomeKeyringAccessControl *ac);
GNOME_KEYRING_DEPRECATED
void                  gnome_keyring_item_ac_set_display_name   (GnomeKeyringAccessControl *ac,
                                                                const char           *value);

GNOME_KEYRING_DEPRECATED
char *                gnome_keyring_item_ac_get_path_name      (GnomeKeyringAccessControl *ac);
GNOME_KEYRING_DEPRECATED
void                  gnome_keyring_item_ac_set_path_name      (GnomeKeyringAccessControl *ac,
                                                                const char           *value);

GNOME_KEYRING_DEPRECATED
GnomeKeyringAccessType gnome_keyring_item_ac_get_access_type   (GnomeKeyringAccessControl *ac);
GNOME_KEYRING_DEPRECATED
void                   gnome_keyring_item_ac_set_access_type   (GnomeKeyringAccessControl *ac,
                                                                const GnomeKeyringAccessType value);

GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_item_get_acl             (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GnomeKeyringOperationGetListCallback        callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_item_get_acl_sync        (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GList                                     **acl);
GNOME_KEYRING_DEPRECATED
gpointer           gnome_keyring_item_set_acl             (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GList                                      *acl,
                                                           GnomeKeyringOperationDoneCallback           callback,
                                                           gpointer                                    data,
                                                           GDestroyNotify                              destroy_data);
GNOME_KEYRING_DEPRECATED
GnomeKeyringResult gnome_keyring_item_set_acl_sync        (const char                                 *keyring,
                                                           guint32                                     id,
                                                           GList                                      *acl);

#endif /* GNOME_KEYRING_DISABLE_DEPRECATED */

G_END_DECLS

#endif /* GNOME_KEYRING_H */
