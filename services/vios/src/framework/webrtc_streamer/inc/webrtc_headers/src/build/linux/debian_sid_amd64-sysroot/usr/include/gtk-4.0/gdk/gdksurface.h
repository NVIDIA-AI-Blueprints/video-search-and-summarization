/* GDK - The GIMP Drawing Kit
 * Copyright (C) 1995-1997 Peter Mattis, Spencer Kimball and Josh MacDonald
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

/*
 * Modified by the GTK+ Team and others 1997-2000.  See the AUTHORS
 * file for a list of people on the GTK+ Team.  See the ChangeLog
 * files for a list of changes.  These files are distributed with
 * GTK+ at ftp://ftp.gtk.org/pub/gtk/.
 */

#ifndef __GDK_SURFACE_H__
#define __GDK_SURFACE_H__

#if !defined (__GDK_H_INSIDE__) && !defined (GDK_COMPILATION)
#error "Only <gdk/gdk.h> can be included directly."
#endif

#include <gdk/gdkversionmacros.h>
#include <gdk/gdktypes.h>
#include <gdk/gdkevents.h>
#include <gdk/gdkframeclock.h>
#include <gdk/gdkmonitor.h>

G_BEGIN_DECLS

typedef struct _GdkGeometry          GdkGeometry;

/**
 * GdkSurfaceType:
 * @GDK_SURFACE_TOPLEVEL: toplevel window (used to implement #GtkWindow)
 * @GDK_SURFACE_CHILD: child surface (used to implement e.g. #GtkEntry)
 * @GDK_SURFACE_TEMP: override redirect temporary surface (used to implement #GtkMenu)
 *
 * Describes the kind of surface.
 */
typedef enum
{
  GDK_SURFACE_TOPLEVEL,
  GDK_SURFACE_CHILD,
  GDK_SURFACE_TEMP
} GdkSurfaceType;

/* Size restriction enumeration.
 */
/**
 * GdkSurfaceHints:
 * @GDK_HINT_POS: indicates that the program has positioned the surface
 * @GDK_HINT_MIN_SIZE: min size fields are set
 * @GDK_HINT_MAX_SIZE: max size fields are set
 * @GDK_HINT_BASE_SIZE: base size fields are set
 * @GDK_HINT_ASPECT: aspect ratio fields are set
 * @GDK_HINT_RESIZE_INC: resize increment fields are set
 * @GDK_HINT_WIN_GRAVITY: surface gravity field is set
 * @GDK_HINT_USER_POS: indicates that the surface’s position was explicitly set
 *  by the user
 * @GDK_HINT_USER_SIZE: indicates that the surface’s size was explicitly set by
 *  the user
 *
 * Used to indicate which fields of a #GdkGeometry struct should be paid
 * attention to. Also, the presence/absence of @GDK_HINT_POS,
 * @GDK_HINT_USER_POS, and @GDK_HINT_USER_SIZE is significant, though they don't
 * directly refer to #GdkGeometry fields. @GDK_HINT_USER_POS will be set
 * automatically by #GtkWindow if you call gtk_window_move().
 * @GDK_HINT_USER_POS and @GDK_HINT_USER_SIZE should be set if the user
 * specified a size/position using a --geometry command-line argument;
 * gtk_window_parse_geometry() automatically sets these flags.
 */
typedef enum
{
  GDK_HINT_POS	       = 1 << 0,
  GDK_HINT_MIN_SIZE    = 1 << 1,
  GDK_HINT_MAX_SIZE    = 1 << 2,
  GDK_HINT_BASE_SIZE   = 1 << 3,
  GDK_HINT_ASPECT      = 1 << 4,
  GDK_HINT_RESIZE_INC  = 1 << 5,
  GDK_HINT_WIN_GRAVITY = 1 << 6,
  GDK_HINT_USER_POS    = 1 << 7,
  GDK_HINT_USER_SIZE   = 1 << 8
} GdkSurfaceHints;

/* The next two enumeration values current match the
 * Motif constants. If this is changed, the implementation
 * of gdk_surface_set_decorations/gdk_surface_set_functions
 * will need to change as well.
 */
