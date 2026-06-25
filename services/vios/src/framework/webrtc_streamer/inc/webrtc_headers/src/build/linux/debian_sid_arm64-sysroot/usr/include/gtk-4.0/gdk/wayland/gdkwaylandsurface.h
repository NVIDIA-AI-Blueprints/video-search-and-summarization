/* GDK - The GIMP Drawing Kit
 * Copyright (C) 2013 Jan Arne Petersen
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library. If not, see <http://www.gnu.org/licenses/>.
 */

#ifndef __GDK_WAYLAND_SURFACE_H__
#define __GDK_WAYLAND_SURFACE_H__

#if !defined (__GDKWAYLAND_H_INSIDE__) && !defined (GDK_COMPILATION)
#error "Only <gdk/gdkwayland.h> can be included directly."
#endif

#include <gdk/gdk.h>

#include <wayland-client.h>

G_BEGIN_DECLS

#ifdef GDK_COMPILATION
typedef struct _GdkWaylandSurface GdkWaylandSurface;
#else
typedef GdkSurface GdkWaylandSurface;
#endif
typedef struct _GdkWaylandSurfaceClass GdkWaylandSurfaceClass;

#define GDK_TYPE_WAYLAND_SURFACE              (gdk_wayland_surface_get_type())
#define GDK_WAYLAND_SURFACE(object)           (G_TYPE_CHECK_INSTANCE_CAST ((object), GDK_TYPE_WAYLAND_SURFACE, GdkWaylandSurface))
#define GDK_WAYLAND_SURFACE_CLASS(klass)      (G_TYPE_CHECK_CLASS_CAST ((klass), GDK_TYPE_WAYLAND_SURFACE, GdkWaylandSurfaceClass))
#define GDK_IS_WAYLAND_SURFACE(object)        (G_TYPE_CHECK_INSTANCE_TYPE ((object), GDK_TYPE_WAYLAND_SURFACE))
#define GDK_IS_WAYLAND_SURFACE_CLASS(klass)   (G_TYPE_CHECK_CLASS_TYPE ((klass), GDK_TYPE_WAYLAND_SURFACE))
#define GDK_WAYLAND_SURFACE_GET_CLASS(obj)    (G_TYPE_INSTANCE_GET_CLASS ((obj), GDK_TYPE_WAYLAND_SURFACE, GdkWaylandSurfaceClass))

GDK_AVAILABLE_IN_ALL
GType                    gdk_wayland_surface_get_type             (void);

GDK_AVAILABLE_IN_ALL
GdkSurface *              gdk_wayland_surface_new_subsurface       (GdkDisplay            *display,
                                                                    const GdkRectangle    *position);
GDK_AVAILABLE_IN_ALL
struct wl_surface       *gdk_wayland_surface_get_wl_surface       (GdkSurface *surface);

GDK_AVAILABLE_IN_ALL
void                     gdk_wayland_surface_set_dbus_properties_libgtk_only (GdkSurface  *surface,
                                                                              const char *application_id,
                                                                              const char *app_menu_path,
                                                                              const char *menubar_path,
                                                                              const char *window_object_path,
                                                                              const char *application_object_path,
                                                                              const char *unique_bus_name);

typedef void (*GdkWaylandSurfaceExported) (GdkSurface  *surface,
                                          const char *handle,
                                          gpointer    user_data);

GDK_AVAILABLE_IN_ALL
gboolean                 gdk_wayland_surface_export_handle (GdkSurface               *surface,
                                                            GdkWaylandSurfaceExported callback,
                                                            gpointer                 user_data,
                                                            GDestroyNotify           destroy_func);

GDK_AVAILABLE_IN_ALL
void                     gdk_wayland_surface_unexport_handle (GdkSurface *surface);

GDK_AVAILABLE_IN_ALL
gboolean                 gdk_wayland_surface_set_transient_for_exported (GdkSurface *surface,
                                                                         char      *parent_handle_str);

void gdk_wayland_surface_announce_csd                        (GdkSurface *surface);

G_END_DECLS

#endif /* __GDK_WAYLAND_SURFACE_H__ */
