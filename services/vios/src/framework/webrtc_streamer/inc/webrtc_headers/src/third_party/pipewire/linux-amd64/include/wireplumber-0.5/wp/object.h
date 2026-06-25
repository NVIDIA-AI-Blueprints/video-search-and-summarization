/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_OBJECT_H__
#define __WIREPLUMBER_OBJECT_H__

#include "transition.h"

G_BEGIN_DECLS

typedef struct _WpCore WpCore;

/*!
 * \brief Flags that specify functionality that is available on this class.
 *
 * Use wp_object_activate() to enable more features,
 * wp_object_get_supported_features() to see which features are supported and
 * wp_object_get_active_features() to find out which features are already
 * enabled. Features can also be deactivated later using wp_object_deactivate().
 *
 * Actual feature flags are to be specified by subclasses and their interfaces.
 * WP_OBJECT_FEATURES_ALL is a special value that can be used to activate
 * all the supported features in any given object.
 *
 * \ingroup wpobject
 */
typedef guint WpObjectFeatures;

/*!
 * \brief Special value that can be used to activate
 * all the supported features in any given object.
 * \ingroup wpobject
 */
static const WpObjectFeatures WP_OBJECT_FEATURES_ALL = 0xffffffff;

/*!
 * \brief The WpFeatureActivationTransition GType
 * \ingroup wpfeatureactivationtransition
 */
#define WP_TYPE_FEATURE_ACTIVATION_TRANSITION \
    (wp_feature_activation_transition_get_type ())
WP_API
G_DECLARE_FINAL_TYPE (WpFeatureActivationTransition,
                      wp_feature_activation_transition,
                      WP, FEATURE_ACTIVATION_TRANSITION, WpTransition)

WP_API
WpObjectFeatures wp_feature_activation_transition_get_requested_features (
    WpFeatureActivationTransition * self);

/*!
 * \brief The WpObject GType
 * \ingroup wpobject
 */
#define WP_TYPE_OBJECT (wp_object_get_type ())
WP_API
G_DECLARE_DERIVABLE_TYPE (WpObject, wp_object, WP, OBJECT, GObject)

struct _WpObjectClass
{
  GObjectClass parent_class;

  WpObjectFeatures (*get_supported_features) (WpObject * self);

  guint (*activate_get_next_step) (WpObject * self,
      WpFeatureActivationTransition * transition, guint step,
      WpObjectFeatures missing);
  void (*activate_execute_step) (WpObject * self,
      WpFeatureActivationTransition * transition, guint step,
      WpObjectFeatures missing);

  void (*deactivate) (WpObject * self, WpObjectFeatures features);

  /*< private >*/
  WP_PADDING(8)
};

WP_API
guint wp_object_get_id (WpObject * self);

WP_API
WpCore * wp_object_get_core (WpObject * self);

WP_API
WpObjectFeatures wp_object_get_active_features (WpObject * self);

WP_API
gboolean wp_object_test_active_features (WpObject * self, WpObjectFeatures features);

WP_API
WpObjectFeatures wp_object_get_supported_features (WpObject * self);

WP_API
gboolean wp_object_test_supported_features (WpObject * self, WpObjectFeatures features);

WP_API
void wp_object_activate (WpObject * self,
    WpObjectFeatures features, GCancellable * cancellable,
    GAsyncReadyCallback callback, gpointer user_data);

WP_API
void wp_object_activate_closure (WpObject * self,
    WpObjectFeatures features, GCancellable * cancellable, GClosure *closure);

WP_API
gboolean wp_object_activate_finish (WpObject * self, GAsyncResult * res,
    GError ** error);

WP_API
void wp_object_deactivate (WpObject * self, WpObjectFeatures features);

/* for subclasses only */

WP_API
void wp_object_abort_activation (WpObject * self, const gchar *msg);

WP_API
void wp_object_update_features (WpObject * self, WpObjectFeatures activated,
    WpObjectFeatures deactivated);

G_END_DECLS

#endif