/**
 * GdkWMDecoration:
 * @GDK_DECOR_ALL: all decorations should be applied.
 * @GDK_DECOR_BORDER: a frame should be drawn around the surface.
 * @GDK_DECOR_RESIZEH: the frame should have resize handles.
 * @GDK_DECOR_TITLE: a titlebar should be placed above the surface.
 * @GDK_DECOR_MENU: a button for opening a menu should be included.
 * @GDK_DECOR_MINIMIZE: a minimize button should be included.
 * @GDK_DECOR_MAXIMIZE: a maximize button should be included.
 *
 * These are hints originally defined by the Motif toolkit.
 * The window manager can use them when determining how to decorate
 * the surface. The hint must be set before mapping the surface.
 */
typedef enum
{
  GDK_DECOR_ALL		= 1 << 0,
  GDK_DECOR_BORDER	= 1 << 1,
  GDK_DECOR_RESIZEH	= 1 << 2,
  GDK_DECOR_TITLE	= 1 << 3,
  GDK_DECOR_MENU	= 1 << 4,
  GDK_DECOR_MINIMIZE	= 1 << 5,
  GDK_DECOR_MAXIMIZE	= 1 << 6
} GdkWMDecoration;

/**
 * GdkWMFunction:
 * @GDK_FUNC_ALL: all functions should be offered.
 * @GDK_FUNC_RESIZE: the surface should be resizable.
 * @GDK_FUNC_MOVE: the surface should be movable.
 * @GDK_FUNC_MINIMIZE: the surface should be minimizable.
 * @GDK_FUNC_MAXIMIZE: the surface should be maximizable.
 * @GDK_FUNC_CLOSE: the surface should be closable.
 *
 * These are hints originally defined by the Motif toolkit. The window manager
 * can use them when determining the functions to offer for the surface. The
 * hint must be set before mapping the surface.
 */
typedef enum
{
  GDK_FUNC_ALL		= 1 << 0,
  GDK_FUNC_RESIZE	= 1 << 1,
  GDK_FUNC_MOVE		= 1 << 2,
  GDK_FUNC_MINIMIZE	= 1 << 3,
  GDK_FUNC_MAXIMIZE	= 1 << 4,
  GDK_FUNC_CLOSE	= 1 << 5
} GdkWMFunction;

/* Currently, these are the same values numerically as in the
 * X protocol. If you change that, gdksurface-x11.c/gdk_surface_set_geometry_hints()
 * will need fixing.
 */
/**
 * GdkGravity:
 * @GDK_GRAVITY_NORTH_WEST: the reference point is at the top left corner.
 * @GDK_GRAVITY_NORTH: the reference point is in the middle of the top edge.
 * @GDK_GRAVITY_NORTH_EAST: the reference point is at the top right corner.
 * @GDK_GRAVITY_WEST: the reference point is at the middle of the left edge.
 * @GDK_GRAVITY_CENTER: the reference point is at the center of the surface.
 * @GDK_GRAVITY_EAST: the reference point is at the middle of the right edge.
 * @GDK_GRAVITY_SOUTH_WEST: the reference point is at the lower left corner.
 * @GDK_GRAVITY_SOUTH: the reference point is at the middle of the lower edge.
 * @GDK_GRAVITY_SOUTH_EAST: the reference point is at the lower right corner.
 * @GDK_GRAVITY_STATIC: the reference point is at the top left corner of the
 *  surface itself, ignoring window manager decorations.
 *
 * Defines the reference point of a surface and the meaning of coordinates
 * passed to gtk_window_move(). See gtk_window_move() and the "implementation
 * notes" section of the
 * [Extended Window Manager Hints](http://www.freedesktop.org/Standards/wm-spec)
 * specification for more details.
 */
typedef enum
{
  GDK_GRAVITY_NORTH_WEST = 1,
  GDK_GRAVITY_NORTH,
  GDK_GRAVITY_NORTH_EAST,
  GDK_GRAVITY_WEST,
  GDK_GRAVITY_CENTER,
  GDK_GRAVITY_EAST,
  GDK_GRAVITY_SOUTH_WEST,
  GDK_GRAVITY_SOUTH,
  GDK_GRAVITY_SOUTH_EAST,
  GDK_GRAVITY_STATIC
} GdkGravity;

