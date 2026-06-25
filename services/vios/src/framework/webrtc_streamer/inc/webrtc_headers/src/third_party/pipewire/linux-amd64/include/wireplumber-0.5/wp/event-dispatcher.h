/* WirePlumber
 *
 * Copyright © 2022 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_EVENT_DISPATCHER_H__
#define __WIREPLUMBER_EVENT_DISPATCHER_H__

#include "core.h"
#include "event.h"
#include "event-hook.h"

G_BEGIN_DECLS

/*! \defgroup wpeventdispatcher WpEventDispatcher */
/*!
 * \struct WpEventDispatcher
 *
 * The event dispatcher holds all the events and hooks and dispatches them. It orchestras the show on event stack.
 */
#define WP_TYPE_EVENT_DISPATCHER (wp_event_dispatcher_get_type ())
WP_API
G_DECLARE_FINAL_TYPE (WpEventDispatcher, wp_event_dispatcher,
                      WP, EVENT_DISPATCHER, GObject)

WP_API
WpEventDispatcher * wp_event_dispatcher_get_instance (WpCore * core);

WP_API
void wp_event_dispatcher_push_event (WpEventDispatcher * self, WpEvent * event);

WP_API
void wp_event_dispatcher_register_hook (WpEventDispatcher * self,
    WpEventHook * hook);

WP_API
void wp_event_dispatcher_unregister_hook (WpEventDispatcher * self,
    WpEventHook * hook);

WP_API
WpIterator * wp_event_dispatcher_new_hooks_iterator (WpEventDispatcher * self);

G_END_DECLS

#endif
