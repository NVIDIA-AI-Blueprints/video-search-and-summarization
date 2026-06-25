/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_LOG_H__
#define __WIREPLUMBER_LOG_H__

#include <glib-object.h>
#include "defs.h"

G_BEGIN_DECLS

/*!
 * \brief A custom GLib log level for trace messages (extension of GLogLevelFlags)
 * \ingroup wplog
 */
static const guint WP_LOG_LEVEL_TRACE = (1 << 8);

/*
  The above WP_LOG_LEVEL_TRACE constant is intended to be defined as
  (1 << G_LOG_LEVEL_USER_SHIFT), but due to a gobject-introspection bug
  we define it with the value of G_LOG_LEVEL_USER_SHIFT, which is 8, so
  that it ends up correctly in the bindings. To avoid value mismatches,
  we statically verify here that G_LOG_LEVEL_USER_SHIFT is indeed 8.
  See https://gitlab.freedesktop.org/pipewire/wireplumber/-/issues/540
*/
G_STATIC_ASSERT (G_LOG_LEVEL_USER_SHIFT == 8);

#define WP_OBJECT_FORMAT "<%s:%p>"
#define WP_OBJECT_ARGS(object) \
    (object ? G_OBJECT_TYPE_NAME(object) : "invalid"), object

WP_PRIVATE_API
void wp_log_init (gint flags);

WP_API
gboolean wp_log_set_level (const gchar *log_level);

/*!
 * \brief WpLogTopic flags
 * \ingroup wplog
 */
typedef enum { /*< flags >*/
  /*! the lower 16 bits of the flags are GLogLevelFlags */
  WP_LOG_TOPIC_LEVEL_MASK = 0xFFFF,
  /*! the log topic has infinite lifetime (lives on static storage) */
  WP_LOG_TOPIC_FLAG_STATIC = 1u << 30,
  /*! the log topic has been initialized */
  WP_LOG_TOPIC_FLAG_INITIALIZED = 1u << 31,
} WpLogTopicFlags;

/*!
 * \brief A structure representing a log topic
 * \ingroup wplog
 */
typedef struct {
  const char *topic_name;
  WpLogTopicFlags flags;

  /*< private >*/
  WP_PADDING(3)
} WpLogTopic;

#define WP_LOG_TOPIC_EXTERN(var) \
  extern WpLogTopic * var;