/**
 * GdkAnchorHints:
 * @GDK_ANCHOR_FLIP_X: allow flipping anchors horizontally
 * @GDK_ANCHOR_FLIP_Y: allow flipping anchors vertically
 * @GDK_ANCHOR_SLIDE_X: allow sliding surface horizontally
 * @GDK_ANCHOR_SLIDE_Y: allow sliding surface vertically
 * @GDK_ANCHOR_RESIZE_X: allow resizing surface horizontally
 * @GDK_ANCHOR_RESIZE_Y: allow resizing surface vertically
 * @GDK_ANCHOR_FLIP: allow flipping anchors on both axes
 * @GDK_ANCHOR_SLIDE: allow sliding surface on both axes
 * @GDK_ANCHOR_RESIZE: allow resizing surface on both axes
 *
 * Positioning hints for aligning a surface relative to a rectangle.
 *
 * These hints determine how the surface should be positioned in the case that
 * the surface would fall off-screen if placed in its ideal position.
 *
 * For example, %GDK_ANCHOR_FLIP_X will replace %GDK_GRAVITY_NORTH_WEST with
 * %GDK_GRAVITY_NORTH_EAST and vice versa if the surface extends beyond the left
 * or right edges of the monitor.
 *
 * If %GDK_ANCHOR_SLIDE_X is set, the surface can be shifted horizontally to fit
 * on-screen. If %GDK_ANCHOR_RESIZE_X is set, the surface can be shrunken
 * horizontally to fit.
 *
 * In general, when multiple flags are set, flipping should take precedence over
 * sliding, which should take precedence over resizing.
 */
typedef enum
{
  GDK_ANCHOR_FLIP_X   = 1 << 0,
  GDK_ANCHOR_FLIP_Y   = 1 << 1,
  GDK_ANCHOR_SLIDE_X  = 1 << 2,
  GDK_ANCHOR_SLIDE_Y  = 1 << 3,
  GDK_ANCHOR_RESIZE_X = 1 << 4,
  GDK_ANCHOR_RESIZE_Y = 1 << 5,
  GDK_ANCHOR_FLIP     = GDK_ANCHOR_FLIP_X | GDK_ANCHOR_FLIP_Y,
  GDK_ANCHOR_SLIDE    = GDK_ANCHOR_SLIDE_X | GDK_ANCHOR_SLIDE_Y,
  GDK_ANCHOR_RESIZE   = GDK_ANCHOR_RESIZE_X | GDK_ANCHOR_RESIZE_Y
} GdkAnchorHints;

/**
 * GdkSurfaceEdge:
 * @GDK_SURFACE_EDGE_NORTH_WEST: the top left corner.
 * @GDK_SURFACE_EDGE_NORTH: the top edge.
 * @GDK_SURFACE_EDGE_NORTH_EAST: the top right corner.
 * @GDK_SURFACE_EDGE_WEST: the left edge.
 * @GDK_SURFACE_EDGE_EAST: the right edge.
 * @GDK_SURFACE_EDGE_SOUTH_WEST: the lower left corner.
 * @GDK_SURFACE_EDGE_SOUTH: the lower edge.
 * @GDK_SURFACE_EDGE_SOUTH_EAST: the lower right corner.
 *
 * Determines a surface edge or corner.
 */
typedef enum
{
  GDK_SURFACE_EDGE_NORTH_WEST,
  GDK_SURFACE_EDGE_NORTH,
  GDK_SURFACE_EDGE_NORTH_EAST,
  GDK_SURFACE_EDGE_WEST,
  GDK_SURFACE_EDGE_EAST,
  GDK_SURFACE_EDGE_SOUTH_WEST,
  GDK_SURFACE_EDGE_SOUTH,
  GDK_SURFACE_EDGE_SOUTH_EAST  
} GdkSurfaceEdge;

/**
 * GdkFullscreenMode:
 * @GDK_FULLSCREEN_ON_CURRENT_MONITOR: Fullscreen on current monitor only.
 * @GDK_FULLSCREEN_ON_ALL_MONITORS: Span across all monitors when fullscreen.
 *
 * Indicates which monitor (in a multi-head setup) a surface should span over
 * when in fullscreen mode.
 **/
typedef enum
{
  GDK_FULLSCREEN_ON_CURRENT_MONITOR,
  GDK_FULLSCREEN_ON_ALL_MONITORS
} GdkFullscreenMode;

