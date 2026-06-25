/*
 * Copyright © 2018 Benjamin Otte
 *
 * This library is free software; you can redistribute it and/or
 * modify it under the terms of the GNU Lesser General Public
 * License as published by the Free Software Foundation; either
 * version 2.1 of the License, or (at your option) any later version.
 *
 * This library is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
 * Lesser General Public License for more details.
 *
 * You should have received a copy of the GNU Lesser General Public
 * License along with this library. If not, see <http://www.gnu.org/licenses/>.
 *
 * Authors: Benjamin Otte <otte@gnome.org>
 */

#ifndef __GTK_SELECTION_MODEL_H__
#define __GTK_SELECTION_MODEL_H__

#if !defined (__GTK_H_INSIDE__) && !defined (GTK_COMPILATION)
#error "Only <gtk/gtk.h> can be included directly."
#endif

#include <gdk/gdk.h>

G_BEGIN_DECLS

#define GTK_TYPE_SELECTION_MODEL       (gtk_selection_model_get_type ())
                                                      
GDK_AVAILABLE_IN_ALL
G_DECLARE_INTERFACE (GtkSelectionModel, gtk_selection_model, GTK, SELECTION_MODEL, GListModel)

/**
 * GtkSelectionModelInterface:
 * @is_selected: Return if the item at the given position is selected.
 * @select_item: Select the item in the given position. If the operation
 *     is known to fail, return %FALSE.
 * @unselect_item: Unselect the item in the given position. If the
 *     operation is known to fail, return %FALSE.
 * @select_range: Select all items in the given range. If the operation
 *     is unsupported or known to fail for all items, return %FALSE.
 * @unselect_range: Unselect all items in the given range. If the
 *     operation is unsupported or known to fail for all items, return
 *     %FALSE.
 * @select_all: Select all items in the model. If the operation is
 *     unsupported or known to fail for all items, return %FALSE.
 * @unselect_all: Unselect all items in the model. If the operation is
 *     unsupported or known to fail for all items, return %FALSE.
 *
 * The list of virtual functions for the #GtkSelectionModel interface.
 * All getter functions are mandatory to implement, but the model does
 * not need to implement any functions to support selecting or unselecting
 * items. Of course, if the model does not do that, it means that users
 * cannot select or unselect items in a list widgets using the model.
 */
struct _GtkSelectionModelInterface
{
  /*< private >*/
  GTypeInterface g_iface;

  /*< public >*/
  gboolean              (* is_selected)                         (GtkSelectionModel      *model,
                                                                 guint                   position);

  gboolean              (* select_item)                         (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 gboolean                exclusive);
  gboolean              (* unselect_item)                       (GtkSelectionModel      *model,
                                                                 guint                   position);
  gboolean              (* select_range)                        (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 guint                   n_items,
                                                                 gboolean                exclusive);
  gboolean              (* unselect_range)                      (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 guint                   n_items);
  gboolean              (* select_all)                          (GtkSelectionModel      *model);
  gboolean              (* unselect_all)                        (GtkSelectionModel      *model);
  void                  (* query_range)                         (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 guint                  *start_range,
                                                                 guint                  *n_items,
                                                                 gboolean               *selected);
};

GDK_AVAILABLE_IN_ALL
gboolean                gtk_selection_model_is_selected         (GtkSelectionModel      *model,
                                                                 guint                   position);

GDK_AVAILABLE_IN_ALL
gboolean                gtk_selection_model_select_item         (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 gboolean                exclusive);
GDK_AVAILABLE_IN_ALL
gboolean                gtk_selection_model_unselect_item       (GtkSelectionModel      *model,
                                                                 guint                   position);
GDK_AVAILABLE_IN_ALL
gboolean                gtk_selection_model_select_range        (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 guint                   n_items,
                                                                 gboolean                exclusive);
GDK_AVAILABLE_IN_ALL
gboolean                gtk_selection_model_unselect_range      (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 guint                   n_items);
GDK_AVAILABLE_IN_ALL
gboolean                gtk_selection_model_select_all          (GtkSelectionModel      *model);
GDK_AVAILABLE_IN_ALL
gboolean                gtk_selection_model_unselect_all        (GtkSelectionModel      *model);

GDK_AVAILABLE_IN_ALL
void                    gtk_selection_model_query_range         (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 guint                  *start_range,
                                                                 guint                  *n_items,
                                                                 gboolean               *selected);

/* for implementations only */
GDK_AVAILABLE_IN_ALL
void                    gtk_selection_model_selection_changed   (GtkSelectionModel      *model,
                                                                 guint                   position,
                                                                 guint                   n_items);

G_END_DECLS

#endif /* __GTK_SELECTION_MODEL_H__ */
