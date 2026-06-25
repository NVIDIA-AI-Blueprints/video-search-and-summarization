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

#ifndef __GDK_TYPES_H__
#define __GDK_TYPES_H__

#if !defined (__GDK_H_INSIDE__) && !defined (GDK_COMPILATION)
#error "Only <gdk/gdk.h> can be included directly."
#endif

/* GDK uses "glib". (And so does GTK).
 */
#include <glib.h>
#include <glib-object.h>
#include <gio/gio.h>
#include <cairo.h>
#include <pango/pango.h>

/* The system specific file gdkconfig.h contains such configuration
 * settings that are needed not only when compiling GDK (or GTK)
 * itself, but also occasionally when compiling programs that use GDK
 * (or GTK). One such setting is what windowing API backend is in use.
 */
#include <gdk/gdkconfig.h>

/* some common magic values */

/**
 * GDK_CURRENT_TIME:
 *
 * Represents the current time, and can be used anywhere a time is expected.
 */
#define GDK_CURRENT_TIME     0L

/**
 * GDK_PARENT_RELATIVE:
 *
 * A special value, indicating that the background
 * for a surface should be inherited from the parent surface.
 */
#define GDK_PARENT_RELATIVE  1L



G_BEGIN_DECLS


/**
 * GdkPoint:
 * @x: the x coordinate of the point
 * @y: the y coordinate of the point
 *
 * Defines the x and y coordinates of a point.
 */
struct _GdkPoint
{
  gint x;
  gint y;
};
typedef struct _GdkPoint              GdkPoint;

/**
 * GdkRectangle:
 * @x: the x coordinate of the top left corner
 * @y: the y coordinate of the top left corner
 * @width: the width of the rectangle
 * @height: the height of the rectangle
 *
 * Defines the position and size of a rectangle. It is identical to
 * #cairo_rectangle_int_t.
 */
#ifdef __GI_SCANNER__
/* The introspection scanner is currently unable to lookup how
 * cairo_rectangle_int_t is actually defined. This prevents
 * introspection data for the GdkRectangle type to include fields
 * descriptions. To workaround this issue, we define it with the same
 * content as cairo_rectangle_int_t, but only under the introspection
 * define.
 */
struct _GdkRectangle
{
    int x, y;
    int width, height;
};
typedef struct _GdkRectangle          GdkRectangle;
#else
typedef cairo_rectangle_int_t         GdkRectangle;
#endif

/**
 * GdkAtom:
 *
 * An opaque type representing a string as an index into a table
 * of strings on the X server.
 */
typedef const char                   *GdkAtom;

/* Forward declarations of commonly used types */
typedef struct _GdkRGBA               GdkRGBA;
typedef struct _GdkContentFormats     GdkContentFormats;
typedef struct _GdkContentProvider    GdkContentProvider;
typedef struct _GdkCursor             GdkCursor;
typedef struct _GdkTexture            GdkTexture;
typedef struct _GdkDevice             GdkDevice;
typedef struct _GdkDrag               GdkDrag;
typedef struct _GdkDrop               GdkDrop;

typedef struct _GdkClipboard          GdkClipboard;
typedef struct _GdkDisplayManager     GdkDisplayManager;
typedef struct _GdkDisplay            GdkDisplay;
typedef struct _GdkSurface             GdkSurface;
typedef struct _GdkKeymap             GdkKeymap;
typedef struct _GdkAppLaunchContext   GdkAppLaunchContext;
typedef struct _GdkSeat               GdkSeat;
typedef struct _GdkSnapshot           GdkSnapshot;

typedef struct _GdkDrawingContext     GdkDrawingContext;
typedef struct _GdkDrawContext        GdkDrawContext;
typedef struct _GdkCairoContext       GdkCairoContext;
typedef struct _GdkGLContext          GdkGLContext;
typedef struct _GdkVulkanContext      GdkVulkanContext;

