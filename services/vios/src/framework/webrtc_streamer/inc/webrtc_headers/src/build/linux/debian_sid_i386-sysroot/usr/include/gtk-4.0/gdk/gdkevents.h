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

#ifndef __GDK_EVENTS_H__
#define __GDK_EVENTS_H__

#if !defined (__GDK_H_INSIDE__) && !defined (GDK_COMPILATION)
#error "Only <gdk/gdk.h> can be included directly."
#endif

#include <gdk/gdkversionmacros.h>
#include <gdk/gdktypes.h>
#include <gdk/gdkdrag.h>
#include <gdk/gdkdevice.h>
#include <gdk/gdkdevicetool.h>

G_BEGIN_DECLS


#define GDK_TYPE_EVENT          (gdk_event_get_type ())
#define GDK_TYPE_EVENT_SEQUENCE (gdk_event_sequence_get_type ())

#define GDK_EVENT(object)       (G_TYPE_CHECK_INSTANCE_CAST ((object), GDK_TYPE_EVENT, GdkEvent))
#define GDK_IS_EVENT(object)    (G_TYPE_CHECK_INSTANCE_TYPE ((object), GDK_TYPE_EVENT))


/**
 * GDK_PRIORITY_EVENTS:
 *
 * This is the priority that events from the X server are given in the
 * [GLib Main Loop][glib-The-Main-Event-Loop].
 */
#define GDK_PRIORITY_EVENTS	(G_PRIORITY_DEFAULT)

/**
 * GDK_PRIORITY_REDRAW: (value 120)
 *
 * This is the priority that the idle handler processing surface updates
 * is given in the
 * [GLib Main Loop][glib-The-Main-Event-Loop].
 */
#define GDK_PRIORITY_REDRAW     (G_PRIORITY_HIGH_IDLE + 20)

/**
 * GDK_EVENT_PROPAGATE:
 *
 * Use this macro as the return value for continuing the propagation of
 * an event handler.
 */
#define GDK_EVENT_PROPAGATE     (FALSE)

/**
 * GDK_EVENT_STOP:
 *
 * Use this macro as the return value for stopping the propagation of
 * an event handler.
 */
#define GDK_EVENT_STOP          (TRUE)

/**
 * GDK_BUTTON_PRIMARY:
 *
 * The primary button. This is typically the left mouse button, or the
 * right button in a left-handed setup.
 */
#define GDK_BUTTON_PRIMARY      (1)

/**
 * GDK_BUTTON_MIDDLE:
 *
 * The middle button.
 */
#define GDK_BUTTON_MIDDLE       (2)

/**
 * GDK_BUTTON_SECONDARY:
 *
 * The secondary button. This is typically the right mouse button, or the
 * left button in a left-handed setup.
 */
#define GDK_BUTTON_SECONDARY    (3)



typedef struct _GdkEventAny	    GdkEventAny;
typedef struct _GdkEventMotion	    GdkEventMotion;
typedef struct _GdkEventButton	    GdkEventButton;
typedef struct _GdkEventTouch       GdkEventTouch;
typedef struct _GdkEventScroll      GdkEventScroll;  
typedef struct _GdkEventKey	    GdkEventKey;
typedef struct _GdkEventFocus	    GdkEventFocus;
typedef struct _GdkEventCrossing    GdkEventCrossing;
typedef struct _GdkEventConfigure   GdkEventConfigure;
typedef struct _GdkEventProximity   GdkEventProximity;
typedef struct _GdkEventDND         GdkEventDND;
typedef struct _GdkEventSetting     GdkEventSetting;
typedef struct _GdkEventGrabBroken  GdkEventGrabBroken;
typedef struct _GdkEventTouchpadSwipe GdkEventTouchpadSwipe;
typedef struct _GdkEventTouchpadPinch GdkEventTouchpadPinch;
typedef struct _GdkEventPadButton   GdkEventPadButton;
typedef struct _GdkEventPadAxis     GdkEventPadAxis;
typedef struct _GdkEventPadGroupMode GdkEventPadGroupMode;

typedef struct _GdkEventSequence    GdkEventSequence;
typedef union  _GdkEvent	    GdkEvent;