#define WP_LOG_TOPIC(var, name) \
  WpLogTopic var##_struct = { .topic_name = name, .flags = WP_LOG_TOPIC_FLAG_STATIC };  \
  WpLogTopic * var = &(var##_struct);

#define WP_LOG_TOPIC_STATIC(var, name) \
  static WpLogTopic var##_struct = { .topic_name = name, .flags = WP_LOG_TOPIC_FLAG_STATIC }; \
  static G_GNUC_UNUSED WpLogTopic * var = &(var##_struct);

#define WP_DEFINE_LOCAL_LOG_TOPIC(name) \
  WP_LOG_TOPIC_STATIC(WP_LOCAL_LOG_TOPIC, name)

/* make glib log functions also use the local log topic */
#ifdef WP_USE_LOCAL_LOG_TOPIC_IN_G_LOG
# ifdef G_LOG_DOMAIN
#  undef G_LOG_DOMAIN
# endif
# define G_LOG_DOMAIN (WP_LOCAL_LOG_TOPIC->topic_name)
#endif

WP_API
void wp_log_topic_init (WpLogTopic *topic);

WP_API
void wp_log_topic_register (WpLogTopic *topic);

WP_API
void wp_log_topic_unregister (WpLogTopic *topic);

static inline gboolean
wp_log_topic_is_initialized (WpLogTopic *topic)
{
  return (topic->flags & WP_LOG_TOPIC_FLAG_INITIALIZED) != 0;
}

static inline gboolean
wp_log_topic_is_enabled (WpLogTopic *topic, GLogLevelFlags log_level)
{
  /* first time initialization */
  if (G_UNLIKELY (!wp_log_topic_is_initialized (topic)))
    wp_log_topic_init (topic);

  return (topic->flags & log_level & WP_LOG_TOPIC_LEVEL_MASK) != 0;
}

#define wp_local_log_topic_is_enabled(log_level) \
  (wp_log_topic_is_enabled (WP_LOCAL_LOG_TOPIC, log_level))

WP_API
GLogWriterOutput wp_log_writer_default (GLogLevelFlags log_level,
    const GLogField *fields, gsize n_fields, gpointer user_data);

WP_API
void wp_log_checked (const gchar *log_topic, GLogLevelFlags log_level,
    const gchar *file, const gchar *line, const gchar *func,
    GType object_type, gconstpointer object,
    const gchar *message_format, ...) G_GNUC_PRINTF (8, 9) G_GNUC_DEPRECATED_FOR (wp_logt_checked);

WP_API
void wp_logt_checked (const WpLogTopic *topic, GLogLevelFlags log_level,
    const gchar *file, const gchar *line, const gchar *func,
    GType object_type, gconstpointer object,
    const gchar *message_format, ...) G_GNUC_PRINTF (8, 9);

#define wp_log(topic, level, type, object, ...) \
({ \
  if (G_UNLIKELY (wp_log_topic_is_enabled (topic, level))) \
    wp_logt_checked (topic, level, __FILE__, G_STRINGIFY (__LINE__), \
        G_STRFUNC, type, object, __VA_ARGS__); \
})

#define wp_critical(...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_CRITICAL, 0, NULL, __VA_ARGS__)
#define wp_warning(...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_WARNING, 0, NULL, __VA_ARGS__)
#define wp_notice(...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_MESSAGE, 0, NULL, __VA_ARGS__)
#define wp_info(...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_INFO, 0, NULL, __VA_ARGS__)
#define wp_debug(...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_DEBUG, 0, NULL, __VA_ARGS__)
#define wp_trace(...) \
    wp_log (WP_LOCAL_LOG_TOPIC, WP_LOG_LEVEL_TRACE, 0, NULL, __VA_ARGS__)

#define wp_critical_object(object, ...)  \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_CRITICAL, (object) ? G_TYPE_FROM_INSTANCE (object) : G_TYPE_NONE, object, __VA_ARGS__)
#define wp_warning_object(object, ...)  \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_WARNING, (object) ? G_TYPE_FROM_INSTANCE (object) : G_TYPE_NONE, object, __VA_ARGS__)
#define wp_notice_object(object, ...)  \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_MESSAGE, (object) ? G_TYPE_FROM_INSTANCE (object) : G_TYPE_NONE, object, __VA_ARGS__)
#define wp_info_object(object, ...)  \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_INFO, (object) ? G_TYPE_FROM_INSTANCE (object) : G_TYPE_NONE, object, __VA_ARGS__)
#define wp_debug_object(object, ...)  \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_DEBUG, (object) ? G_TYPE_FROM_INSTANCE (object) : G_TYPE_NONE, object, __VA_ARGS__)
#define wp_trace_object(object, ...)  \
    wp_log (WP_LOCAL_LOG_TOPIC, WP_LOG_LEVEL_TRACE, (object) ? G_TYPE_FROM_INSTANCE (object) : G_TYPE_NONE, object, __VA_ARGS__)

#define wp_critical_boxed(type, object, ...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_CRITICAL, type, object, __VA_ARGS__)
#define wp_warning_boxed(type, object, ...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_WARNING, type, object, __VA_ARGS__)
#define wp_notice_boxed(type, object, ...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_MESSAGE, type, object, __VA_ARGS__)
#define wp_info_boxed(type, object, ...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_INFO, type, object, __VA_ARGS__)
#define wp_debug_boxed(type, object, ...) \
    wp_log (WP_LOCAL_LOG_TOPIC, G_LOG_LEVEL_DEBUG, type, object, __VA_ARGS__)
#define wp_trace_boxed(type, object, ...) \
    wp_log (WP_LOCAL_LOG_TOPIC, WP_LOG_LEVEL_TRACE, type, object, __VA_ARGS__)

struct spa_log;

WP_API
struct spa_log * wp_spa_log_get_instance (void);

G_END_DECLS

#endif