/**
 * GdkGeometry:
 * @min_width: minimum width of surface (or -1 to use requisition, with
 *  #GtkWindow only)
 * @min_height: minimum height of surface (or -1 to use requisition, with
 *  #GtkWindow only)
 * @max_width: maximum width of surface (or -1 to use requisition, with
 *  #GtkWindow only)
 * @max_height: maximum height of surface (or -1 to use requisition, with
 *  #GtkWindow only)
 * @base_width: allowed surface widths are @base_width + @width_inc * N where N
 *  is any integer (-1 allowed with #GtkWindow)
 * @base_height: allowed surface widths are @base_height + @height_inc * N where
 *  N is any integer (-1 allowed with #GtkWindow)
 * @width_inc: width resize increment
 * @height_inc: height resize increment
 * @min_aspect: minimum width/height ratio
 * @max_aspect: maximum width/height ratio
 * @win_gravity: surface gravity, see gtk_window_set_gravity()
 *
 * The #GdkGeometry struct gives the window manager information about
 * a surface’s geometry constraints. Normally you would set these on
 * the GTK+ level using gtk_window_set_geometry_hints(). #GtkWindow
 * then sets the hints on the #GdkSurface it creates.
 *
 * gdk_surface_set_geometry_hints() expects the hints to be fully valid already
 * and simply passes them to the window manager; in contrast,
 * gtk_window_set_geometry_hints() performs some interpretation. For example,
 * #GtkWindow will apply the hints to the geometry widget instead of the
 * toplevel window, if you set a geometry widget. Also, the
 * @min_width/@min_height/@max_width/@max_height fields may be set to -1, and
 * #GtkWindow will substitute the size request of the surface or geometry widget.
 * If the minimum size hint is not provided, #GtkWindow will use its requisition
 * as the minimum size. If the minimum size is provided and a geometry widget is
 * set, #GtkWindow will take the minimum size as the minimum size of the
 * geometry widget rather than the entire surface. The base size is treated
 * similarly.
 *
 * The canonical use-case for gtk_window_set_geometry_hints() is to get a
 * terminal widget to resize properly. Here, the terminal text area should be
 * the geometry widget; #GtkWindow will then automatically set the base size to
 * the size of other widgets in the terminal window, such as the menubar and
 * scrollbar. Then, the @width_inc and @height_inc fields should be set to the
 * size of one character in the terminal. Finally, the base size should be set
 * to the size of one character. The net effect is that the minimum size of the
 * terminal will have a 1x1 character terminal area, and only terminal sizes on
 * the “character grid” will be allowed.
 *
 * Here’s an example of how the terminal example would be implemented, assuming
 * a terminal area widget called “terminal” and a toplevel window “toplevel”:
 *
 * |[<!-- language="C" -->
 * 	GdkGeometry hints;
 *
 * 	hints.base_width = terminal->char_width;
 *         hints.base_height = terminal->char_height;
 *         hints.min_width = terminal->char_width;
 *         hints.min_height = terminal->char_height;
 *         hints.width_inc = terminal->char_width;
 *         hints.height_inc = terminal->char_height;
 *
 *  gtk_window_set_geometry_hints (GTK_WINDOW (toplevel),
 *                                 GTK_WIDGET (terminal),
 *                                 &hints,
 *                                 GDK_HINT_RESIZE_INC |
 *                                 GDK_HINT_MIN_SIZE |
 *                                 GDK_HINT_BASE_SIZE);
 * ]|
 *
 * The other useful fields are the @min_aspect and @max_aspect fields; these
 * contain a width/height ratio as a floating point number. If a geometry widget
 * is set, the aspect applies to the geometry widget rather than the entire
 * window. The most common use of these hints is probably to set @min_aspect and
 * @max_aspect to the same value, thus forcing the window to keep a constant
 * aspect ratio.
 */
struct _GdkGeometry
{
  gint min_width;
  gint min_height;
  gint max_width;
  gint max_height;
  gint base_width;
  gint base_height;
  gint width_inc;
  gint height_inc;
  gdouble min_aspect;
  gdouble max_aspect;
  GdkGravity win_gravity;
};