/**
 * GdkEventType:
 * @GDK_NOTHING: a special code to indicate a null event.
 * @GDK_DELETE: the window manager has requested that the toplevel surface be
 *   hidden or destroyed, usually when the user clicks on a special icon in the
 *   title bar.
 * @GDK_DESTROY: the surface has been destroyed.
 * @GDK_MOTION_NOTIFY: the pointer (usually a mouse) has moved.
 * @GDK_BUTTON_PRESS: a mouse button has been pressed.
 * @GDK_BUTTON_RELEASE: a mouse button has been released.
 * @GDK_KEY_PRESS: a key has been pressed.
 * @GDK_KEY_RELEASE: a key has been released.
 * @GDK_ENTER_NOTIFY: the pointer has entered the surface.
 * @GDK_LEAVE_NOTIFY: the pointer has left the surface.
 * @GDK_FOCUS_CHANGE: the keyboard focus has entered or left the surface.
 * @GDK_CONFIGURE: the size, position or stacking order of the surface has changed.
 *   Note that GTK+ discards these events for %GDK_SURFACE_CHILD surfaces.
 * @GDK_PROXIMITY_IN: an input device has moved into contact with a sensing
 *   surface (e.g. a touchscreen or graphics tablet).
 * @GDK_PROXIMITY_OUT: an input device has moved out of contact with a sensing
 *   surface.
 * @GDK_DRAG_ENTER: the mouse has entered the surface while a drag is in progress.
 * @GDK_DRAG_LEAVE: the mouse has left the surface while a drag is in progress.
 * @GDK_DRAG_MOTION: the mouse has moved in the surface while a drag is in
 *   progress.
 * @GDK_DROP_START: a drop operation onto the surface has started.
 * @GDK_SCROLL: the scroll wheel was turned
 * @GDK_GRAB_BROKEN: a pointer or keyboard grab was broken. This event type
 *   was added in 2.8.
 * @GDK_TOUCH_BEGIN: A new touch event sequence has just started. This event
 *   type was added in 3.4.
 * @GDK_TOUCH_UPDATE: A touch event sequence has been updated. This event type
 *   was added in 3.4.
 * @GDK_TOUCH_END: A touch event sequence has finished. This event type
 *   was added in 3.4.
 * @GDK_TOUCH_CANCEL: A touch event sequence has been canceled. This event type
 *   was added in 3.4.
 * @GDK_TOUCHPAD_SWIPE: A touchpad swipe gesture event, the current state
 *   is determined by its phase field. This event type was added in 3.18.
 * @GDK_TOUCHPAD_PINCH: A touchpad pinch gesture event, the current state
 *   is determined by its phase field. This event type was added in 3.18.
 * @GDK_PAD_BUTTON_PRESS: A tablet pad button press event. This event type
 *   was added in 3.22.
 * @GDK_PAD_BUTTON_RELEASE: A tablet pad button release event. This event type
 *   was added in 3.22.
 * @GDK_PAD_RING: A tablet pad axis event from a "ring". This event type was
 *   added in 3.22.
 * @GDK_PAD_STRIP: A tablet pad axis event from a "strip". This event type was
 *   added in 3.22.
 * @GDK_PAD_GROUP_MODE: A tablet pad group mode change. This event type was
 *   added in 3.22.
 * @GDK_EVENT_LAST: marks the end of the GdkEventType enumeration. Added in 2.18
 *
 * Specifies the type of the event.
 *
 * Do not confuse these events with the signals that GTK+ widgets emit.
 * Although many of these events result in corresponding signals being emitted,
 * the events are often transformed or filtered along the way.
 */
typedef enum
{
  GDK_NOTHING,
  GDK_DELETE,
  GDK_DESTROY,
  GDK_MOTION_NOTIFY,
  GDK_BUTTON_PRESS,
  GDK_BUTTON_RELEASE,
  GDK_KEY_PRESS,
  GDK_KEY_RELEASE,
  GDK_ENTER_NOTIFY,
  GDK_LEAVE_NOTIFY,
  GDK_FOCUS_CHANGE,
  GDK_CONFIGURE,
  GDK_PROXIMITY_IN,
  GDK_PROXIMITY_OUT,
  GDK_DRAG_ENTER,
  GDK_DRAG_LEAVE,
  GDK_DRAG_MOTION,
  GDK_DROP_START,
  GDK_SCROLL,
  GDK_GRAB_BROKEN,
  GDK_TOUCH_BEGIN,
  GDK_TOUCH_UPDATE,
  GDK_TOUCH_END,
  GDK_TOUCH_CANCEL,
  GDK_TOUCHPAD_SWIPE,
  GDK_TOUCHPAD_PINCH,
  GDK_PAD_BUTTON_PRESS,
  GDK_PAD_BUTTON_RELEASE,
  GDK_PAD_RING,
  GDK_PAD_STRIP,
  GDK_PAD_GROUP_MODE,
  GDK_EVENT_LAST        /* helper variable for decls */
} GdkEventType;

