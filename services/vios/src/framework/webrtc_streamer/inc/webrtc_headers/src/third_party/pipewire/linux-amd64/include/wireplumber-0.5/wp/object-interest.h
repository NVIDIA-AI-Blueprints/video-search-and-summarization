/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_OBJECT_INTEREST_H__
#define __WIREPLUMBER_OBJECT_INTEREST_H__

#include <glib-object.h>
#include "defs.h"
#include "properties.h"

G_BEGIN_DECLS

/*!
 * \brief Constraint types for wp_object_interest_add_constraint()
 * \ingroup wpobjectinterest
 */
typedef enum {
  /*! invalid constraint type */
  WP_CONSTRAINT_TYPE_NONE = 0,
  /*! constraint applies to a PipeWire global property of the object
   *  (the ones returned by wp_global_proxy_get_global_properties()) */
  WP_CONSTRAINT_TYPE_PW_GLOBAL_PROPERTY,
  /*! constraint applies to a PipeWire property of the object
   *  (the ones returned by wp_pipewire_object_get_properties()) */
  WP_CONSTRAINT_TYPE_PW_PROPERTY,
  /*! constraint applies to a GObject property of the object */
  WP_CONSTRAINT_TYPE_G_PROPERTY,
} WpConstraintType;

/*!
 * \brief Verbs to use with wp_object_interest_add_constraint()
 * \ingroup wpobjectinterest
 */
typedef enum {
  /*! the subject's value must equal the constraint's value */
  WP_CONSTRAINT_VERB_EQUALS = '=',
  /*! the subject's value must be different from the constraint's value */
  WP_CONSTRAINT_VERB_NOT_EQUALS = '!',
  /*! the subject's value must equal at least
   *  one of the values in the list given as the constraint's value */
  WP_CONSTRAINT_VERB_IN_LIST = 'c',
  /*! the subject's value must be a number in the range defined
   *  by the constraint's value */
  WP_CONSTRAINT_VERB_IN_RANGE = '~',
  /*! the subject's value must match the pattern specified in the
   *  constraint's value */
  WP_CONSTRAINT_VERB_MATCHES = '#',
  /*! the subject property must exist */
  WP_CONSTRAINT_VERB_IS_PRESENT = '+',
  /*! the subject property must not exist */
  WP_CONSTRAINT_VERB_IS_ABSENT = '-',
} WpConstraintVerb;

/*!
 * \brief Flags that indicate which constraints have been matched in
 *  wp_object_interest_matches_full()
 * \ingroup wpobjectinterest
 */
typedef enum { /*< flags >*/
  WP_INTEREST_MATCH_NONE = 0,
  WP_INTEREST_MATCH_GTYPE = (1 << 0),
  WP_INTEREST_MATCH_PW_GLOBAL_PROPERTIES = (1 << 1),
  WP_INTEREST_MATCH_PW_PROPERTIES = (1 << 2),
  WP_INTEREST_MATCH_G_PROPERTIES = (1 << 3),

  /*!
   * Special WpInterestMatch value that indicates that all constraints
   * have been matched
   */
  WP_INTEREST_MATCH_ALL =
    (WP_INTEREST_MATCH_GTYPE |
     WP_INTEREST_MATCH_PW_GLOBAL_PROPERTIES |
     WP_INTEREST_MATCH_PW_PROPERTIES |
     WP_INTEREST_MATCH_G_PROPERTIES),
} WpInterestMatch;

/*!
 * \brief Flags to alter the behaviour of wp_object_interest_matches_full()
 * \ingroup wpobjectinterest
 */
typedef enum { /*< flags >*/
  WP_INTEREST_MATCH_FLAGS_NONE = 0,
  /*! check all the constraints instead of returning after the first mismatch */
  WP_INTEREST_MATCH_FLAGS_CHECK_ALL = (1 << 0),
} WpInterestMatchFlags;

/*!
 * \brief The WpObjectInterest GType
 * \ingroup wpobjectinterest
 */
#define WP_TYPE_OBJECT_INTEREST (wp_object_interest_get_type ())
WP_API
GType wp_object_interest_get_type (void) G_GNUC_CONST;

typedef struct _WpObjectInterest WpObjectInterest;

WP_API
WpObjectInterest * wp_object_interest_new (GType gtype, ...) G_GNUC_NULL_TERMINATED;

WP_API
WpObjectInterest * wp_object_interest_new_valist (GType gtype, va_list * args);

WP_API
WpObjectInterest * wp_object_interest_new_type (GType gtype);

WP_API
void wp_object_interest_add_constraint (WpObjectInterest * self,
    WpConstraintType type, const gchar * subject,
    WpConstraintVerb verb, GVariant * value);

WP_API
WpObjectInterest * wp_object_interest_ref (WpObjectInterest *self);

WP_API
void wp_object_interest_unref (WpObjectInterest * self);

WP_API
gboolean wp_object_interest_validate (WpObjectInterest * self, GError ** error);

WP_API
gboolean wp_object_interest_matches (WpObjectInterest * self, gpointer object);

WP_API
WpInterestMatch wp_object_interest_matches_full (WpObjectInterest * self,
    WpInterestMatchFlags flags, GType object_type, gpointer object,
    WpProperties * pw_props, WpProperties * pw_global_props);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpObjectInterest, wp_object_interest_unref)

G_END_DECLS

#endif
