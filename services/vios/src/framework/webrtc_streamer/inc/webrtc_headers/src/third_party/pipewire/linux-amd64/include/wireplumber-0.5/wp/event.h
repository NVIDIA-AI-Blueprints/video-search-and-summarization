/* WirePlumber
 *
 * Copyright © 2022 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_EVENT_H__
#define __WIREPLUMBER_EVENT_H__

#include "properties.h"
#include "iterator.h"

G_BEGIN_DECLS

typedef struct _WpEventDispatcher WpEventDispatcher;

/*! \defgroup wpevent WpEvent */
/*!
 * \struct WpEvent
 *
 * WpEvent describes an event, an event is an entity which can be pushed on to
 * event stack and the event dispatcher is going to pick and dispatch it.
 *
 */
#define WP_TYPE_EVENT (wp_event_get_type ())
WP_API
GType wp_event_get_type (void) G_GNUC_CONST;

typedef struct _WpEvent WpEvent;

WP_API
WpEvent * wp_event_new (const gchar * type, gint priority,
    WpProperties * properties, GObject * source, GObject * subject);

WP_API
WpEvent * wp_event_ref (WpEvent * self);

WP_API
void wp_event_unref (WpEvent * self);

WP_API
gint wp_event_get_priority (WpEvent * self);

WP_API
WpProperties * wp_event_get_properties (WpEvent * self);

WP_API
GObject * wp_event_get_source (WpEvent * self);

WP_API
const gchar *wp_event_get_name(WpEvent *self);

WP_API
GObject * wp_event_get_subject (WpEvent * self);

WP_API
GCancellable * wp_event_get_cancellable (WpEvent * self);

WP_API
void wp_event_stop_processing (WpEvent * self);

WP_API
void wp_event_set_data (WpEvent * self, const gchar * key, const GValue * data);

WP_API
const GValue * wp_event_get_data (WpEvent * self, const gchar * key);

WP_API
gboolean wp_event_collect_hooks (WpEvent * event,
    WpEventDispatcher * dispatcher);

WP_API
WpIterator * wp_event_new_hooks_iterator (WpEvent * event);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpEvent, wp_event_unref)

G_END_DECLS

#endif
