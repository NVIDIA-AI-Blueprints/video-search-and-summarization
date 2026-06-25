/* gdktexture.h
 *
 * Copyright 2016  Benjamin Otte
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

#ifndef __GDK_TEXTURE_H__
#define __GDK_TEXTURE_H__

#if !defined (__GDK_H_INSIDE__) && !defined (GDK_COMPILATION)
#error "Only <gdk/gdk.h> can be included directly."
#endif

#include <gdk/gdkversionmacros.h>
#include <gdk/gdktypes.h>
#include <gdk-pixbuf/gdk-pixbuf.h>

G_BEGIN_DECLS

#define GDK_TYPE_TEXTURE (gdk_texture_get_type ())

#define GDK_TEXTURE(obj)               (G_TYPE_CHECK_INSTANCE_CAST ((obj), GDK_TYPE_TEXTURE, GdkTexture))
#define GDK_IS_TEXTURE(obj)            (G_TYPE_CHECK_INSTANCE_TYPE ((obj), GDK_TYPE_TEXTURE))

G_DEFINE_AUTOPTR_CLEANUP_FUNC(GdkTexture, g_object_unref)

typedef struct _GdkTextureClass        GdkTextureClass;


GDK_AVAILABLE_IN_ALL
GType                   gdk_texture_get_type                   (void) G_GNUC_CONST;

GDK_AVAILABLE_IN_ALL
GdkTexture *            gdk_texture_new_for_pixbuf             (GdkPixbuf       *pixbuf);
GDK_AVAILABLE_IN_ALL
GdkTexture *            gdk_texture_new_from_resource          (const char      *resource_path);
GDK_AVAILABLE_IN_ALL
GdkTexture *            gdk_texture_new_from_file              (GFile           *file,
                                                                GError         **error);

GDK_AVAILABLE_IN_ALL
int                     gdk_texture_get_width                  (GdkTexture      *texture);
GDK_AVAILABLE_IN_ALL
int                     gdk_texture_get_height                 (GdkTexture      *texture);

GDK_AVAILABLE_IN_ALL
void                    gdk_texture_download                   (GdkTexture      *texture,
                                                                guchar          *data,
                                                                gsize            stride);
GDK_AVAILABLE_IN_ALL
gboolean                gdk_texture_save_to_png                (GdkTexture      *texture,
                                                                const char      *filename);

G_END_DECLS

#endif /* __GDK_TEXTURE_H__ */