/**
 * GdkByteOrder:
 * @GDK_LSB_FIRST: The values are stored with the least-significant byte
 *   first. For instance, the 32-bit value 0xffeecc would be stored
 *   in memory as 0xcc, 0xee, 0xff, 0x00.
 * @GDK_MSB_FIRST: The values are stored with the most-significant byte
 *   first. For instance, the 32-bit value 0xffeecc would be stored
 *   in memory as 0x00, 0xff, 0xee, 0xcc.
 *
 * A set of values describing the possible byte-orders
 * for storing pixel values in memory.
 */
typedef enum
{
  GDK_LSB_FIRST,
  GDK_MSB_FIRST
} GdkByteOrder;

/* Types of modifiers.
 */
/**
 * GdkModifierType:
 * @GDK_SHIFT_MASK: the Shift key.
 * @GDK_LOCK_MASK: a Lock key (depending on the modifier mapping of the
 *  X server this may either be CapsLock or ShiftLock).
 * @GDK_CONTROL_MASK: the Control key.
 * @GDK_MOD1_MASK: the fourth modifier key (it depends on the modifier
 *  mapping of the X server which key is interpreted as this modifier, but
 *  normally it is the Alt key).
 * @GDK_MOD2_MASK: the fifth modifier key (it depends on the modifier
 *  mapping of the X server which key is interpreted as this modifier).
 * @GDK_MOD3_MASK: the sixth modifier key (it depends on the modifier
 *  mapping of the X server which key is interpreted as this modifier).
 * @GDK_MOD4_MASK: the seventh modifier key (it depends on the modifier
 *  mapping of the X server which key is interpreted as this modifier).
 * @GDK_MOD5_MASK: the eighth modifier key (it depends on the modifier
 *  mapping of the X server which key is interpreted as this modifier).
 * @GDK_BUTTON1_MASK: the first mouse button.
 * @GDK_BUTTON2_MASK: the second mouse button.
 * @GDK_BUTTON3_MASK: the third mouse button.
 * @GDK_BUTTON4_MASK: the fourth mouse button.
 * @GDK_BUTTON5_MASK: the fifth mouse button.
 * @GDK_MODIFIER_RESERVED_13_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_14_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_15_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_16_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_17_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_18_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_19_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_20_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_21_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_22_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_23_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_24_MASK: A reserved bit flag; do not use in your own code
 * @GDK_MODIFIER_RESERVED_25_MASK: A reserved bit flag; do not use in your own code
 * @GDK_SUPER_MASK: the Super modifier
 * @GDK_HYPER_MASK: the Hyper modifier
 * @GDK_META_MASK: the Meta modifier
 * @GDK_MODIFIER_RESERVED_29_MASK: A reserved bit flag; do not use in your own code
 * @GDK_RELEASE_MASK: not used in GDK itself. GTK uses it to differentiate
 *  between (keyval, modifiers) pairs from key press and release events.
 * @GDK_MODIFIER_MASK: a mask covering all modifier types.
 *
 * A set of bit-flags to indicate the state of modifier keys and mouse buttons
 * in various event types. Typical modifier keys are Shift, Control, Meta,
 * Super, Hyper, Alt, Compose, Apple, CapsLock or ShiftLock.
 *
 * Like the X Window System, GDK supports 8 modifier keys and 5 mouse buttons.
 *
 * GDK recognizes which of the Meta, Super or Hyper keys are mapped
 * to Mod2 - Mod5, and indicates this by setting %GDK_SUPER_MASK,
 * %GDK_HYPER_MASK or %GDK_META_MASK in the state field of key events.
 *
 * Note that GDK may add internal values to events which include
 * reserved values such as %GDK_MODIFIER_RESERVED_13_MASK.  Your code
 * should preserve and ignore them.  You can use %GDK_MODIFIER_MASK to
 * remove all reserved values.
 *
 * Also note that the GDK X backend interprets button press events for button
 * 4-7 as scroll events, so %GDK_BUTTON4_MASK and %GDK_BUTTON5_MASK will never
 * be set.
 */
