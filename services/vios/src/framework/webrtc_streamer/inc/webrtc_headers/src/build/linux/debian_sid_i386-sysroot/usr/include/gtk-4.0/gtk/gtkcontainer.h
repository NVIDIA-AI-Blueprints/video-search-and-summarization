/* GTK - The GIMP Toolkit
 * Copyright (C) 1995-1997 Peter Mattis, Spencer Kimball and Josh MacDonald
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.	 See the GNU
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

#ifndef __GTK_CONTAINER_H__
#define __GTK_CONTAINER_H__


#if !defined (__GTK_H_INSIDE__) && !defined (GTK_COMPILATION)
#error "Only <gtk/gtk.h> can be included directly."
#endif

#include <gtk/gtkwidget.h>

G_BEGIN_DECLS

#define GTK_TYPE_CONTAINER              (gtk_container_get_type ())
#define GTK_CONTAINER(obj)              (G_TYPE_CHECK_INSTANCE_CAST ((obj), GTK_TYPE_CONTAINER, GtkContainer))
#define GTK_CONTAINER_CLASS(klass)      (G_TYPE_CHECK_CLASS_CAST ((klass), GTK_TYPE_CONTAINER, GtkContainerClass))
#define GTK_IS_CONTAINER(obj)           (G_TYPE_CHECK_INSTANCE_TYPE ((obj), GTK_TYPE_CONTAINER))
#define GTK_IS_CONTAINER_CLASS(klass)   (G_TYPE_CHECK_CLASS_TYPE ((klass), GTK_TYPE_CONTAINER))
#define GTK_CONTAINER_GET_CLASS(obj)    (G_TYPE_INSTANCE_GET_CLASS ((obj), GTK_TYPE_CONTAINER, GtkContainerClass))


typedef struct _GtkContainer              GtkContainer;
typedef struct _GtkContainerPrivate       GtkContainerPrivate;
typedef struct _GtkContainerClass         GtkContainerClass;

struct _GtkContainer
{
  GtkWidget widget;
};

/**
 * GtkContainerClass:
 * @parent_class: The parent class.
 * @add: Signal emitted when a widget is added to container.
 * @remove: Signal emitted when a widget is removed from container.
 * @forall: Invokes callback on each child of container. The callback handler
 *    may remove the child.
 * @set_focus_child: Sets the focused child of container.
 * @child_type: Returns the type of the children supported by the container.
 * @set_child_property: Set a property on a child of container.
 * @get_child_property: Get a property from a child of container.
 * @get_path_for_child: Get path representing entire widget hierarchy
 *    from the toplevel down to and including @child.
 *
 * Base class for containers.
 */
struct _GtkContainerClass
{
  GtkWidgetClass parent_class;

  /*< public >*/

  void    (*add)       		(GtkContainer	 *container,
				 GtkWidget	 *widget);
  void    (*remove)    		(GtkContainer	 *container,
				 GtkWidget	 *widget);
  void    (*forall)    		(GtkContainer	 *container,
				 GtkCallback	  callback,
				 gpointer	  callback_data);
  void    (*set_focus_child)	(GtkContainer	 *container,
				 GtkWidget	 *child);
  GType   (*child_type)		(GtkContainer	 *container);
  GtkWidgetPath * (*get_path_for_child) (GtkContainer *container,
                                         GtkWidget    *child);


  /*< private >*/

  /* Padding for future expansion */
  void (*_gtk_reserved1) (void);
  void (*_gtk_reserved2) (void);
  void (*_gtk_reserved3) (void);
  void (*_gtk_reserved4) (void);
  void (*_gtk_reserved5) (void);
  void (*_gtk_reserved6) (void);
  void (*_gtk_reserved7) (void);
  void (*_gtk_reserved8) (void);
};


/* Application-level methods */

GDK_AVAILABLE_IN_ALL
GType   gtk_container_get_type		 (void) G_GNUC_CONST;

GDK_AVAILABLE_IN_ALL
void    gtk_container_add		 (GtkContainer	   *container,
					  GtkWidget	   *widget);
GDK_AVAILABLE_IN_ALL
void    gtk_container_remove		 (GtkContainer	   *container,
					  GtkWidget	   *widget);

GDK_AVAILABLE_IN_ALL
void     gtk_container_foreach      (GtkContainer       *container,
				     GtkCallback         callback,
				     gpointer            callback_data);
GDK_AVAILABLE_IN_ALL
GList*   gtk_container_get_children     (GtkContainer       *container);

/* Widget-level methods */
GDK_AVAILABLE_IN_ALL
void   gtk_container_set_focus_vadjustment (GtkContainer     *container,
					    GtkAdjustment    *adjustment);
GDK_AVAILABLE_IN_ALL
GtkAdjustment *gtk_container_get_focus_vadjustment (GtkContainer *container);
GDK_AVAILABLE_IN_ALL
void   gtk_container_set_focus_hadjustment (GtkContainer     *container,
					    GtkAdjustment    *adjustment);
GDK_AVAILABLE_IN_ALL
GtkAdjustment *gtk_container_get_focus_hadjustment (GtkContainer *container);

GDK_AVAILABLE_IN_ALL
GType   gtk_container_child_type	   (GtkContainer     *container);

GDK_AVAILABLE_IN_ALL
void    gtk_container_forall		     (GtkContainer *container,
					      GtkCallback   callback,
					      gpointer	    callback_data);

GDK_AVAILABLE_IN_ALL
GtkWidgetPath * gtk_container_get_path_for_child (GtkContainer      *container,
                                                  GtkWidget         *child);

G_DEFINE_AUTOPTR_CLEANUP_FUNC(GtkContainer, g_object_unref)

G_END_DECLS

#endif /* __GTK_CONTAINER_H__ */
