/* WirePlumber
 *
 * Copyright © 2020 Collabora Ltd.
 *    @author George Kiagiadakis <george.kiagiadakis@collabora.com>
 *
 * SPDX-License-Identifier: MIT
 */

#ifndef __WIREPLUMBER_SI_INTERFACES_H__
#define __WIREPLUMBER_SI_INTERFACES_H__

#include "session-item.h"
#include "properties.h"
#include "spa-pod.h"

G_BEGIN_DECLS

typedef struct _WpSiAcquisition WpSiAcquisition;

/*!
 * \brief The WpSiAdapter GType
 * \ingroup wpsiinterfaces
 */
#define WP_TYPE_SI_ADAPTER (wp_si_adapter_get_type ())
WP_API
G_DECLARE_INTERFACE (WpSiAdapter, wp_si_adapter,
                     WP, SI_ADAPTER, WpSessionItem)

/*!
 * \brief The ports configuration state of the adapter
 * \ingroup wpsiinterfaces
 * \since 0.4.10
 */
typedef enum {
  WP_SI_ADAPTER_PORTS_STATE_NONE = 0, /*!< the ports have never been configured */
  WP_SI_ADAPTER_PORTS_STATE_CONFIGURING, /*!< the ports are being configured */
  WP_SI_ADAPTER_PORTS_STATE_CONFIGURED, /*!< the ports are configured */
} WpSiAdapterPortsState;

struct _WpSiAdapterInterface
{
  GTypeInterface interface;

  WpSpaPod * (*get_ports_format) (WpSiAdapter * self, const gchar **mode);
  void (*set_ports_format) (WpSiAdapter * self, WpSpaPod *format,
      const gchar *mode, GAsyncReadyCallback callback, gpointer data);
  gboolean (*set_ports_format_finish) (WpSiAdapter * self, GAsyncResult * res,
      GError ** error);
  WpSiAdapterPortsState (*get_ports_state) (WpSiAdapter * self);

  /*< private >*/
  WP_PADDING(4)
};

WP_API
WpSiAdapterPortsState wp_si_adapter_get_ports_state (WpSiAdapter * self);

WP_API
WpSpaPod *wp_si_adapter_get_ports_format (WpSiAdapter * self,
    const gchar **mode);

WP_API
void wp_si_adapter_set_ports_format (WpSiAdapter * self, WpSpaPod *format,
    const gchar *mode, GAsyncReadyCallback callback, gpointer data);

WP_API
gboolean wp_si_adapter_set_ports_format_finish (WpSiAdapter * self,
    GAsyncResult * res, GError ** error);

/*!
 * \brief The WpSiLinkable GType
 * \ingroup wpsiinterfaces
 */
#define WP_TYPE_SI_LINKABLE (wp_si_linkable_get_type ())
WP_API
G_DECLARE_INTERFACE (WpSiLinkable, wp_si_linkable,
                     WP, SI_LINKABLE, WpSessionItem)

struct _WpSiLinkableInterface
{
  GTypeInterface interface;

  GVariant * (*get_ports) (WpSiLinkable * self, const gchar * context);
  WpSiAcquisition * (*get_acquisition) (WpSiLinkable * self);

  /*< private >*/
  WP_PADDING(6)
};

WP_API
GVariant * wp_si_linkable_get_ports (WpSiLinkable * self,
    const gchar * context);

WP_API
WpSiAcquisition * wp_si_linkable_get_acquisition (WpSiLinkable * self);

/*!
 * \brief The WpSiLink GType
 * \ingroup wpsiinterfaces
 */
#define WP_TYPE_SI_LINK (wp_si_link_get_type ())
WP_API
G_DECLARE_INTERFACE (WpSiLink, wp_si_link,
                     WP, SI_LINK, WpSessionItem)

struct _WpSiLinkInterface
{
  GTypeInterface interface;

  GVariant * (*get_registration_info) (WpSiLink * self);
  WpProperties * (*get_properties) (WpSiLink * self);

  WpSiLinkable * (*get_out_item) (WpSiLink * self);
  WpSiLinkable * (*get_in_item) (WpSiLink * self);

  /*< private >*/
  WP_PADDING(4)
};

WP_API
GVariant * wp_si_link_get_registration_info (WpSiLink * self);

WP_API
WpProperties * wp_si_link_get_properties (WpSiLink * self);

WP_API
WpSiLinkable * wp_si_link_get_out_item (WpSiLink * self);

WP_API
WpSiLinkable * wp_si_link_get_in_item (WpSiLink * self);

/*!
 * \brief The WpSiAcquisition GType
 * \ingroup wpsiinterfaces
 */
#define WP_TYPE_SI_ACQUISITION (wp_si_acquisition_get_type ())
WP_API
G_DECLARE_INTERFACE (WpSiAcquisition, wp_si_acquisition,
                     WP, SI_ACQUISITION, WpSessionItem)

struct _WpSiAcquisitionInterface
{
  GTypeInterface interface;

  void (*acquire) (WpSiAcquisition * self, WpSiLink * acquisitor,
      WpSiLinkable * item, GAsyncReadyCallback callback, gpointer data);
  gboolean (*acquire_finish) (WpSiAcquisition * self, GAsyncResult * res,
      GError ** error);

  void (*release) (WpSiAcquisition * self, WpSiLink * acquisitor,
      WpSiLinkable * item);

  /*< private >*/
  WP_PADDING(5)
};

WP_API
void wp_si_acquisition_acquire (WpSiAcquisition * self, WpSiLink * acquisitor,
    WpSiLinkable * item, GAsyncReadyCallback callback, gpointer data);

WP_API
gboolean wp_si_acquisition_acquire_finish (
    WpSiAcquisition * self, GAsyncResult * res, GError ** error);

WP_API
void wp_si_acquisition_release (WpSiAcquisition * self, WpSiLink * acquisitor,
    WpSiLinkable * item);

G_END_DECLS

#endif
