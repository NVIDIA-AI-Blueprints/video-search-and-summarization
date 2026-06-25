/* WirePlumber
 *
 * Copyright © 2021 Asymptotic
 *    @author Arun Raghavan <arun@asymptotic.io>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_MODULE_H__
#define __WIREPLUMBER_MODULE_H__

#include <glib-object.h>

#include "core.h"
#include "defs.h"
#include "properties.h"

G_BEGIN_DECLS

/*!
 * \brief The WpImplModule GType
 * \since 0.4.2
 * \ingroup wpimplmodule
 */
#define WP_TYPE_IMPL_MODULE (wp_impl_module_get_type())
WP_API
G_DECLARE_FINAL_TYPE (WpImplModule, wp_impl_module, WP, IMPL_MODULE, GObject);

WP_API
WpImplModule * wp_impl_module_load (WpCore * core, const gchar * name,
    const gchar * arguments, WpProperties * properties);

G_END_DECLS

#endif /* __WIREPLUMBER_MODULE_H__ */