typedef enum
{
  GDK_SHIFT_MASK    = 1 << 0,
  GDK_LOCK_MASK     = 1 << 1,
  GDK_CONTROL_MASK  = 1 << 2,
  GDK_MOD1_MASK     = 1 << 3,
  GDK_MOD2_MASK     = 1 << 4,
  GDK_MOD3_MASK     = 1 << 5,
  GDK_MOD4_MASK     = 1 << 6,
  GDK_MOD5_MASK     = 1 << 7,
  GDK_BUTTON1_MASK  = 1 << 8,
  GDK_BUTTON2_MASK  = 1 << 9,
  GDK_BUTTON3_MASK  = 1 << 10,
  GDK_BUTTON4_MASK  = 1 << 11,
  GDK_BUTTON5_MASK  = 1 << 12,

  GDK_MODIFIER_RESERVED_13_MASK  = 1 << 13,
  GDK_MODIFIER_RESERVED_14_MASK  = 1 << 14,
  GDK_MODIFIER_RESERVED_15_MASK  = 1 << 15,
  GDK_MODIFIER_RESERVED_16_MASK  = 1 << 16,
  GDK_MODIFIER_RESERVED_17_MASK  = 1 << 17,
  GDK_MODIFIER_RESERVED_18_MASK  = 1 << 18,
  GDK_MODIFIER_RESERVED_19_MASK  = 1 << 19,
  GDK_MODIFIER_RESERVED_20_MASK  = 1 << 20,
  GDK_MODIFIER_RESERVED_21_MASK  = 1 << 21,
  GDK_MODIFIER_RESERVED_22_MASK  = 1 << 22,
  GDK_MODIFIER_RESERVED_23_MASK  = 1 << 23,
  GDK_MODIFIER_RESERVED_24_MASK  = 1 << 24,
  GDK_MODIFIER_RESERVED_25_MASK  = 1 << 25,

  /* The next few modifiers are used by XKB, so we skip to the end.
   * Bits 15 - 25 are currently unused. Bit 29 is used internally.
   */
  
  GDK_SUPER_MASK    = 1 << 26,
  GDK_HYPER_MASK    = 1 << 27,
  GDK_META_MASK     = 1 << 28,
  
  GDK_MODIFIER_RESERVED_29_MASK  = 1 << 29,

  GDK_RELEASE_MASK  = 1 << 30,

  /* Combination of GDK_SHIFT_MASK..GDK_BUTTON5_MASK + GDK_SUPER_MASK
     + GDK_HYPER_MASK + GDK_META_MASK + GDK_RELEASE_MASK */
  GDK_MODIFIER_MASK = 0x5c001fff
} GdkModifierType;

/**
 * GdkModifierIntent:
 * @GDK_MODIFIER_INTENT_PRIMARY_ACCELERATOR: the primary modifier used to invoke
 *  menu accelerators.
 * @GDK_MODIFIER_INTENT_CONTEXT_MENU: the modifier used to invoke context menus.
 *  Note that mouse button 3 always triggers context menus. When this modifier
 *  is not 0, it additionally triggers context menus when used with mouse button 1.
 * @GDK_MODIFIER_INTENT_EXTEND_SELECTION: the modifier used to extend selections
 *  using `modifier`-click or `modifier`-cursor-key
 * @GDK_MODIFIER_INTENT_MODIFY_SELECTION: the modifier used to modify selections,
 *  which in most cases means toggling the clicked item into or out of the selection.
 * @GDK_MODIFIER_INTENT_NO_TEXT_INPUT: when any of these modifiers is pressed, the
 *  key event cannot produce a symbol directly. This is meant to be used for
 *  input methods, and for use cases like typeahead search.
 * @GDK_MODIFIER_INTENT_SHIFT_GROUP: the modifier that switches between keyboard
 *  groups (AltGr on X11/Windows and Option/Alt on OS X).
 * @GDK_MODIFIER_INTENT_DEFAULT_MOD_MASK: The set of modifier masks accepted
 * as modifiers in accelerators. Needed because Command is mapped to MOD2 on
 * OSX, which is widely used, but on X11 MOD2 is NumLock and using that for a
 * mod key is problematic at best.
 * Ref: https://bugzilla.gnome.org/show_bug.cgi?id=736125.
 *
 * This enum is used with gdk_keymap_get_modifier_mask()
 * in order to determine what modifiers the
 * currently used windowing system backend uses for particular
 * purposes. For example, on X11/Windows, the Control key is used for
 * invoking menu shortcuts (accelerators), whereas on Apple computers
 * it’s the Command key (which correspond to %GDK_CONTROL_MASK and
 * %GDK_MOD2_MASK, respectively).
 **/