/**
 * GdkTouchpadGesturePhase:
 * @GDK_TOUCHPAD_GESTURE_PHASE_BEGIN: The gesture has begun.
 * @GDK_TOUCHPAD_GESTURE_PHASE_UPDATE: The gesture has been updated.
 * @GDK_TOUCHPAD_GESTURE_PHASE_END: The gesture was finished, changes
 *   should be permanently applied.
 * @GDK_TOUCHPAD_GESTURE_PHASE_CANCEL: The gesture was cancelled, all
 *   changes should be undone.
 *
 * Specifies the current state of a touchpad gesture. All gestures are
 * guaranteed to begin with an event with phase %GDK_TOUCHPAD_GESTURE_PHASE_BEGIN,
 * followed by 0 or several events with phase %GDK_TOUCHPAD_GESTURE_PHASE_UPDATE.
 *
 * A finished gesture may have 2 possible outcomes, an event with phase
 * %GDK_TOUCHPAD_GESTURE_PHASE_END will be emitted when the gesture is
 * considered successful, this should be used as the hint to perform any
 * permanent changes.

 * Cancelled gestures may be so for a variety of reasons, due to hardware
 * or the compositor, or due to the gesture recognition layers hinting the
 * gesture did not finish resolutely (eg. a 3rd finger being added during
 * a pinch gesture). In these cases, the last event will report the phase
 * %GDK_TOUCHPAD_GESTURE_PHASE_CANCEL, this should be used as a hint
 * to undo any visible/permanent changes that were done throughout the
 * progress of the gesture.
 */
typedef enum
{
  GDK_TOUCHPAD_GESTURE_PHASE_BEGIN,
  GDK_TOUCHPAD_GESTURE_PHASE_UPDATE,
  GDK_TOUCHPAD_GESTURE_PHASE_END,
  GDK_TOUCHPAD_GESTURE_PHASE_CANCEL
} GdkTouchpadGesturePhase;

/**
 * GdkScrollDirection:
 * @GDK_SCROLL_UP: the surface is scrolled up.
 * @GDK_SCROLL_DOWN: the surface is scrolled down.
 * @GDK_SCROLL_LEFT: the surface is scrolled to the left.
 * @GDK_SCROLL_RIGHT: the surface is scrolled to the right.
 * @GDK_SCROLL_SMOOTH: the scrolling is determined by the delta values
 *   in scroll events. See gdk_event_get_scroll_deltas()
 *
 * Specifies the direction for scroll events.
 */
typedef enum
{
  GDK_SCROLL_UP,
  GDK_SCROLL_DOWN,
  GDK_SCROLL_LEFT,
  GDK_SCROLL_RIGHT,
  GDK_SCROLL_SMOOTH
} GdkScrollDirection;

/**
 * GdkNotifyType:
 * @GDK_NOTIFY_ANCESTOR: the surface is entered from an ancestor or
 *   left towards an ancestor.
 * @GDK_NOTIFY_VIRTUAL: the pointer moves between an ancestor and an
 *   inferior of the surface.
 * @GDK_NOTIFY_INFERIOR: the surface is entered from an inferior or
 *   left towards an inferior.
 * @GDK_NOTIFY_NONLINEAR: the surface is entered from or left towards
 *   a surface which is neither an ancestor nor an inferior.
 * @GDK_NOTIFY_NONLINEAR_VIRTUAL: the pointer moves between two surfaces
 *   which are not ancestors of each other and the surface is part of
 *   the ancestor chain between one of these surfaces and their least
 *   common ancestor.
 * @GDK_NOTIFY_UNKNOWN: an unknown type of enter/leave event occurred.
 *
 * Specifies the kind of crossing for enter and leave events.
 *
 * See the X11 protocol specification of LeaveNotify for
 * full details of crossing event generation.
 */
