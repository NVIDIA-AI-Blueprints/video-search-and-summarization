/* -*- Mode: C; c-file-style: "gnu"; tab-width: 8 -*- */
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

#ifndef __GTK_DND_H__
#define __GTK_DND_H__


#if !defined (__GTK_H_INSIDE__) && !defined (GTK_COMPILATION)
#error "Only <gtk/gtk.h> can be included directly."
#endif

#include <gtk/gtkwidget.h>
#include <gtk/gtkselection.h>


G_BEGIN_DECLS

/* Destination side */

GDK_AVAILABLE_IN_ALL
void gtk_drag_get_data (GtkWidget      *widget,
			GdkDrop        *drop,
			GdkAtom         target);

GDK_AVAILABLE_IN_ALL
GtkWidget *gtk_drag_get_source_widget (GdkDrag *drag);

GDK_AVAILABLE_IN_ALL
void gtk_drag_highlight   (GtkWidget  *widget);
GDK_AVAILABLE_IN_ALL
void gtk_drag_unhighlight (GtkWidget  *widget);

/* Source side */

GDK_AVAILABLE_IN_ALL
GdkDrag *gtk_drag_begin (GtkWidget         *widget,
                         GdkDevice         *device,
                         GdkContentFormats *targets,
                         GdkDragAction      actions,
                         gint               x,
                         gint               y);

GDK_AVAILABLE_IN_ALL
void gtk_drag_cancel           (GdkDrag *drag);

GDK_AVAILABLE_IN_ALL
void gtk_drag_set_icon_widget (GdkDrag        *drag,
			       GtkWidget      *widget,
			       gint            hot_x,
			       gint            hot_y);
GDK_AVAILABLE_IN_ALL
void gtk_drag_set_icon_paintable (GdkDrag     *drag,
			       GdkPaintable   *paintable,
                               int             hot_x,
                               int             hot_y);
GDK_AVAILABLE_IN_ALL
void gtk_drag_set_icon_name   (GdkDrag        *drag,
			       const gchar    *icon_name,
			       gint            hot_x,
			       gint            hot_y);
GDK_AVAILABLE_IN_ALL
void gtk_drag_set_icon_gicon  (GdkDrag        *drag,
			       GIcon          *icon,
			       gint            hot_x,
			       gint            hot_y);

GDK_AVAILABLE_IN_ALL
void gtk_drag_set_icon_default (GdkDrag       *drag);

GDK_AVAILABLE_IN_ALL
gboolean gtk_drag_check_threshold (GtkWidget *widget,
				   gint       start_x,
				   gint       start_y,
				   gint       current_x,
				   gint       current_y);


G_END_DECLS

#endif /* __GTK_DND_H__ */