typedef enum
{
  GDK_MODIFIER_INTENT_PRIMARY_ACCELERATOR,
  GDK_MODIFIER_INTENT_CONTEXT_MENU,
  GDK_MODIFIER_INTENT_EXTEND_SELECTION,
  GDK_MODIFIER_INTENT_MODIFY_SELECTION,
  GDK_MODIFIER_INTENT_NO_TEXT_INPUT,
  GDK_MODIFIER_INTENT_SHIFT_GROUP,
  GDK_MODIFIER_INTENT_DEFAULT_MOD_MASK,
} GdkModifierIntent;

/**
 * GdkGrabStatus:
 * @GDK_GRAB_SUCCESS: the resource was successfully grabbed.
 * @GDK_GRAB_ALREADY_GRABBED: the resource is actively grabbed by another client.
 * @GDK_GRAB_INVALID_TIME: the resource was grabbed more recently than the
 *  specified time.
 * @GDK_GRAB_NOT_VIEWABLE: the grab surface or the @confine_to surface are not
 *  viewable.
 * @GDK_GRAB_FROZEN: the resource is frozen by an active grab of another client.
 * @GDK_GRAB_FAILED: the grab failed for some other reason
 *
 * Returned by gdk_device_grab() to indicate success or the reason for the
 * failure of the grab attempt.
 */
typedef enum
{
  GDK_GRAB_SUCCESS         = 0,
  GDK_GRAB_ALREADY_GRABBED = 1,
  GDK_GRAB_INVALID_TIME    = 2,
  GDK_GRAB_NOT_VIEWABLE    = 3,
  GDK_GRAB_FROZEN          = 4,
  GDK_GRAB_FAILED          = 5
} GdkGrabStatus;

/**
 * GdkGrabOwnership:
 * @GDK_OWNERSHIP_NONE: All other devices’ events are allowed.
 * @GDK_OWNERSHIP_SURFACE: Other devices’ events are blocked for the grab surface.
 * @GDK_OWNERSHIP_APPLICATION: Other devices’ events are blocked for the whole application.
 *
 * Defines how device grabs interact with other devices.
 */
typedef enum
{
  GDK_OWNERSHIP_NONE,
  GDK_OWNERSHIP_SURFACE,
  GDK_OWNERSHIP_APPLICATION
} GdkGrabOwnership;

/**
 * GdkEventMask:
 * @GDK_EXPOSURE_MASK: receive expose events
 * @GDK_POINTER_MOTION_MASK: receive all pointer motion events
 * @GDK_BUTTON_MOTION_MASK: receive pointer motion events while any button is pressed
 * @GDK_BUTTON1_MOTION_MASK: receive pointer motion events while 1 button is pressed
 * @GDK_BUTTON2_MOTION_MASK: receive pointer motion events while 2 button is pressed
 * @GDK_BUTTON3_MOTION_MASK: receive pointer motion events while 3 button is pressed
 * @GDK_BUTTON_PRESS_MASK: receive button press events
 * @GDK_BUTTON_RELEASE_MASK: receive button release events
 * @GDK_KEY_PRESS_MASK: receive key press events
 * @GDK_KEY_RELEASE_MASK: receive key release events
 * @GDK_ENTER_NOTIFY_MASK: receive surface enter events
 * @GDK_LEAVE_NOTIFY_MASK: receive surface leave events
 * @GDK_FOCUS_CHANGE_MASK: receive focus change events
 * @GDK_STRUCTURE_MASK: receive events about surface configuration change
 * @GDK_PROPERTY_CHANGE_MASK: receive property change events
 * @GDK_PROXIMITY_IN_MASK: receive proximity in events
 * @GDK_PROXIMITY_OUT_MASK: receive proximity out events
 * @GDK_SUBSTRUCTURE_MASK: receive events about surface configuration changes of child surfaces
 * @GDK_SCROLL_MASK: receive scroll events
 * @GDK_TOUCH_MASK: receive touch events
 * @GDK_SMOOTH_SCROLL_MASK: receive smooth scrolling events
   @GDK_TOUCHPAD_GESTURE_MASK: receive touchpad gesture events
 * @GDK_TABLET_PAD_MASK: receive tablet pad events
 * @GDK_ALL_EVENTS_MASK: the combination of all the above event masks.
 *
 * A set of bit-flags to indicate which events a surface is to receive.
 * Most of these masks map onto one or more of the #GdkEventType event types
 * above.
 *
 * See the [input handling overview][chap-input-handling] for details of
 * [event masks][event-masks] and [event propagation][event-propagation].
 *
 * If %GDK_TOUCH_MASK is enabled, the surface will receive touch events
 * from touch-enabled devices. Those will come as sequences of #GdkEventTouch
 * with type %GDK_TOUCH_UPDATE, enclosed by two events with
 * type %GDK_TOUCH_BEGIN and %GDK_TOUCH_END (or %GDK_TOUCH_CANCEL).
 * gdk_event_get_event_sequence() returns the event sequence for these
 * events, so different sequences may be distinguished.
 */