/**
 * GdkSurfaceState:
 * @GDK_SURFACE_STATE_WITHDRAWN: the surface is not shown
 * @GDK_SURFACE_STATE_ICONIFIED: the surface is minimized
 * @GDK_SURFACE_STATE_MAXIMIZED: the surface is maximized
 * @GDK_SURFACE_STATE_STICKY: the surface is sticky
 * @GDK_SURFACE_STATE_FULLSCREEN: the surface is maximized without decorations
 * @GDK_SURFACE_STATE_ABOVE: the surface is kept above other surfaces
 * @GDK_SURFACE_STATE_BELOW: the surface is kept below other surfaces
 * @GDK_SURFACE_STATE_FOCUSED: the surface is presented as focused (with active decorations)
 * @GDK_SURFACE_STATE_TILED: the surface is in a tiled state
 * @GDK_SURFACE_STATE_TOP_TILED: whether the top edge is tiled
 * @GDK_SURFACE_STATE_TOP_RESIZABLE: whether the top edge is resizable
 * @GDK_SURFACE_STATE_RIGHT_TILED: whether the right edge is tiled
 * @GDK_SURFACE_STATE_RIGHT_RESIZABLE: whether the right edge is resizable
 * @GDK_SURFACE_STATE_BOTTOM_TILED: whether the bottom edge is tiled
 * @GDK_SURFACE_STATE_BOTTOM_RESIZABLE: whether the bottom edge is resizable
 * @GDK_SURFACE_STATE_LEFT_TILED: whether the left edge is tiled
 * @GDK_SURFACE_STATE_LEFT_RESIZABLE: whether the left edge is resizable
 *
 * Specifies the state of a toplevel surface.
 *
 * On platforms that support information about individual edges, the %GDK_SURFACE_STATE_TILED
 * state will be set whenever any of the individual tiled states is set. On platforms
 * that lack that support, the tiled state will give an indication of tiledness without
 * any of the per-edge states being set.
 */
typedef enum
{
  GDK_SURFACE_STATE_WITHDRAWN        = 1 << 0,
  GDK_SURFACE_STATE_ICONIFIED        = 1 << 1,
  GDK_SURFACE_STATE_MAXIMIZED        = 1 << 2,
  GDK_SURFACE_STATE_STICKY           = 1 << 3,
  GDK_SURFACE_STATE_FULLSCREEN       = 1 << 4,
  GDK_SURFACE_STATE_ABOVE            = 1 << 5,
  GDK_SURFACE_STATE_BELOW            = 1 << 6,
  GDK_SURFACE_STATE_FOCUSED          = 1 << 7,
  GDK_SURFACE_STATE_TILED            = 1 << 8,
  GDK_SURFACE_STATE_TOP_TILED        = 1 << 9,
  GDK_SURFACE_STATE_TOP_RESIZABLE    = 1 << 10,
  GDK_SURFACE_STATE_RIGHT_TILED      = 1 << 11,
  GDK_SURFACE_STATE_RIGHT_RESIZABLE  = 1 << 12,
  GDK_SURFACE_STATE_BOTTOM_TILED     = 1 << 13,
  GDK_SURFACE_STATE_BOTTOM_RESIZABLE = 1 << 14,
  GDK_SURFACE_STATE_LEFT_TILED       = 1 << 15,
  GDK_SURFACE_STATE_LEFT_RESIZABLE   = 1 << 16
} GdkSurfaceState;


typedef struct _GdkSurfaceClass GdkSurfaceClass;

#define GDK_TYPE_SURFACE              (gdk_surface_get_type ())
#define GDK_SURFACE(object)           (G_TYPE_CHECK_INSTANCE_CAST ((object), GDK_TYPE_SURFACE, GdkSurface))
#define GDK_SURFACE_CLASS(klass)      (G_TYPE_CHECK_CLASS_CAST ((klass), GDK_TYPE_SURFACE, GdkSurfaceClass))
#define GDK_IS_SURFACE(object)        (G_TYPE_CHECK_INSTANCE_TYPE ((object), GDK_TYPE_SURFACE))
#define GDK_IS_SURFACE_CLASS(klass)   (G_TYPE_CHECK_CLASS_TYPE ((klass), GDK_TYPE_SURFACE))
#define GDK_SURFACE_GET_CLASS(obj)    (G_TYPE_INSTANCE_GET_CLASS ((obj), GDK_TYPE_SURFACE, GdkSurfaceClass))


struct _GdkSurfaceClass
{
  GObjectClass      parent_class;