typedef enum
{
  GDK_NOTIFY_ANCESTOR		= 0,
  GDK_NOTIFY_VIRTUAL		= 1,
  GDK_NOTIFY_INFERIOR		= 2,
  GDK_NOTIFY_NONLINEAR		= 3,
  GDK_NOTIFY_NONLINEAR_VIRTUAL	= 4,
  GDK_NOTIFY_UNKNOWN		= 5
} GdkNotifyType;

/**
 * GdkCrossingMode:
 * @GDK_CROSSING_NORMAL: crossing because of pointer motion.
 * @GDK_CROSSING_GRAB: crossing because a grab is activated.
 * @GDK_CROSSING_UNGRAB: crossing because a grab is deactivated.
 * @GDK_CROSSING_GTK_GRAB: crossing because a GTK+ grab is activated.
 * @GDK_CROSSING_GTK_UNGRAB: crossing because a GTK+ grab is deactivated.
 * @GDK_CROSSING_STATE_CHANGED: crossing because a GTK+ widget changed
 *   state (e.g. sensitivity).
 * @GDK_CROSSING_TOUCH_BEGIN: crossing because a touch sequence has begun,
 *   this event is synthetic as the pointer might have not left the surface.
 * @GDK_CROSSING_TOUCH_END: crossing because a touch sequence has ended,
 *   this event is synthetic as the pointer might have not left the surface.
 * @GDK_CROSSING_DEVICE_SWITCH: crossing because of a device switch (i.e.
 *   a mouse taking control of the pointer after a touch device), this event
 *   is synthetic as the pointer didn’t leave the surface.
 *
 * Specifies the crossing mode for enter and leave events.
 */
typedef enum
{
  GDK_CROSSING_NORMAL,
  GDK_CROSSING_GRAB,
  GDK_CROSSING_UNGRAB,
  GDK_CROSSING_GTK_GRAB,
  GDK_CROSSING_GTK_UNGRAB,
  GDK_CROSSING_STATE_CHANGED,
  GDK_CROSSING_TOUCH_BEGIN,
  GDK_CROSSING_TOUCH_END,
  GDK_CROSSING_DEVICE_SWITCH
} GdkCrossingMode;

GDK_AVAILABLE_IN_ALL
GType     gdk_event_get_type            (void) G_GNUC_CONST;

GDK_AVAILABLE_IN_ALL
GType     gdk_event_sequence_get_type   (void) G_GNUC_CONST;