typedef enum
{
  GDK_EXPOSURE_MASK             = 1 << 1,
  GDK_POINTER_MOTION_MASK       = 1 << 2,
  GDK_BUTTON_MOTION_MASK        = 1 << 4,
  GDK_BUTTON1_MOTION_MASK       = 1 << 5,
  GDK_BUTTON2_MOTION_MASK       = 1 << 6,
  GDK_BUTTON3_MOTION_MASK       = 1 << 7,
  GDK_BUTTON_PRESS_MASK         = 1 << 8,
  GDK_BUTTON_RELEASE_MASK       = 1 << 9,
  GDK_KEY_PRESS_MASK            = 1 << 10,
  GDK_KEY_RELEASE_MASK          = 1 << 11,
  GDK_ENTER_NOTIFY_MASK         = 1 << 12,
  GDK_LEAVE_NOTIFY_MASK         = 1 << 13,
  GDK_FOCUS_CHANGE_MASK         = 1 << 14,
  GDK_STRUCTURE_MASK            = 1 << 15,
  GDK_PROPERTY_CHANGE_MASK      = 1 << 16,
  GDK_PROXIMITY_IN_MASK         = 1 << 18,
  GDK_PROXIMITY_OUT_MASK        = 1 << 19,
  GDK_SUBSTRUCTURE_MASK         = 1 << 20,
  GDK_SCROLL_MASK               = 1 << 21,
  GDK_TOUCH_MASK                = 1 << 22,
  GDK_SMOOTH_SCROLL_MASK        = 1 << 23,
  GDK_TOUCHPAD_GESTURE_MASK     = 1 << 24,
  GDK_TABLET_PAD_MASK           = 1 << 25,
  GDK_ALL_EVENTS_MASK           = 0x3FFFFFE
} GdkEventMask;

/**
 * GdkGLError:
 * @GDK_GL_ERROR_NOT_AVAILABLE: OpenGL support is not available
 * @GDK_GL_ERROR_UNSUPPORTED_FORMAT: The requested visual format is not supported
 * @GDK_GL_ERROR_UNSUPPORTED_PROFILE: The requested profile is not supported
 * @GDK_GL_ERROR_COMPILATION_FAILED: The shader compilation failed
 * @GDK_GL_ERROR_LINK_FAILED: The shader linking failed
 *
 * Error enumeration for #GdkGLContext.
 */
typedef enum {
  GDK_GL_ERROR_NOT_AVAILABLE,
  GDK_GL_ERROR_UNSUPPORTED_FORMAT,
  GDK_GL_ERROR_UNSUPPORTED_PROFILE,
  GDK_GL_ERROR_COMPILATION_FAILED,
  GDK_GL_ERROR_LINK_FAILED
} GdkGLError;

/**
 * GdkVulkanError:
 * @GDK_VULKAN_ERROR_UNSUPPORTED: Vulkan is not supported on this backend or has not been
 *     compiled in.
 * @GDK_VULKAN_ERROR_NOT_AVAILABLE: Vulkan support is not available on this Surface
 *
 * Error enumeration for #GdkVulkanContext.
 */
