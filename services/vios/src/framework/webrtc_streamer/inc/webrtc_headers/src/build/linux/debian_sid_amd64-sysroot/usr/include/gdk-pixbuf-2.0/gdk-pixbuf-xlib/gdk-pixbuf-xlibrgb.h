/* -*- Mode: C++; tab-width: 2; indent-tabs-mode: nil; c-basic-offset: 2 -*-
 *
 * The contents of this file are subject to the Mozilla Public License
 * Version 1.1 (the "MPL"); you may not use this file except in
 * compliance with the MPL.  You may obtain a copy of the MPL at
 * http://www.mozilla.org/MPL/
 *
 * Software distributed under the MPL is distributed on an "AS IS" basis,
 * WITHOUT WARRANTY OF ANY KIND, either express or implied. See the MPL
 * for the specific language governing rights and limitations under the
 * MPL.
 *
 * Alternatively, the contents of this file may be used under the
 * terms of the GNU Library General Public License (the "LGPL"), in
 * which case the provisions of the LGPL are applicable instead of
 * those above.  If you wish to allow use of your version of this file
 * only under the terms of the LGPL and not to allow others to use
 * your version of this file under the MPL, indicate your decision by
 * deleting the provisions above and replace them with the notice and
 * other provisions required by the LGPL.  If you do not delete the
 * provisions above, a recipient may use your version of this file
 * under either the MPL or the LGPL.
 */

/*
 * This code is derived from GdkRgb.
 * For more information on GdkRgb, see http://www.levien.com/gdkrgb/
 * Raph Levien <raph@acm.org>
 */

/* Ported by Christopher Blizzard to Xlib.  With permission from the
 * original authors of this file, the contents of this file are also
 * redistributable under the terms of the Mozilla Public license.  For
 * information about the Mozilla Public License, please see the
 * license information at http://www.mozilla.org/MPL/
 */

/* This code is copyright the following authors:
 * Raph Levien          <raph@acm.org>
 * Manish Singh         <manish@gtk.org>
 * Tim Janik            <timj@gtk.org>
 * Peter Mattis         <petm@xcf.berkeley.edu>
 * Spencer Kimball      <spencer@xcf.berkeley.edu>
 * Josh MacDonald       <jmacd@xcf.berkeley.edu>
 * Christopher Blizzard <blizzard@redhat.com>
 * Owen Taylor          <otaylor@redhat.com>
 * Shawn T. Amundson    <amundson@gtk.org>
*/


#ifndef __XLIB_RGB_H__
#define __XLIB_RGB_H__

#include <gdk-pixbuf/gdk-pixbuf.h>

G_BEGIN_DECLS

#include <X11/Xlib.h>
#include <X11/Xutil.h>

/**
 * XlibRgbCmap:
 * @colors: FIXME.
 * @lut: FIXME.
 * 
 *     FIXME: Describe this.
 */
typedef struct _XlibRgbCmap XlibRgbCmap;
struct _XlibRgbCmap {
  unsigned int colors[256];
  unsigned char lut[256]; /* for 8-bit modes */
};

_GDK_PIXBUF_EXTERN
void
xlib_rgb_init (Display *display, Screen *screen);
_GDK_PIXBUF_EXTERN
void
xlib_rgb_init_with_depth (Display *display, Screen *screen, int prefDepth);

_GDK_PIXBUF_EXTERN
unsigned long
xlib_rgb_xpixel_from_rgb (guint32 rgb);

_GDK_PIXBUF_EXTERN
void
xlib_rgb_gc_set_foreground (GC gc, guint32 rgb);

_GDK_PIXBUF_EXTERN
void
xlib_rgb_gc_set_background (GC gc, guint32 rgb);

/**
 * XlibRgbDither:
 * @XLIB_RGB_DITHER_NONE: Specifies no dithering.
 * @XLIB_RGB_DITHER_NORMAL: Specifies dithering only on pseudocolor
 *  displays.
 * @XLIB_RGB_DITHER_MAX: Specifies dithering on high color displays.
 * 
 * These values are used to specify which dithering method should be
 * used.  <symbol>XLIB_RGB_DITHER_NONE</symbol> will use no dithering
 * and simply map the colors in an RGB buffer to the closest colors
 * that the display can provide.
 * <symbol>XLIB_RGB_DITHER_NORMAL</symbol> will provide dithering
 * only on pseudocolor displays.
 * <symbol>XLIB_RGB_DITHER_MAX</symbol> will provide dithering on
 * pseudocolor and 16-bit truecolor or &ldquo;high color&rdquo;
 * displays.
 */
typedef enum
{
  XLIB_RGB_DITHER_NONE,
  XLIB_RGB_DITHER_NORMAL,
  XLIB_RGB_DITHER_MAX
} XlibRgbDither;

_GDK_PIXBUF_EXTERN
void
xlib_draw_rgb_image (Drawable drawable,
		     GC gc,
		     int x,
		     int y,
		     int width,
		     int height,
		     XlibRgbDither dith,
		     unsigned char *rgb_buf,
		     int rowstride);

_GDK_PIXBUF_EXTERN
void
xlib_draw_rgb_image_dithalign (Drawable drawable,
			       GC gc,
			       int x,
			       int y,
			       int width,
			       int height,
			       XlibRgbDither dith,
			       unsigned char *rgb_buf,
			       int rowstride,
			       int xdith,
			       int ydith);

_GDK_PIXBUF_EXTERN
void
xlib_draw_rgb_32_image (Drawable drawable,
			GC gc,
			int x,
			int y,
			int width,
			int height,
			XlibRgbDither dith,
			unsigned char *buf,
			int rowstride);

_GDK_PIXBUF_EXTERN
void
xlib_draw_gray_image (Drawable drawable,
		      GC gc,
		      int x,
		      int y,
		      int width,
		      int height,
		      XlibRgbDither dith,
		      unsigned char *buf,
		      int rowstride);

_GDK_PIXBUF_EXTERN
XlibRgbCmap *
xlib_rgb_cmap_new (guint32 *colors, int n_colors);

_GDK_PIXBUF_EXTERN
void
xlib_rgb_cmap_free (XlibRgbCmap *cmap);

_GDK_PIXBUF_EXTERN
void
xlib_draw_indexed_image (Drawable drawable,
                         GC gc,
                         int x,
                         int y,
                         int width,
                         int height,
                         XlibRgbDither dith,
                         unsigned char *buf,
                         int rowstride,
                         XlibRgbCmap *cmap);

/* Below are some functions which are primarily useful for debugging
   and experimentation. */
_GDK_PIXBUF_EXTERN
Bool
xlib_rgb_ditherable (void);

_GDK_PIXBUF_EXTERN
void
xlib_rgb_set_verbose (Bool verbose);

/* experimental colormap stuff */
_GDK_PIXBUF_EXTERN
void
xlib_rgb_set_install (Bool install);

_GDK_PIXBUF_EXTERN
void
xlib_rgb_set_min_colors (int min_colors);

_GDK_PIXBUF_EXTERN
Colormap
xlib_rgb_get_cmap (void);

_GDK_PIXBUF_EXTERN
Visual *
xlib_rgb_get_visual (void);

_GDK_PIXBUF_EXTERN
XVisualInfo *
xlib_rgb_get_visual_info (void);

_GDK_PIXBUF_EXTERN
int
xlib_rgb_get_depth (void);

_GDK_PIXBUF_EXTERN
Display *
xlib_rgb_get_display (void);

_GDK_PIXBUF_EXTERN
Screen *
xlib_rgb_get_screen (void);

G_END_DECLS

#endif /* __XLIB_RGB_H__ */
