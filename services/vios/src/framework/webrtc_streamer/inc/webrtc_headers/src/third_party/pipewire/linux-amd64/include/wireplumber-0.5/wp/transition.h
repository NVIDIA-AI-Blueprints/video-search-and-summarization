/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_TRANSITION_H__
#define __WIREPLUMBER_TRANSITION_H__

#include <gio/gio.h>
#include "defs.h"

G_BEGIN_DECLS

/*!
 * \brief The WpTransition GType
 * \ingroup wptransition
 */
#define WP_TYPE_TRANSITION (wp_transition_get_type ())
WP_API
G_DECLARE_DERIVABLE_TYPE (WpTransition, wp_transition, WP, TRANSITION, GObject)

/*!
 * \brief Values for the \em steps of the implemented state machine
 * \ingroup wptransition
 */
typedef enum {
  /*! the initial and final step of the transition */
  WP_TRANSITION_STEP_NONE = 0,
  /*! returned by _WpTransitionClass::get_next_step() in case of an error */
  WP_TRANSITION_STEP_ERROR,
  /*! starting value for steps defined in subclasses */
  WP_TRANSITION_STEP_CUSTOM_START = 0x10
} WpTransitionStep;

struct _WpTransitionClass
{
  GObjectClass parent_class;

  /*! See wp_transition_advance() */
  guint (*get_next_step) (WpTransition * transition, guint step);
  /*! See wp_transition_advance() */
  void (*execute_step) (WpTransition * transition, guint step);

  /*< private >*/
  WP_PADDING(6)
};

WP_API
WpTransition * wp_transition_new (GType type,
    gpointer source_object, GCancellable * cancellable,
    GAsyncReadyCallback callback, gpointer callback_data);

WP_API
WpTransition * wp_transition_new_closure (GType type,
    gpointer source_object, GCancellable * cancellable, GClosure * closure);

/* source object */

WP_API
gpointer wp_transition_get_source_object (WpTransition * self);

/* tag */

WP_API
gboolean wp_transition_is_tagged (WpTransition * self, gpointer tag);

WP_API
gpointer wp_transition_get_source_tag (WpTransition * self);

WP_API
void wp_transition_set_source_tag (WpTransition * self, gpointer tag);

/* task data */

WP_API
gpointer wp_transition_get_data (WpTransition * self);

WP_API
void wp_transition_set_data (WpTransition * self, gpointer data,
    GDestroyNotify data_destroy);

/* state machine */

WP_API
gboolean wp_transition_get_completed (WpTransition * self);

WP_API
gboolean wp_transition_had_error (WpTransition * self);

WP_API
void wp_transition_advance (WpTransition * self);

WP_API
void wp_transition_return_error (WpTransition * self, GError * error);

/* result */

WP_API
gboolean wp_transition_finish (GAsyncResult * res, GError ** error);

G_END_DECLS

#endif