typedef enum {
  GDK_VULKAN_ERROR_UNSUPPORTED,
  GDK_VULKAN_ERROR_NOT_AVAILABLE,
} GdkVulkanError;

/**
 * GdkSurfaceTypeHint:
 * @GDK_SURFACE_TYPE_HINT_NORMAL: Normal toplevel window.
 * @GDK_SURFACE_TYPE_HINT_DIALOG: Dialog window.
 * @GDK_SURFACE_TYPE_HINT_MENU: Window used to implement a menu; GTK uses
 *  this hint only for torn-off menus, see #GtkTearoffMenuItem.
 * @GDK_SURFACE_TYPE_HINT_TOOLBAR: Window used to implement toolbars.
 * @GDK_SURFACE_TYPE_HINT_SPLASHSCREEN: Window used to display a splash
 *  screen during application startup.
 * @GDK_SURFACE_TYPE_HINT_UTILITY: Utility windows which are not detached
 *  toolbars or dialogs.
 * @GDK_SURFACE_TYPE_HINT_DOCK: Used for creating dock or panel windows.
 * @GDK_SURFACE_TYPE_HINT_DESKTOP: Used for creating the desktop background
 *  window.
 * @GDK_SURFACE_TYPE_HINT_DROPDOWN_MENU: A menu that belongs to a menubar.
 * @GDK_SURFACE_TYPE_HINT_POPUP_MENU: A menu that does not belong to a menubar,
 *  e.g. a context menu.
 * @GDK_SURFACE_TYPE_HINT_TOOLTIP: A tooltip.
 * @GDK_SURFACE_TYPE_HINT_NOTIFICATION: A notification - typically a “bubble”
 *  that belongs to a status icon.
 * @GDK_SURFACE_TYPE_HINT_COMBO: A popup from a combo box.
 * @GDK_SURFACE_TYPE_HINT_DND: A window that is used to implement a DND cursor.
 *
 * These are hints for the window manager that indicate what type of function
 * the window has. The window manager can use this when determining decoration
 * and behaviour of the window. The hint must be set before mapping the window.
 *
 * See the [Extended Window Manager Hints](http://www.freedesktop.org/Standards/wm-spec)
 * specification for more details about window types.
 */
typedef enum
{
  GDK_SURFACE_TYPE_HINT_NORMAL,
  GDK_SURFACE_TYPE_HINT_DIALOG,
  GDK_SURFACE_TYPE_HINT_MENU,		/* Torn off menu */
  GDK_SURFACE_TYPE_HINT_TOOLBAR,
  GDK_SURFACE_TYPE_HINT_SPLASHSCREEN,
  GDK_SURFACE_TYPE_HINT_UTILITY,
  GDK_SURFACE_TYPE_HINT_DOCK,
  GDK_SURFACE_TYPE_HINT_DESKTOP,
  GDK_SURFACE_TYPE_HINT_DROPDOWN_MENU,	/* A drop down menu (from a menubar) */
  GDK_SURFACE_TYPE_HINT_POPUP_MENU,	/* A popup menu (from right-click) */
  GDK_SURFACE_TYPE_HINT_TOOLTIP,
  GDK_SURFACE_TYPE_HINT_NOTIFICATION,
  GDK_SURFACE_TYPE_HINT_COMBO,
  GDK_SURFACE_TYPE_HINT_DND
} GdkSurfaceTypeHint;

