/* WirePlumber
 *
 * Copyright © 2021 Collabora Ltd.
 *    @author Ashok Sidipotu <ashok.sidipotuc@ollabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_FACTORY_H__
#define __WIREPLUMBER_FACTORY_H__

#include "global-proxy.h"

G_BEGIN_DECLS

struct pw_factory;

/*!
 * \brief The WpFactory GType
 * \ingroup wpfactory
 */
#define WP_TYPE_FACTORY (wp_factory_get_type ())

WP_API
G_DECLARE_FINAL_TYPE (WpFactory, wp_factory, WP, FACTORY, WpGlobalProxy)

G_END_DECLS

#endif
