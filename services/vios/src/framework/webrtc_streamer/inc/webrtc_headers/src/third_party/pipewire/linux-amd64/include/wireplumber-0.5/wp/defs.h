/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_DEFS_H__
#define __WIREPLUMBER_DEFS_H__

#if defined(__GNUC__)
# define WP_PLUGIN_EXPORT __attribute__ ((visibility ("default")))
# define WP_API_EXPORT extern __attribute__ ((visibility ("default")))
#else
# define WP_PLUGIN_EXPORT
# define WP_API_EXPORT extern
#endif

#define WP_API_IMPORT extern

#ifndef WP_API
# ifdef BUILDING_WP
#  define WP_API WP_API_EXPORT
# else
#  define WP_API WP_API_IMPORT
# endif
#endif

#ifndef WP_PRIVATE_API
# ifdef BUILDING_WP
#  define WP_PRIVATE_API
# else
#  define WP_PRIVATE_API __attribute__ ((deprecated ("Private API")))
# endif
#endif

#define WP_PADDING(n) gpointer _wp_padding[n];

#endif
