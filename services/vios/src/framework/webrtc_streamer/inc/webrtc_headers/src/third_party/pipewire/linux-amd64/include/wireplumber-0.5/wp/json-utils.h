/* WirePlumber
 *
 * Copyright © 2023 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_JSON_UTILS_H__
#define __WIREPLUMBER_JSON_UTILS_H__

#include "spa-json.h"
#include "properties.h"

G_BEGIN_DECLS

/*!
 * \brief A function to be called by wp_json_utils_match_rules() when a match is found.
 * \param data (closure): the user data passed to wp_json_utils_match_rules()
 * \param action the rule's action string
 * \param value the value associated with this action
 * \param error (out): return location for a GError
 * \returns FALSE if an error occurred and the match process should stop, TRUE otherwise
 * \ingroup wpjsonutils
 */
typedef gboolean (*WpRuleMatchCallback) (gpointer data, const gchar * action,
    WpSpaJson * value, GError ** error);

WP_API
gboolean wp_json_utils_match_rules (WpSpaJson * json, WpProperties * match_props,
    WpRuleMatchCallback callback, gpointer data, GError ** error);

WP_API
gint wp_json_utils_match_rules_update_properties (WpSpaJson *json, WpProperties *props);

WP_API
WpSpaJson * wp_json_utils_merge_containers (WpSpaJson * a, WpSpaJson * b);

G_END_DECLS

#endif