  /* Padding for future expansion */
  void (*_gdk_reserved1) (void);
  void (*_gdk_reserved2) (void);
  void (*_gdk_reserved3) (void);
  void (*_gdk_reserved4) (void);
  void (*_gdk_reserved5) (void);
  void (*_gdk_reserved6) (void);
  void (*_gdk_reserved7) (void);
  void (*_gdk_reserved8) (void);
};

/* Surfaces
 */
GDK_AVAILABLE_IN_ALL
GType         gdk_surface_get_type              (void) G_GNUC_CONST;
GDK_AVAILABLE_IN_ALL
GdkSurface *   gdk_surface_new_toplevel         (GdkDisplay    *display,
                                                 int            width,
                                                 int            height);
GDK_AVAILABLE_IN_ALL
GdkSurface *   gdk_surface_new_popup            (GdkDisplay    *display,
                                                 const GdkRectangle *position);
GDK_AVAILABLE_IN_ALL
GdkSurface *   gdk_surface_new_temp             (GdkDisplay    *display);
GDK_AVAILABLE_IN_ALL
GdkSurface *   gdk_surface_new_child            (GdkSurface     *parent,
                                                 const GdkRectangle *position);

GDK_AVAILABLE_IN_ALL
void          gdk_surface_destroy               (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
GdkSurfaceType gdk_surface_get_surface_type     (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
gboolean      gdk_surface_is_destroyed          (GdkSurface     *surface);

GDK_AVAILABLE_IN_ALL
GdkDisplay *  gdk_surface_get_display           (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_show                  (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_hide                  (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_show_unraised         (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_move                  (GdkSurface     *surface,
                                                 gint           x,
                                                 gint           y);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_resize                (GdkSurface     *surface,
                                                 gint           width,
                                                 gint           height);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_move_resize           (GdkSurface     *surface,
                                                 gint           x,
                                                 gint           y,
                                                 gint           width,
                                                 gint           height);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_move_to_rect          (GdkSurface         *surface,
                                                 const GdkRectangle *rect,
                                                 GdkGravity          rect_anchor,
                                                 GdkGravity          surface_anchor,
                                                 GdkAnchorHints      anchor_hints,
                                                 gint                rect_anchor_dx,
                                                 gint                rect_anchor_dy);

GDK_AVAILABLE_IN_ALL
void          gdk_surface_raise                 (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_lower                 (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_restack               (GdkSurface     *surface,
                                                 GdkSurface     *sibling,
                                                 gboolean       above);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_focus                 (GdkSurface     *surface,
                                                 guint32        timestamp);
GDK_AVAILABLE_IN_ALL
gboolean      gdk_surface_get_accept_focus      (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_accept_focus      (GdkSurface     *surface,
                                                 gboolean       accept_focus);
GDK_AVAILABLE_IN_ALL
gboolean      gdk_surface_get_focus_on_map      (GdkSurface     *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_focus_on_map      (GdkSurface     *surface,
                                                 gboolean       focus_on_map);

GDK_AVAILABLE_IN_ALL
void gdk_surface_input_shape_combine_region (GdkSurface       *surface,
                                             const cairo_region_t *shape_region,
                                             gint             offset_x,
                                             gint             offset_y);
GDK_AVAILABLE_IN_ALL
void gdk_surface_set_child_input_shapes     (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void gdk_surface_merge_child_input_shapes   (GdkSurface       *surface);


GDK_AVAILABLE_IN_ALL
void gdk_surface_set_pass_through (GdkSurface *surface,
                                   gboolean   pass_through);
GDK_AVAILABLE_IN_ALL
gboolean gdk_surface_get_pass_through (GdkSurface *surface);

/*
 * Check if a surface has been shown, and whether all its
 * parents up to a toplevel have been shown, respectively.
 * Note that a surface that is_viewable below is not necessarily
 * viewable in the X sense.
 */
GDK_AVAILABLE_IN_ALL
gboolean gdk_surface_is_visible     (GdkSurface *surface);
GDK_AVAILABLE_IN_ALL
gboolean gdk_surface_is_viewable    (GdkSurface *surface);
GDK_AVAILABLE_IN_ALL
gboolean gdk_surface_is_input_only  (GdkSurface *surface);

GDK_AVAILABLE_IN_ALL
GdkSurfaceState gdk_surface_get_state (GdkSurface *surface);


/* GdkSurface */

GDK_AVAILABLE_IN_ALL
gboolean      gdk_surface_has_native         (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void              gdk_surface_set_type_hint (GdkSurface        *surface,
                                             GdkSurfaceTypeHint hint);
GDK_AVAILABLE_IN_ALL
GdkSurfaceTypeHint gdk_surface_get_type_hint (GdkSurface        *surface);

GDK_AVAILABLE_IN_ALL
gboolean      gdk_surface_get_modal_hint   (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_modal_hint   (GdkSurface       *surface,
                                            gboolean         modal);

GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_geometry_hints (GdkSurface          *surface,
                                              const GdkGeometry  *geometry,
                                              GdkSurfaceHints      geom_mask);

GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_title         (GdkSurface   *surface,
                                             const gchar  *title);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_startup_id    (GdkSurface     *surface,
                                             const gchar   *startup_id);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_transient_for (GdkSurface     *surface,
                                             GdkSurface     *parent);

GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_cursor     (GdkSurface      *surface,
                                          GdkCursor       *cursor);
GDK_AVAILABLE_IN_ALL
GdkCursor    *gdk_surface_get_cursor      (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_device_cursor (GdkSurface   *surface,
                                             GdkDevice     *device,
                                             GdkCursor     *cursor);
GDK_AVAILABLE_IN_ALL
GdkCursor    *gdk_surface_get_device_cursor (GdkSurface     *surface,
                                             GdkDevice     *device);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_get_geometry   (GdkSurface      *surface,
                                          gint            *x,
                                          gint            *y,
                                          gint            *width,
                                          gint            *height);
GDK_AVAILABLE_IN_ALL
int           gdk_surface_get_width       (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
int           gdk_surface_get_height      (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_get_position   (GdkSurface      *surface,
                                          gint            *x,
                                          gint            *y);
GDK_AVAILABLE_IN_ALL
gint          gdk_surface_get_origin     (GdkSurface      *surface,
                                          gint            *x,
                                          gint            *y);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_get_root_coords (GdkSurface     *surface,
                                           gint             x,
                                           gint             y,
                                           gint            *root_x,
                                           gint            *root_y);
GDK_AVAILABLE_IN_ALL
void       gdk_surface_coords_to_parent   (GdkSurface       *surface,
                                           gdouble          x,
                                           gdouble          y,
                                           gdouble         *parent_x,
                                           gdouble         *parent_y);
GDK_AVAILABLE_IN_ALL
void       gdk_surface_coords_from_parent (GdkSurface       *surface,
                                           gdouble          parent_x,
                                           gdouble          parent_y,
                                           gdouble         *x,
                                           gdouble         *y);

GDK_AVAILABLE_IN_ALL
void          gdk_surface_get_root_origin (GdkSurface     *surface,
                                           gint            *x,
                                           gint            *y);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_get_frame_extents (GdkSurface     *surface,
                                             GdkRectangle  *rect);

GDK_AVAILABLE_IN_ALL
gint          gdk_surface_get_scale_factor  (GdkSurface     *surface);

GDK_AVAILABLE_IN_ALL
GdkSurface *   gdk_surface_get_device_position (GdkSurface      *surface,
                                                GdkDevice       *device,
                                                double          *x,
                                                double          *y,
                                                GdkModifierType *mask);
GDK_AVAILABLE_IN_ALL
GdkSurface *   gdk_surface_get_parent      (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
GdkSurface *   gdk_surface_get_toplevel    (GdkSurface       *surface);

GDK_AVAILABLE_IN_ALL
GList *       gdk_surface_get_children   (GdkSurface      *surface);
GDK_AVAILABLE_IN_ALL
GList *       gdk_surface_peek_children   (GdkSurface       *surface);

GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_icon_list   (GdkSurface       *surface,
                                           GList           *surfaces);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_icon_name  (GdkSurface      *surface,
                                          const gchar     *name);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_decorations (GdkSurface     *surface,
                                           GdkWMDecoration  decorations);
GDK_AVAILABLE_IN_ALL
gboolean      gdk_surface_get_decorations (GdkSurface       *surface,
                                           GdkWMDecoration *decorations);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_functions  (GdkSurface      *surface,
                                          GdkWMFunction    functions);

GDK_AVAILABLE_IN_ALL
cairo_surface_t *
              gdk_surface_create_similar_surface (GdkSurface *surface,
                                                  cairo_content_t  content,
                                                  int              width,
                                                  int              height);

GDK_AVAILABLE_IN_ALL
void          gdk_surface_beep            (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_iconify         (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_deiconify       (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_stick           (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_unstick         (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_maximize        (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_unmaximize      (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_fullscreen      (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_fullscreen_on_monitor (GdkSurface      *surface,
                                                 GdkMonitor     *monitor);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_fullscreen_mode (GdkSurface   *surface,
                                               GdkFullscreenMode mode);
GDK_AVAILABLE_IN_ALL
GdkFullscreenMode
              gdk_surface_get_fullscreen_mode (GdkSurface   *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_unfullscreen    (GdkSurface       *surface);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_keep_above  (GdkSurface       *surface,
                                           gboolean         setting);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_keep_below  (GdkSurface       *surface,
                                           gboolean         setting);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_set_opacity     (GdkSurface       *surface,
                                           gdouble          opacity);
GDK_AVAILABLE_IN_ALL
void          gdk_surface_register_dnd    (GdkSurface       *surface);

GDK_AVAILABLE_IN_ALL
void gdk_surface_begin_resize_drag            (GdkSurface     *surface,
                                               GdkSurfaceEdge  edge,
                                               gint            button,
                                               gint            x,
                                               gint            y,
                                               guint32         timestamp);
GDK_AVAILABLE_IN_ALL
void gdk_surface_begin_resize_drag_for_device (GdkSurface     *surface,
                                               GdkSurfaceEdge  edge,
                                               GdkDevice      *device,
                                               gint            button,
                                               gint            x,
                                               gint            y,
                                               guint32         timestamp);
GDK_AVAILABLE_IN_ALL
void gdk_surface_begin_move_drag              (GdkSurface     *surface,
                                               gint            button,
                                               gint            x,
                                               gint            y,
                                               guint32         timestamp);
GDK_AVAILABLE_IN_ALL
void gdk_surface_begin_move_drag_for_device   (GdkSurface     *surface,
                                               GdkDevice      *device,
                                               gint            button,
                                               gint            x,
                                               gint            y,
                                               guint32         timestamp);

/* Interface for dirty-region queueing */
GDK_AVAILABLE_IN_ALL
void       gdk_surface_queue_expose              (GdkSurface          *surface);

GDK_AVAILABLE_IN_ALL
void       gdk_surface_freeze_updates      (GdkSurface    *surface);
GDK_AVAILABLE_IN_ALL
void       gdk_surface_thaw_updates        (GdkSurface    *surface);

GDK_AVAILABLE_IN_ALL
void       gdk_surface_constrain_size      (GdkGeometry    *geometry,
                                            GdkSurfaceHints  flags,
                                            gint            width,
                                            gint            height,
                                            gint           *new_width,
                                            gint           *new_height);

/* Multidevice support */
GDK_AVAILABLE_IN_ALL
void       gdk_surface_set_support_multidevice (GdkSurface *surface,
                                                gboolean   support_multidevice);
GDK_AVAILABLE_IN_ALL
gboolean   gdk_surface_get_support_multidevice (GdkSurface *surface);

/* Frame clock */
GDK_AVAILABLE_IN_ALL
GdkFrameClock* gdk_surface_get_frame_clock      (GdkSurface     *surface);

GDK_AVAILABLE_IN_ALL
void       gdk_surface_set_opaque_region        (GdkSurface      *surface,
                                                 cairo_region_t *region);

GDK_AVAILABLE_IN_ALL
void       gdk_surface_set_shadow_width         (GdkSurface      *surface,
                                                 gint            left,
                                                 gint            right,
                                                 gint            top,
                                                 gint            bottom);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_surface_show_window_menu          (GdkSurface      *surface,
                                                 GdkEvent       *event);

GDK_AVAILABLE_IN_ALL
GdkCairoContext *gdk_surface_create_cairo_context(GdkSurface    *surface);
GDK_AVAILABLE_IN_ALL
GdkGLContext * gdk_surface_create_gl_context    (GdkSurface     *surface,
                                                 GError        **error);
GDK_AVAILABLE_IN_ALL
GdkVulkanContext *
               gdk_surface_create_vulkan_context(GdkSurface     *surface,
                                                 GError        **error);

G_END_DECLS

#endif /* __GDK_SURFACE_H__ */
