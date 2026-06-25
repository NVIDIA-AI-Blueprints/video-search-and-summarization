/* GdkPixbuf library - Xlib header file
 *
 * Authors: John Harper <john@dcs.warwick.ac.uk>
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Library General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Library General Public License for more details.
 *
 * You should have received a copy of the GNU Library General Public
 * License along with this library; if not, see <http://www.gnu.org/licenses/>.
 */

#ifndef GDK_PIXBUF_XLIB_H
#define GDK_PIXBUF_XLIB_H

#include <gdk-pixbuf/gdk-pixbuf.h>
#include <gdk-pixbuf-xlib/gdk-pixbuf-xlibrgb.h>

G_BEGIN_DECLS

#include <X11/Xlib.h>



/* init */

_GDK_PIXBUF_EXTERN
void gdk_pixbuf_xlib_init (Display *display, int screen_num);

_GDK_PIXBUF_EXTERN
void gdk_pixbuf_xlib_init_with_depth (Display *display, int screen_num,
				      int prefDepth);



/* render */

_GDK_PIXBUF_EXTERN
void gdk_pixbuf_xlib_render_threshold_alpha (GdkPixbuf *pixbuf, Pixmap bitmap,
					     int src_x, int src_y,
					     int dest_x, int dest_y,
					     int width, int height,
					     int alpha_threshold);

_GDK_PIXBUF_EXTERN
void gdk_pixbuf_xlib_render_to_drawable (GdkPixbuf *pixbuf,
					 Drawable drawable, GC gc,
					 int src_x, int src_y,
					 int dest_x, int dest_y,
					 int width, int height,
					 XlibRgbDither dither,
					 int x_dither, int y_dither);


_GDK_PIXBUF_EXTERN
void gdk_pixbuf_xlib_render_to_drawable_alpha (GdkPixbuf *pixbuf,
					       Drawable drawable,
					       int src_x, int src_y,
					       int dest_x, int dest_y,
					       int width, int height,
					       GdkPixbufAlphaMode alpha_mode,
					       int alpha_threshold,
					       XlibRgbDither dither,
					       int x_dither, int y_dither);

_GDK_PIXBUF_EXTERN
void gdk_pixbuf_xlib_render_pixmap_and_mask (GdkPixbuf *pixbuf,
					     Pixmap *pixmap_return,
					     Pixmap *mask_return,
					     int alpha_threshold);



/* drawable */

_GDK_PIXBUF_EXTERN
GdkPixbuf *gdk_pixbuf_xlib_get_from_drawable (GdkPixbuf *dest,
					      Drawable src,
					      Colormap cmap, Visual *visual,
					      int src_x, int src_y,
					      int dest_x, int dest_y,
					      int width, int height);

G_END_DECLS

#endif /* GDK_PIXBUF_XLIB_H */
