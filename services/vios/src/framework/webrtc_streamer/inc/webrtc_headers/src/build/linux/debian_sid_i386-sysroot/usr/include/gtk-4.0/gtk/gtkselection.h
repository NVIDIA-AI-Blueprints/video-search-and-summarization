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

#ifndef __GTK_SELECTION_H__
#define __GTK_SELECTION_H__

#if !defined (__GTK_H_INSIDE__) && !defined (GTK_COMPILATION)
#error "Only <gtk/gtk.h> can be included directly."
#endif

#include <gtk/gtkwidget.h>
#include <gtk/gtktextiter.h>

G_BEGIN_DECLS

#define GTK_TYPE_SELECTION_DATA (gtk_selection_data_get_type ())

GDK_AVAILABLE_IN_ALL
GdkContentFormats *     gtk_content_formats_add_text_targets      (GdkContentFormats *list) G_GNUC_WARN_UNUSED_RESULT;
GDK_AVAILABLE_IN_ALL
GdkContentFormats *     gtk_content_formats_add_image_targets     (GdkContentFormats *list,
                                                                   gboolean           writable) G_GNUC_WARN_UNUSED_RESULT;
GDK_AVAILABLE_IN_ALL
GdkContentFormats *     gtk_content_formats_add_uri_targets       (GdkContentFormats *list) G_GNUC_WARN_UNUSED_RESULT;

GDK_AVAILABLE_IN_ALL
GdkAtom       gtk_selection_data_get_target    (const GtkSelectionData *selection_data);
GDK_AVAILABLE_IN_ALL
GdkAtom       gtk_selection_data_get_data_type (const GtkSelectionData *selection_data);
GDK_AVAILABLE_IN_ALL
gint          gtk_selection_data_get_format    (const GtkSelectionData *selection_data);
GDK_AVAILABLE_IN_ALL
const guchar *gtk_selection_data_get_data      (const GtkSelectionData *selection_data);
GDK_AVAILABLE_IN_ALL
gint          gtk_selection_data_get_length    (const GtkSelectionData *selection_data);
GDK_AVAILABLE_IN_ALL
const guchar *gtk_selection_data_get_data_with_length
                                               (const GtkSelectionData *selection_data,
                                                gint                   *length);

GDK_AVAILABLE_IN_ALL
GdkDisplay   *gtk_selection_data_get_display   (const GtkSelectionData *selection_data);

GDK_AVAILABLE_IN_ALL
void     gtk_selection_data_set      (GtkSelectionData     *selection_data,
                                      GdkAtom               type,
                                      gint                  format,
                                      const guchar         *data,
                                      gint                  length);
GDK_AVAILABLE_IN_ALL
gboolean gtk_selection_data_set_text (GtkSelectionData     *selection_data,
                                      const gchar          *str,
                                      gint                  len);
GDK_AVAILABLE_IN_ALL
guchar * gtk_selection_data_get_text (const GtkSelectionData     *selection_data);
GDK_AVAILABLE_IN_ALL
gboolean gtk_selection_data_set_pixbuf   (GtkSelectionData  *selection_data,
                                          GdkPixbuf         *pixbuf);
GDK_AVAILABLE_IN_ALL
GdkPixbuf *gtk_selection_data_get_pixbuf (const GtkSelectionData  *selection_data);
GDK_AVAILABLE_IN_ALL
gboolean gtk_selection_data_set_texture (GtkSelectionData *selection_data,
                                         GdkTexture       *texture);
GDK_AVAILABLE_IN_ALL
GdkTexture *gtk_selection_data_get_texture (const GtkSelectionData *selection_data);
GDK_AVAILABLE_IN_ALL
gboolean gtk_selection_data_set_uris (GtkSelectionData     *selection_data,
                                      gchar               **uris);
GDK_AVAILABLE_IN_ALL
gchar  **gtk_selection_data_get_uris (const GtkSelectionData     *selection_data);

GDK_AVAILABLE_IN_ALL
gboolean gtk_selection_data_get_targets          (const GtkSelectionData  *selection_data,
                                                  GdkAtom          **targets,
                                                  gint              *n_atoms);
GDK_AVAILABLE_IN_ALL
gboolean gtk_selection_data_targets_include_text (const GtkSelectionData  *selection_data);
GDK_AVAILABLE_IN_ALL
gboolean gtk_selection_data_targets_include_image (const GtkSelectionData  *selection_data,
                                                   gboolean           writable);
GDK_AVAILABLE_IN_ALL
gboolean gtk_selection_data_targets_include_uri  (const GtkSelectionData  *selection_data);
GDK_AVAILABLE_IN_ALL
gboolean gtk_targets_include_text                (GdkAtom       *targets,
                                                  gint           n_targets);
GDK_AVAILABLE_IN_ALL
gboolean gtk_targets_include_image               (GdkAtom       *targets,
                                                  gint           n_targets,
                                                  gboolean       writable);
GDK_AVAILABLE_IN_ALL
gboolean gtk_targets_include_uri                 (GdkAtom       *targets,
                                                  gint           n_targets);


GDK_AVAILABLE_IN_ALL
GType             gtk_selection_data_get_type (void) G_GNUC_CONST;
GDK_AVAILABLE_IN_ALL
GtkSelectionData *gtk_selection_data_copy     (const GtkSelectionData *data);
GDK_AVAILABLE_IN_ALL
void              gtk_selection_data_free     (GtkSelectionData *data);

G_END_DECLS

#endif /* __GTK_SELECTION_H__ */