/**
 * GdkAxisUse:
 * @GDK_AXIS_IGNORE: the axis is ignored.
 * @GDK_AXIS_X: the axis is used as the x axis.
 * @GDK_AXIS_Y: the axis is used as the y axis.
 * @GDK_AXIS_PRESSURE: the axis is used for pressure information.
 * @GDK_AXIS_XTILT: the axis is used for x tilt information.
 * @GDK_AXIS_YTILT: the axis is used for y tilt information.
 * @GDK_AXIS_WHEEL: the axis is used for wheel information.
 * @GDK_AXIS_DISTANCE: the axis is used for pen/tablet distance information
 * @GDK_AXIS_ROTATION: the axis is used for pen rotation information
 * @GDK_AXIS_SLIDER: the axis is used for pen slider information
 * @GDK_AXIS_LAST: a constant equal to the numerically highest axis value.
 *
 * An enumeration describing the way in which a device
 * axis (valuator) maps onto the predefined valuator
 * types that GTK understands.
 *
 * Note that the X and Y axes are not really needed; pointer devices
 * report their location via the x/y members of events regardless. Whether
 * X and Y are present as axes depends on the GDK backend.
 */
typedef enum
{
  GDK_AXIS_IGNORE,
  GDK_AXIS_X,
  GDK_AXIS_Y,
  GDK_AXIS_PRESSURE,
  GDK_AXIS_XTILT,
  GDK_AXIS_YTILT,
  GDK_AXIS_WHEEL,
  GDK_AXIS_DISTANCE,
  GDK_AXIS_ROTATION,
  GDK_AXIS_SLIDER,
  GDK_AXIS_LAST
} GdkAxisUse;

/**
 * GdkAxisFlags:
 * @GDK_AXIS_FLAG_X: X axis is present
 * @GDK_AXIS_FLAG_Y: Y axis is present
 * @GDK_AXIS_FLAG_PRESSURE: Pressure axis is present
 * @GDK_AXIS_FLAG_XTILT: X tilt axis is present
 * @GDK_AXIS_FLAG_YTILT: Y tilt axis is present
 * @GDK_AXIS_FLAG_WHEEL: Wheel axis is present
 * @GDK_AXIS_FLAG_DISTANCE: Distance axis is present
 * @GDK_AXIS_FLAG_ROTATION: Z-axis rotation is present
 * @GDK_AXIS_FLAG_SLIDER: Slider axis is present
 *
 * Flags describing the current capabilities of a device/tool.
 */
typedef enum
{
  GDK_AXIS_FLAG_X        = 1 << GDK_AXIS_X,
  GDK_AXIS_FLAG_Y        = 1 << GDK_AXIS_Y,
  GDK_AXIS_FLAG_PRESSURE = 1 << GDK_AXIS_PRESSURE,
  GDK_AXIS_FLAG_XTILT    = 1 << GDK_AXIS_XTILT,
  GDK_AXIS_FLAG_YTILT    = 1 << GDK_AXIS_YTILT,
  GDK_AXIS_FLAG_WHEEL    = 1 << GDK_AXIS_WHEEL,
  GDK_AXIS_FLAG_DISTANCE = 1 << GDK_AXIS_DISTANCE,
  GDK_AXIS_FLAG_ROTATION = 1 << GDK_AXIS_ROTATION,
  GDK_AXIS_FLAG_SLIDER   = 1 << GDK_AXIS_SLIDER,
} GdkAxisFlags;

/**
 * GdkDragAction:
 * @GDK_ACTION_COPY: Copy the data.
 * @GDK_ACTION_MOVE: Move the data, i.e. first copy it, then delete
 *  it from the source using the DELETE target of the X selection protocol.
 * @GDK_ACTION_LINK: Add a link to the data. Note that this is only
 *  useful if source and destination agree on what it means.
 * @GDK_ACTION_ASK: Ask the user what to do with the data.
 *
 * Used in #GdkDrag to indicate what the destination
 * should do with the dropped data.
 */
typedef enum
{
  GDK_ACTION_COPY    = 1 << 0,
  GDK_ACTION_MOVE    = 1 << 1,
  GDK_ACTION_LINK    = 1 << 2,
  GDK_ACTION_ASK     = 1 << 3
} GdkDragAction;

/**
 * GDK_ACTION_ALL:
 *
 * Defines all possible DND actions. This can be used in gdk_drop_status()
 * messages when any drop can be accepted or a more specific drop method
 * is not yet known.
 */
#define GDK_ACTION_ALL (GDK_ACTION_COPY | GDK_ACTION_MOVE | GDK_ACTION_LINK)

G_END_DECLS

#endif /* __GDK_TYPES_H__ */
