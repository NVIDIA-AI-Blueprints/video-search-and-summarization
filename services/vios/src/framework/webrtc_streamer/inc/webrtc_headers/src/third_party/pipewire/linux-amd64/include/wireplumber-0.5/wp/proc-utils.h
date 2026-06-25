/* WirePlumber
 *
 * Copyright © 2024 Collabora Ltd.
 *    @author Julian Bouzas <julian.bouzas@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_PROC_UTILS_H__
#define __WIREPLUMBER_PROC_UTILS_H__

#include <gio/gio.h>

G_BEGIN_DECLS

/*!
 * \brief The WpProcInfo GType
 * \ingroup wpprocutils
 */
#define WP_TYPE_PROC_INFO (wp_proc_info_get_type ())
WP_API
GType wp_proc_info_get_type (void);

typedef struct _WpProcInfo WpProcInfo;

WP_API
WpProcInfo *wp_proc_info_ref (WpProcInfo * self);

WP_API
void wp_proc_info_unref (WpProcInfo * self);

WP_API
pid_t wp_proc_info_get_pid (WpProcInfo * self);

WP_API
pid_t wp_proc_info_get_parent_pid (WpProcInfo * self);

WP_API
guint wp_proc_info_get_n_args (WpProcInfo * self);

WP_API
const gchar *wp_proc_info_get_arg (WpProcInfo * self, guint index);

WP_API
const gchar *wp_proc_info_get_cgroup (WpProcInfo * self);

G_DEFINE_AUTOPTR_CLEANUP_FUNC (WpProcInfo, wp_proc_info_unref)

WP_API
WpProcInfo *wp_proc_utils_get_proc_info (pid_t pid);

G_END_DECLS

#endif