GDK_AVAILABLE_IN_ALL
GdkEvent* gdk_event_new                 (GdkEventType    type);
GDK_AVAILABLE_IN_ALL
GdkEvent* gdk_event_copy     		(const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
GdkSurface *gdk_event_get_surface       (const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
guint32   gdk_event_get_time            (const GdkEvent  *event);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_state           (const GdkEvent  *event,
                                         GdkModifierType *state);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_coords		(const GdkEvent  *event,
					 gdouble	 *x_win,
					 gdouble	 *y_win);
GDK_AVAILABLE_IN_ALL
void      gdk_event_set_coords          (GdkEvent *event,
                                         gdouble   x,
                                         gdouble   y);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_root_coords	(const GdkEvent *event,
					 gdouble	*x_root,
					 gdouble	*y_root);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_button          (const GdkEvent *event,
                                         guint          *button);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_click_count     (const GdkEvent *event,
                                         guint          *click_count);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_keyval          (const GdkEvent *event,
                                         guint          *keyval);

GDK_AVAILABLE_IN_ALL
void      gdk_event_set_keyval          (GdkEvent *event,
                                         guint     keyval);

GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_keycode         (const GdkEvent *event,
                                         guint16        *keycode);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_key_is_modifier (const GdkEvent *event,
                                         gboolean       *is_modifier);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_key_group       (const GdkEvent *event,
                                         guint          *group);

GDK_AVAILABLE_IN_ALL
gboolean gdk_event_get_scroll_direction (const GdkEvent *event,
                                         GdkScrollDirection *direction);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_scroll_deltas   (const GdkEvent *event,
                                         gdouble         *delta_x,
                                         gdouble         *delta_y);

GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_is_scroll_stop_event (const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
gboolean  gdk_event_get_axis            (const GdkEvent  *event,
                                         GdkAxisUse       axis_use,
                                         gdouble         *value);
GDK_AVAILABLE_IN_ALL
void       gdk_event_set_device         (GdkEvent        *event,
                                         GdkDevice       *device);
GDK_AVAILABLE_IN_ALL
GdkDevice* gdk_event_get_device         (const GdkEvent  *event);
GDK_AVAILABLE_IN_ALL
void       gdk_event_set_source_device  (GdkEvent        *event,
                                         GdkDevice       *device);
GDK_AVAILABLE_IN_ALL
GdkDevice* gdk_event_get_source_device  (const GdkEvent  *event);
GDK_AVAILABLE_IN_ALL
gboolean   gdk_event_triggers_context_menu (const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
gboolean  gdk_events_get_distance       (GdkEvent        *event1,
                                         GdkEvent        *event2,
                                         gdouble         *distance);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_events_get_angle          (GdkEvent        *event1,
                                         GdkEvent        *event2,
                                         gdouble         *angle);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_events_get_center         (GdkEvent        *event1,
                                         GdkEvent        *event2,
                                         gdouble         *x,
                                         gdouble         *y);

GDK_AVAILABLE_IN_ALL
void       gdk_event_set_display        (GdkEvent        *event,
                                         GdkDisplay      *display);
GDK_AVAILABLE_IN_ALL
GdkDisplay *gdk_event_get_display       (const GdkEvent  *event);

GDK_AVAILABLE_IN_ALL
GdkEventSequence *gdk_event_get_event_sequence (const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
GdkEventType gdk_event_get_event_type   (const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
GdkSeat  *gdk_event_get_seat            (const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
void	  gdk_set_show_events		(gboolean	 show_events);
GDK_AVAILABLE_IN_ALL
gboolean  gdk_get_show_events		(void);

GDK_AVAILABLE_IN_ALL
GdkDeviceTool *gdk_event_get_device_tool (const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
void           gdk_event_set_device_tool (GdkEvent       *event,
                                          GdkDeviceTool  *tool);

GDK_AVAILABLE_IN_ALL
int            gdk_event_get_scancode    (GdkEvent *event);

GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_pointer_emulated (GdkEvent *event);

GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_is_sent       (const GdkEvent *event);

GDK_AVAILABLE_IN_ALL
GdkDrop *      gdk_event_get_drop (const GdkEvent  *event);

GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_crossing_mode (const GdkEvent  *event,
                                            GdkCrossingMode *mode);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_crossing_detail (const GdkEvent *event,
                                              GdkNotifyType  *detail);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_touchpad_gesture_phase (const GdkEvent          *event,
                                                     GdkTouchpadGesturePhase *phase);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_touchpad_gesture_n_fingers (const GdkEvent *event,
                                                         guint          *n_fingers);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_touchpad_deltas (const GdkEvent *event,
                                              double         *dx,
                                              double         *dy);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_touchpad_angle_delta (const GdkEvent *event,
                                                   double         *delta);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_touchpad_scale (const GdkEvent *event,
                                             double         *scale);

GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_touch_emulating_pointer (const GdkEvent *event,
                                                      gboolean       *emulating);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_grab_surface (const GdkEvent  *event,
                                           GdkSurface      **surface);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_focus_in (const GdkEvent *event,
                                       gboolean       *focus_in);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_pad_group_mode (const GdkEvent *event,
                                             guint          *group,
                                             guint          *mode);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_pad_button (const GdkEvent *event,
                                         guint          *button);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_pad_axis_value (const GdkEvent *event,
                                             guint          *index,
                                             gdouble        *value);
GDK_AVAILABLE_IN_ALL
gboolean       gdk_event_get_axes      (GdkEvent  *event,
                                        gdouble  **axes,
                                        guint     *n_axes);
GDK_AVAILABLE_IN_ALL
GList        * gdk_event_get_motion_history (const GdkEvent  *event);

G_END_DECLS

#endif /* __GDK_EVENTS_H__ */
