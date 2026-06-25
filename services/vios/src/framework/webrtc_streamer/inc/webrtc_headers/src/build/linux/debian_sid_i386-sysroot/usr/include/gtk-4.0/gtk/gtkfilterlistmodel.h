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

#ifndef __GTK_FILTER_LIST_MODEL_H__
#define __GTK_FILTER_LIST_MODEL_H__


#if !defined (__GTK_H_INSIDE__) && !defined (GTK_COMPILATION)
#error "Only <gtk/gtk.h> can be included directly."
#endif

#include <gio/gio.h>
#include <gtk/gtkwidget.h>


G_BEGIN_DECLS

#define GTK_TYPE_FILTER_LIST_MODEL (gtk_filter_list_model_get_type ())

GDK_AVAILABLE_IN_ALL
G_DECLARE_FINAL_TYPE (GtkFilterListModel, gtk_filter_list_model, GTK, FILTER_LIST_MODEL, GObject)

/**
 * GtkFilterListModelFilterFunc:
 * @item: (type GObject): The item that may be filtered
 * @user_data: user data
 *
 * User function that is called to determine if the @item of the original model should be visible.
 * If it should be visible, this function must return %TRUE. If the model should filter out the
 * @item, %FALSE must be returned.
 *
 * Returns: %TRUE to keep the item around
 */
typedef gboolean (* GtkFilterListModelFilterFunc) (gpointer item, gpointer user_data);

GDK_AVAILABLE_IN_ALL
GtkFilterListModel *    gtk_filter_list_model_new               (GListModel             *model,
                                                                 GtkFilterListModelFilterFunc filter_func,
                                                                 gpointer                user_data,
                                                                 GDestroyNotify          user_destroy);
GDK_AVAILABLE_IN_ALL
GtkFilterListModel *    gtk_filter_list_model_new_for_type      (GType                   item_type);

GDK_AVAILABLE_IN_ALL
void                    gtk_filter_list_model_set_filter_func   (GtkFilterListModel     *self,
                                                                 GtkFilterListModelFilterFunc filter_func,
                                                                 gpointer                user_data,
                                                                 GDestroyNotify          user_destroy);
GDK_AVAILABLE_IN_ALL
void                    gtk_filter_list_model_set_model         (GtkFilterListModel     *self,
                                                                 GListModel             *model);
GDK_AVAILABLE_IN_ALL
GListModel *            gtk_filter_list_model_get_model         (GtkFilterListModel     *self);
GDK_AVAILABLE_IN_ALL
gboolean                gtk_filter_list_model_has_filter        (GtkFilterListModel     *self);

GDK_AVAILABLE_IN_ALL
void                    gtk_filter_list_model_refilter          (GtkFilterListModel     *self);

G_END_DECLS

#endif /* __GTK_FILTER_LIST_MODEL_H__ */
