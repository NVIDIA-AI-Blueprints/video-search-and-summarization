/* GSK - The GTK Scene Kit
 *
 * Copyright 2016  Endless
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

#ifndef __GSK_RENDER_NODE_H__
#define __GSK_RENDER_NODE_H__

#if !defined (__GSK_H_INSIDE__) && !defined (GSK_COMPILATION)
#error "Only <gsk/gsk.h> can be included directly."
#endif

#include <gsk/gskroundedrect.h>
#include <gsk/gsktypes.h>
#include <gtk/css/gtkcss.h>

G_BEGIN_DECLS

#define GSK_TYPE_RENDER_NODE (gsk_render_node_get_type ())

#define GSK_IS_RENDER_NODE(obj) ((obj) != NULL)

#define GSK_SERIALIZATION_ERROR       (gsk_serialization_error_quark ())

typedef struct _GskRenderNode           GskRenderNode;
typedef struct _GskColorStop            GskColorStop;
typedef struct _GskShadow               GskShadow;

struct _GskColorStop
{
  double offset;
  GdkRGBA color;
};

struct _GskShadow
{
  GdkRGBA color;
  float dx;
  float dy;
  float radius;
};

typedef void           (* GskParseErrorFunc)                    (const GtkCssSection *section,
                                                                 const GError        *error,
                                                                 gpointer             user_data);

GDK_AVAILABLE_IN_ALL
GType                   gsk_render_node_get_type                (void) G_GNUC_CONST;

GDK_AVAILABLE_IN_ALL
GQuark                  gsk_serialization_error_quark           (void);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_render_node_ref                     (GskRenderNode *node);
GDK_AVAILABLE_IN_ALL
void                    gsk_render_node_unref                   (GskRenderNode *node);

GDK_AVAILABLE_IN_ALL
GskRenderNodeType       gsk_render_node_get_node_type           (GskRenderNode *node);

GDK_AVAILABLE_IN_ALL
void                    gsk_render_node_get_bounds              (GskRenderNode   *node,
                                                                 graphene_rect_t *bounds);

GDK_AVAILABLE_IN_ALL
void                    gsk_render_node_draw                    (GskRenderNode *node,
                                                                 cairo_t       *cr);

GDK_AVAILABLE_IN_ALL
GBytes *                gsk_render_node_serialize               (GskRenderNode *node);
GDK_AVAILABLE_IN_ALL
gboolean                gsk_render_node_write_to_file           (GskRenderNode *node,
                                                                 const char    *filename,
                                                                 GError       **error);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_render_node_deserialize             (GBytes            *bytes,
                                                                 GskParseErrorFunc  error_func,
                                                                 gpointer           user_data);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_debug_node_new                      (GskRenderNode            *child,
                                                                 char                     *message);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_debug_node_get_child                (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const char *            gsk_debug_node_get_message              (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_color_node_new                      (const GdkRGBA            *rgba,
                                                                 const graphene_rect_t    *bounds);
GDK_AVAILABLE_IN_ALL
const GdkRGBA *         gsk_color_node_peek_color               (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_texture_node_new                    (GdkTexture               *texture,
                                                                 const graphene_rect_t    *bounds);
GDK_AVAILABLE_IN_ALL
GdkTexture *            gsk_texture_node_get_texture            (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_linear_gradient_node_new                (const graphene_rect_t    *bounds,
                                                                     const graphene_point_t   *start,
                                                                     const graphene_point_t   *end,
                                                                     const GskColorStop       *color_stops,
                                                                     gsize                     n_color_stops);
GDK_AVAILABLE_IN_ALL
const graphene_point_t * gsk_linear_gradient_node_peek_start        (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const graphene_point_t * gsk_linear_gradient_node_peek_end          (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
gsize                    gsk_linear_gradient_node_get_n_color_stops (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const GskColorStop *     gsk_linear_gradient_node_peek_color_stops  (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_repeating_linear_gradient_node_new      (const graphene_rect_t    *bounds,
                                                                     const graphene_point_t   *start,
                                                                     const graphene_point_t   *end,
                                                                     const GskColorStop       *color_stops,
                                                                     gsize                     n_color_stops);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_border_node_new                     (const GskRoundedRect     *outline,
                                                                 const float               border_width[4],
                                                                 const GdkRGBA             border_color[4]);
GDK_AVAILABLE_IN_ALL
const GskRoundedRect *  gsk_border_node_peek_outline            (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const float *           gsk_border_node_peek_widths             (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const GdkRGBA *         gsk_border_node_peek_colors             (GskRenderNode            *node);


GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_inset_shadow_node_new               (const GskRoundedRect     *outline,
                                                                 const GdkRGBA            *color,
                                                                 float                     dx,
                                                                 float                     dy,
                                                                 float                     spread,
                                                                 float                     blur_radius);
GDK_AVAILABLE_IN_ALL
const GskRoundedRect *  gsk_inset_shadow_node_peek_outline      (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const GdkRGBA *         gsk_inset_shadow_node_peek_color        (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_inset_shadow_node_get_dx            (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_inset_shadow_node_get_dy            (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_inset_shadow_node_get_spread        (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_inset_shadow_node_get_blur_radius   (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_outset_shadow_node_new              (const GskRoundedRect     *outline,
                                                                 const GdkRGBA            *color,
                                                                 float                     dx,
                                                                 float                     dy,
                                                                 float                     spread,
                                                                 float                     blur_radius);
GDK_AVAILABLE_IN_ALL
const GskRoundedRect *  gsk_outset_shadow_node_peek_outline     (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const GdkRGBA *         gsk_outset_shadow_node_peek_color       (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_outset_shadow_node_get_dx           (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_outset_shadow_node_get_dy           (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_outset_shadow_node_get_spread       (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_outset_shadow_node_get_blur_radius  (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_cairo_node_new                      (const graphene_rect_t    *bounds);
GDK_AVAILABLE_IN_ALL
cairo_t *               gsk_cairo_node_get_draw_context         (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const cairo_surface_t * gsk_cairo_node_peek_surface             (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_container_node_new                  (GskRenderNode           **children,
                                                                 guint                     n_children);
GDK_AVAILABLE_IN_ALL
guint                   gsk_container_node_get_n_children       (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_container_node_get_child            (GskRenderNode            *node,
                                                                 guint                     idx);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_transform_node_new                  (GskRenderNode            *child,
                                                                 GskTransform             *transform);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_transform_node_get_child            (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
GskTransform *          gsk_transform_node_get_transform        (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_opacity_node_new                    (GskRenderNode            *child,
                                                                 double                    opacity);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_opacity_node_get_child              (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
double                  gsk_opacity_node_get_opacity            (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_color_matrix_node_new               (GskRenderNode            *child,
                                                                 const graphene_matrix_t  *color_matrix,
                                                                 const graphene_vec4_t    *color_offset);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_color_matrix_node_get_child         (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const graphene_matrix_t *
                        gsk_color_matrix_node_peek_color_matrix (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const graphene_vec4_t * gsk_color_matrix_node_peek_color_offset (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_repeat_node_new                     (const graphene_rect_t    *bounds,
                                                                 GskRenderNode            *child,
                                                                 const graphene_rect_t    *child_bounds);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_repeat_node_get_child               (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const graphene_rect_t * gsk_repeat_node_peek_child_bounds       (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_clip_node_new                       (GskRenderNode            *child,
                                                                 const graphene_rect_t    *clip);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_clip_node_get_child                 (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const graphene_rect_t * gsk_clip_node_peek_clip                 (GskRenderNode            *node);


GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_rounded_clip_node_new               (GskRenderNode            *child,
                                                                 const GskRoundedRect     *clip);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_rounded_clip_node_get_child         (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const GskRoundedRect *  gsk_rounded_clip_node_peek_clip         (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_shadow_node_new                     (GskRenderNode            *child,
                                                                 const GskShadow          *shadows,
                                                                 gsize                     n_shadows);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_shadow_node_get_child               (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const GskShadow *       gsk_shadow_node_peek_shadow             (GskRenderNode            *node,
                                                                 gsize                     i);
GDK_AVAILABLE_IN_ALL
gsize                   gsk_shadow_node_get_n_shadows           (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_blend_node_new                      (GskRenderNode            *bottom,
                                                                 GskRenderNode            *top,
                                                                 GskBlendMode              blend_mode);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_blend_node_get_bottom_child         (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_blend_node_get_top_child            (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
GskBlendMode            gsk_blend_node_get_blend_mode           (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_cross_fade_node_new                 (GskRenderNode            *start,
                                                                 GskRenderNode            *end,
                                                                 double                    progress);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_cross_fade_node_get_start_child     (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_cross_fade_node_get_end_child       (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
double                  gsk_cross_fade_node_get_progress        (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_text_node_new                       (PangoFont                *font,
                                                                 PangoGlyphString         *glyphs,
                                                                 const GdkRGBA            *color,
                                                                 float                     x,
                                                                 float                     y);
GDK_AVAILABLE_IN_ALL
const PangoFont *       gsk_text_node_peek_font                 (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
guint                   gsk_text_node_get_num_glyphs            (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const PangoGlyphInfo   *gsk_text_node_peek_glyphs               (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
const GdkRGBA *         gsk_text_node_peek_color                (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_text_node_get_x                     (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
float                   gsk_text_node_get_y                     (GskRenderNode            *node);

GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_blur_node_new                       (GskRenderNode            *child,
                                                                 double                    radius);
GDK_AVAILABLE_IN_ALL
GskRenderNode *         gsk_blur_node_get_child                 (GskRenderNode            *node);
GDK_AVAILABLE_IN_ALL
double                  gsk_blur_node_get_radius                (GskRenderNode            *node);

G_END_DECLS

#endif /* __GSK_RENDER_NODE_H__ */
