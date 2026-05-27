/*
 * SPDX-FileCopyrightText: Copyright (c) 2021-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: Apache-2.0
 *
 * Licensed under the Apache License, Version 2.0 (the "License");
 * you may not use this file except in compliance with the License.
 * You may obtain a copy of the License at
 *
 * http://www.apache.org/licenses/LICENSE-2.0
 *
 * Unless required by applicable law or agreed to in writing, software
 * distributed under the License is distributed on an "AS IS" BASIS,
 * WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
 * See the License for the specific language governing permissions and
 * limitations under the License.
 */

#include <gst/gst.h>
#include "overlay_internal.h"
#include "gstnvvstmeta.h"
#include "network_utils.h"
#include "nvhwdetection.h"
#include "cudaLoader.h"
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
#include "utils.h"
#endif
#include "nvbufsurface.h"
#include "nvbufwrapper.h"
#include "vst_common.h"
#include <algorithm>
#include <cmath>
#include <opencv2/opencv.hpp>
#include <fstream>
#include <sstream>
#include "halo_safety.h"

#define checkRuntime(call)  check_runtime(call, #call, __LINE__, __FILE__)

using namespace std::chrono_literals;
constexpr int MAX_DISPLAY_LEN = 64;
constexpr const char* MATCH = "MATCH";
constexpr const char* UNMATCH = "UNMATCH";
constexpr const char* INCREMENT = "INCREMENT";
constexpr int MIN_TOLERANCE = 2000;
constexpr double ZERO_FLOAT = 0.0;
constexpr auto METADATA_WAIT_TIME = 1000ms;
constexpr int NUM_DIGITS_MILLI_SECOND = 13;
constexpr int NUM_DIGITS_NANO_SECOND = 19;
constexpr int ANALYTICS_SERVER_QUERY_INTERVAL_MSEC = 500;
constexpr int ANALYTICS_READ_INTERVAL_USEC = 1000000;
constexpr int ROI_STATS_INTERVAL_MSEC = 1000;
constexpr int TRIPWIRE_STATS_INTERVAL_MSEC1 = 900000;
constexpr int TRIPWIRE_STATS_INTERVAL_MSEC2 = 1800000;
constexpr int TRIPWIRE_STATS_INTERVAL_MSEC3 = 2700000;
constexpr int TRIPWIRE_STATS_INTERVAL_MSEC4 = 3600000;
constexpr int BUCKET_SIZE_IN_SEC_FOR_TRIPWIRE = 900;
constexpr int BUCKET_SIZE_IN_SEC_FOR_ROI = 1;
constexpr const char* ANALYTICS_API_GET_TRIPWIRE = "/api/v2/config/tripwire?sensorId=";
constexpr const char* ANALYTICS_API_GET_ROI = "/api/v2/config/roi?sensorId=";
constexpr const char* ANALYTICS_API_GET_TRIPWIRE_STATS = "/api/v2/metrics/tripwire/histogram?sensorId=";
constexpr const char* ANALYTICS_API_GET_ROI_STATS = "/api/v2/metrics/occupancy/roi/histogram?sensorId=";
constexpr int ARROW_SIZE_SCALE_PARAMETER = 10;
constexpr int DEFAULT_FONT_SIZE = 12;
constexpr int DEFAULT_FONT_SIZE_COORDINATES = 5;
constexpr int MAX_CLASSES = 15;
constexpr float DEFAULT_ELLIPSE_SCALE_FACTOR = 1.5f;
constexpr float DEFAULT_ELLIPSE_HEIGHT_FACTOR = 0.5f;
constexpr double MAX_PROXIMITY_AREA_FACTOR = 10.0;

Point interpolateCoordinate(int x, int y, int oldWidth, int oldHeight, int newWidth, int newHeight)
{
    /* Avoid Divide by zero error/crash */
    oldHeight = oldHeight == 0 ? HEIGHT_1080p : oldHeight;
    oldWidth  = oldWidth  == 0 ? WIDTH_1080p  : oldWidth;

    double scaleX = static_cast<double>(newWidth) / oldWidth;
    double scaleY = static_cast<double>(newHeight) / oldHeight;

    // Adjust coordinates based on the scaling factors
    int newX = std::ceil(x * scaleX);
    int newY = std::ceil(y * scaleY);

    return {newX, newY};
}

static int interpolateFontSize(int oldWidth, int newWidth)
{
    if(oldWidth <= 0 || newWidth <=0)
    {
        return DEFAULT_FONT_SIZE;
    }
    return (DEFAULT_FONT_SIZE * newWidth) / oldWidth;
}

// Function to convert box3d to corners3d
std::vector<Point3D> box3d_to_corners3d(map<string, float, std::less<>> bbox3d)
{
    std::vector<Point3D> corners;

    // Create corner offsets in normalized coordinates (relative to the center)
    std::vector<cv::Point3f> corners_norm =
    {
        cv::Point3f(0, 0, 0),
        cv::Point3f(0, 0, 1),
        cv::Point3f(0, 1, 1),
        cv::Point3f(0, 1, 0),
        cv::Point3f(1, 0, 0),
        cv::Point3f(1, 0, 1),
        cv::Point3f(1, 1, 1),
        cv::Point3f(1, 1, 0)
    };

    // Reorder corners
    std::vector<cv::Point3f> corners_norm_reordered =
    {
        corners_norm[0], corners_norm[1], corners_norm[3], corners_norm[2],
        corners_norm[4], corners_norm[5], corners_norm[7], corners_norm[6]
    };

    // Shift to relative origin [0.5, 0.5, 0.5]
    for (auto& corner : corners_norm_reordered)
    {
        corner.x -= 0.5f;
        corner.y -= 0.5f;
        corner.z -= 0.5f;
    }

    // Extract box parameters
    float x = bbox3d["x"];
    float y = bbox3d["y"];
    float z = bbox3d["z"];
    float width = bbox3d["width"];
    float length = bbox3d["length"];
    float height = bbox3d["height"];
    float yaw = bbox3d["yaw"];

    // Scale corners by the box dimensions (W, L, H)
    std::vector<cv::Point3f> scaled_corners;
    for (const auto& corner : corners_norm_reordered)
    {
        scaled_corners.push_back(cv::Point3f(
            corner.x * width,
            corner.y * length,
            corner.z * height
        ));
    }

    // Compute rotation matrix based on yaw (around the Z-axis)
    float rot_cos = cos(yaw);
    float rot_sin = sin(yaw);
    cv::Mat rot_mat = cv::Mat::eye(3, 3, CV_32F);
    rot_mat.at<float>(0, 0) = rot_cos;
    rot_mat.at<float>(0, 1) = -rot_sin;
    rot_mat.at<float>(1, 0) = rot_sin;
    rot_mat.at<float>(1, 1) = rot_cos;

    // Rotate corners and translate by the box center coordinates (X, Y, Z)
    for (const auto& corner : scaled_corners)
    {
        cv::Mat corner_mat = (cv::Mat_<float>(3, 1) << corner.x, corner.y, corner.z);
        cv::Mat rotated_corner = rot_mat * corner_mat;

        Point3D transformed_corner;
        transformed_corner.x = rotated_corner.at<float>(0, 0) + x;
        transformed_corner.y = rotated_corner.at<float>(1, 0) + y;
        transformed_corner.z = rotated_corner.at<float>(2, 0) + z;

        corners.push_back(transformed_corner);
    }

    return corners;
}

vector<Point2D> world_to_image_projection(const vector<Point3D>& corners3d,
                                        const NvLLOverlayInternal::CalibrationData& calibration_data,
                                        int src_height,
                                        bool enableGodsEyeView)
{
    vector<Point2D> image_points;

    if (enableGodsEyeView)
    {
        // Get the number of 3D points
        size_t num_points = corners3d.size();
        if (num_points == 0)
        {
            LOG(warning) << "No 3D points provided for projection" << endl;
            return image_points;
        }

        // Check if scaleFactor is valid (non-zero)
        float scaleFactor = calibration_data.scaleFactor;
        if (std::abs(scaleFactor) < 1e-6)
        {
            LOG(warning) << "Scale factor is too close to zero, using default value 1.0" << endl;
            scaleFactor = 1.0f;
        }

        // Apply the transformation formula for each 3D point
        for (const auto& point : corners3d)
        {
            // Apply translation and scaling similar to the Python code
            // Python: int((location[0] + translation_to_global_coordinates["x"]) * scale_factor)
            float x = (point.x + calibration_data.translationToGlobalCoordinates.x) * scaleFactor;

            // Python: int(map_height - 1. - ((location[1] + translation_to_global_coordinates["y"]) * scale_factor))
            // Note: We're using src_height for map_height
            float y = src_height - 1.0f - ((point.y + calibration_data.translationToGlobalCoordinates.y) * scaleFactor);

            // Add the transformed point to the result
            image_points.push_back(Point2D(x, y));
        }
    }
    else
    {
        // Check if we have valid matrices
        if (calibration_data.proj_w2p_matrix.size() != 16)
        {
            LOG(error) << "Invalid projection matrix size" << endl;
            return image_points;
        }

        // Get the number of 3D points
        size_t num_points = corners3d.size();
        if (num_points == 0)
        {
            return image_points;
        }

        // Create a copy of the projection matrix and reshape to 4x4
        cv::Mat proj_matrix(4, 4, CV_32F);
        for (int i = 0; i < 4; i++)
        {
            for (int j = 0; j < 4; j++)
            {
                proj_matrix.at<float>(i, j) = calibration_data.proj_w2p_matrix[i * 4 + j];
            }
        }

        // 1. Create homogeneous coordinates
        cv::Mat pts_4d(num_points, 4, CV_32F);
        for (size_t i = 0; i < num_points; i++)
        {
            pts_4d.at<float>(i, 0) = corners3d[i].x;
            pts_4d.at<float>(i, 1) = corners3d[i].y;
            pts_4d.at<float>(i, 2) = corners3d[i].z;
            pts_4d.at<float>(i, 3) = 1.0f;
        }

        // 2. Project points using projection matrix
        cv::Mat pts_2d = pts_4d * proj_matrix.t();
        if (pts_2d.empty())
        {
            LOG(error) << "Matrix multiplication failed" << endl;
            return image_points;
        }

        // 3. Normalize points
        for (size_t i = 0; i < num_points; i++)
        {
            float z = std::max(1e-5f, std::min(1e5f, std::abs(pts_2d.at<float>(i, 2))));
            if (z < 1e-5f) continue;  // Skip division by zero
            float x = pts_2d.at<float>(i, 0) / z;
            float y = pts_2d.at<float>(i, 1) / z;
            image_points.push_back(Point2D(x, y));
        }
    }

    return image_points;
}

// Function to calculate bounding circle for a cuboid
Circle calculate_bounding_circle(const vector<Point2D>& corners2d, double proximityAreaFactor)
{
    // Calculate centroid
    float centerX = 0, centerY = 0;
    for (const auto& corner : corners2d)
    {
        centerX += corner.x;
        centerY += corner.y;
    }
    centerX /= corners2d.size();
    centerY /= corners2d.size();

    // Calculate radius as maximum distance from centroid to any corner
    float maxRadius = 0;
    for (const auto& corner : corners2d)
    {
        float dx = corner.x - centerX;
        float dy = corner.y - centerY;
        float distance = sqrt(dx*dx + dy*dy);
        maxRadius = std::max(maxRadius, distance);
    }

    // Add some padding to ensure circle fully encompasses the cuboid
    maxRadius *= proximityAreaFactor;

    return {centerX, centerY, maxRadius};
}

// Function to calculate bounding circle using only the bottom face of a 3D bounding box
Circle calculate_bottom_face_bounding_circle(const vector<Point2D>& corners2d, double proximityAreaFactor)
{
    // The bottom face corners are at indices 0, 2, 6, 4 (based on the corner ordering in box3d_to_corners3d)
    // Corner order from box3d_to_corners3d:
    // corners_norm = 0: (0,0,0), 1: (0,0,1), 2: (0,1,0), 3: (0,1,1),
    //                4: (1,0,0), 5: (1,0,1), 6: (1,1,0), 7: (1,1,1)
    // Bottom face (z=0): 0, 2, 6, 4

    if (corners2d.size() != 8)
    {
        // If we don't have exactly 8 corners, fall back to using all corners
        return calculate_bounding_circle(corners2d, proximityAreaFactor);
    }

    // Extract bottom face corners
    vector<Point2D> bottomFaceCorners =
    {
        corners2d[0], // (0,0,0)
        corners2d[2], // (0,1,0)
        corners2d[6], // (1,1,0)
        corners2d[4]  // (1,0,0)
    };

    // Calculate centroid of bottom face
    float centerX = 0, centerY = 0;
    for (const auto& corner : bottomFaceCorners)
    {
        centerX += corner.x;
        centerY += corner.y;
    }
    centerX /= bottomFaceCorners.size();
    centerY /= bottomFaceCorners.size();

    // Calculate radius as maximum distance from centroid to any bottom face corner
    float maxRadius = 0;
    for (const auto& corner : bottomFaceCorners)
    {
        float dx = corner.x - centerX;
        float dy = corner.y - centerY;
        float distance = sqrt(dx*dx + dy*dy);
        maxRadius = std::max(maxRadius, distance);
    }

    // Add some padding to ensure circle fully encompasses the bottom face
    maxRadius *= proximityAreaFactor;

    return {centerX, centerY, maxRadius};
}

// Function to extract the bottom face corners from a 3D bounding box
vector<Point2D> extract_bottom_face_corners(const vector<Point2D>& corners2d)
{
    // If we don't have exactly 8 corners, return the original corners
    if (corners2d.size() != 8) {
        return corners2d;
    }

    // Extract bottom face corners (z=0): indices 0, 2, 6, 4
    return {
        corners2d[0], // (0,0,0)
        corners2d[2], // (0,1,0)
        corners2d[6], // (1,1,0)
        corners2d[4]  // (1,0,0)
    };
}

// Function to calculate the center point of the bottom face
Point2D calculate_bottom_face_center(const vector<Point2D>& corners2d)
{
    // If we don't have exactly 8 corners, calculate center of all points
    if (corners2d.size() != 8) {
        float centerX = 0, centerY = 0;
        for (const auto& corner : corners2d) {
            centerX += corner.x;
            centerY += corner.y;
        }
        return {centerX / corners2d.size(), centerY / corners2d.size()};
    }

    // Extract bottom face corners
    vector<Point2D> bottomFaceCorners = extract_bottom_face_corners(corners2d);

    // Calculate centroid of bottom face
    float centerX = 0, centerY = 0;
    for (const auto& corner : bottomFaceCorners) {
        centerX += corner.x;
        centerY += corner.y;
    }

    return {centerX / bottomFaceCorners.size(), centerY / bottomFaceCorners.size()};
}

// Function to calculate 3D sphere from 3D corners and project to 2D ellipse
int calculate_3d_sphere_to_2d_ellipse(const map<string, float, std::less<>>& coordinates,
                                      const NvLLOverlayInternal::CalibrationData& calibration_data,
                                      float proximityAreaFactor,
                                      float& centerX, float& centerY, float& width, float& height, float& yaw,
                                      int img_width, int img_height)
{
    // Step 1: Extract 3D bounding box parameters
    float x = coordinates.at("x");
    float y = coordinates.at("y");
    float z = coordinates.at("z");
    float bbox_width = coordinates.at("width");
    float bbox_length = coordinates.at("length");
    float bbox_height = coordinates.at("height");
    float bbox_yaw = coordinates.at("yaw");

    // Step 2: Calculate the center of the bottom face of the 3D bounding box
    // This ensures the ellipse is positioned on the ground
    Point3D bottom_center = {x, y, z - bbox_height/2.0f};

    // Step 3: Calculate the radius of the 2D circle that encompasses the bottom face
    // Use the half-diagonal of the bottom face (width and length only, no height)
    float radius2d = sqrt(bbox_width*bbox_width + bbox_length*bbox_length) / 2.0f;

    // Step 4: Create a set of points on the bottom face perimeter
    vector<Point3D> bottom_face_points;
    const int num_points = 16; // Number of points to sample on the perimeter

    for (int i = 0; i < num_points; i++)
    {
        float angle = 2.0f * M_PI * i / num_points;

        // Calculate points on a circle in the local coordinate system
        float local_x = radius2d * cos(angle);
        float local_y = radius2d * sin(angle);

        // Adjust aspect ratio to match the bounding box dimensions
        // This creates an ellipse that matches the bottom face shape
        if (bbox_width > bbox_length)
        {
            local_y *= (bbox_length / bbox_width);
        }
        else
        {
            local_x *= (bbox_width / bbox_length);
        }

        // Rotate by the bounding box yaw and translate to world coordinates
        float world_x = bottom_center.x + local_x * cos(bbox_yaw) - local_y * sin(bbox_yaw);
        float world_y = bottom_center.y + local_x * sin(bbox_yaw) + local_y * cos(bbox_yaw);
        float world_z = bottom_center.z; // Keep on the ground plane

        bottom_face_points.push_back({world_x, world_y, world_z});
    }

    // Step 5: Project these 3D bottom face points to 2D using the camera projection matrices
    vector<Point2D> ellipse_points = world_to_image_projection(bottom_face_points, calibration_data, img_height, false);

    if (ellipse_points.size() < 3)
    {
        // Not enough points for an ellipse, use default values
        centerX = 0;
        centerY = 0;
        width = 100;
        height = 50;
        yaw = 0;
        return 1;
    }

    // Check if any points are within the frame
    bool any_point_in_frame = false;
    for (const auto& point : ellipse_points)
    {
        if (point.x >= 0 && point.x < img_width && point.y >= 0 && point.y < img_height)
        {
            any_point_in_frame = true;
            break;
        }
    }

    // If no points are in frame, adjust the points to be visible
    if (!any_point_in_frame)
    {
        // Calculate average position to determine direction of adjustment
        float avg_x = 0, avg_y = 0;
        for (const auto& point : ellipse_points)
        {
            avg_x += point.x;
            avg_y += point.y;
        }
        avg_x /= ellipse_points.size();
        avg_y /= ellipse_points.size();

        // Determine adjustment direction
        float adjust_x = 0, adjust_y = 0;
        if (avg_x < 0) adjust_x = -avg_x + img_width * 0.1;
        else if (avg_x >= img_width) adjust_x = img_width * 0.9 - avg_x;

        if (avg_y < 0) adjust_y = -avg_y + img_height * 0.1;
        else if (avg_y >= img_height) adjust_y = img_height * 0.9 - avg_y;

        // Apply adjustment to all points
        for (auto& point : ellipse_points)
        {
            point.x += adjust_x;
            point.y += adjust_y;
        }
    }
    else
    {
        return 0;
    }

    // Step 6: Fit an ellipse to the projected points
    // Calculate the centroid of the projected points
    centerX = 0;
    centerY = 0;
    for (const auto& point : ellipse_points)
    {
        centerX += point.x;
        centerY += point.y;
    }
    centerX /= ellipse_points.size();
    centerY /= ellipse_points.size();

    // Find the principal axes of the ellipse using PCA-like approach
    float sum_xx = 0, sum_xy = 0, sum_yy = 0;

    for (const auto& point : ellipse_points)
    {
        float dx = point.x - centerX;
        float dy = point.y - centerY;

        sum_xx += dx * dx;
        sum_xy += dx * dy;
        sum_yy += dy * dy;
    }

    // Calculate the orientation (yaw) of the ellipse
    if (sum_xy == 0 && sum_xx == sum_yy)
    {
        // Circle case - no clear orientation
        yaw = 0;
    }
    else
    {
        // Calculate the angle of the major axis
        yaw = 0.5 * atan2(2 * sum_xy, sum_xx - sum_yy);
    }

    // Calculate the semi-major and semi-minor axes
    float tmp1 = 0.5 * (sum_xx + sum_yy);
    float tmp2 = 0.5 * sqrt((sum_xx - sum_yy) * (sum_xx - sum_yy) + 4 * sum_xy * sum_xy);
    float lambda1 = tmp1 + tmp2; // Larger eigenvalue
    float lambda2 = tmp1 - tmp2; // Smaller eigenvalue

    // Set the ellipse dimensions
    width = 2.0f * sqrt(lambda1) * proximityAreaFactor; // Scale factor for better visual fit
    height = 2.0f * sqrt(lambda2) * proximityAreaFactor;

    // Ensure width is the longer axis
    if (height > width)
    {
        std::swap(width, height);
        yaw += M_PI / 2.0f;
    }

    // Normalize yaw to [-π, π]
    while (yaw > M_PI) yaw -= 2.0f * M_PI;
    while (yaw < -M_PI) yaw += 2.0f * M_PI;

    return 1;
}

// Generic helper function to check if an object type is in a comma-separated class list
bool is_in_class_list(const string& obj_type, const string& classList)
{
    std::istringstream ss(classList);
    std::string token;

    // Check if obj_type matches any class in the comma-separated list
    while (std::getline(ss, token, ','))
    {
        // Trim whitespace from the token
        token.erase(0, token.find_first_not_of(" \t"));
        token.erase(token.find_last_not_of(" \t") + 1);

        if (obj_type == token)
        {
            return true;
        }
    }

    return false;
}

// Generic helper function to get the matching class from a comma-separated list for color retrieval
string get_matching_class(const string& obj_type, const string& classList)
{
    std::istringstream ss(classList);
    std::string token;

    // Check if obj_type matches any class in the comma-separated list
    while (std::getline(ss, token, ','))
    {
        // Trim whitespace from the token
        token.erase(0, token.find_first_not_of(" \t"));
        token.erase(token.find_last_not_of(" \t") + 1);

        if (obj_type == token)
        {
            return token; // Return the matching class
        }
    }

    // If no match found, return the first class in the list (default behavior)
    ss.clear();
    ss.str(classList);
    if (std::getline(ss, token, ','))
    {
        token.erase(0, token.find_first_not_of(" \t"));
        token.erase(token.find_last_not_of(" \t") + 1);
        return token;
    }

    // If list is empty, return the original string
    return classList;
}

void draw_line_cuosd(OSD_LineParams* line_params,
                BBoxDrawingData* box_params, OsdContext_t context, GstBuffer *buffer,
                const OSD_ColorParams output_color = {0, 0, 0, 0},
                int thickness = 0)  // Default to black with 0 alpha
{
    /* Set Line width */
    if (thickness == 0)
    {
        line_params->thickness = box_params->m_overlay.m_bboxThickness;
    }
    else
    {
        line_params->thickness = thickness;
    }
    line_params->is_interpolation = true;

    OSD_ColorParams color = OSD_COLOR_RED;

    // Check if a valid color was provided (non-zero alpha indicates valid color)
    if (output_color.alpha != 0) color = output_color;
    else if (box_params->m_overlay.m_bboxColor == "green") color = OSD_COLOR_GREEN;
    else if (box_params->m_overlay.m_bboxColor == "blue") color = OSD_COLOR_BLUE;
    else if (box_params->m_overlay.m_bboxColor == "black") color = OSD_COLOR_BLACK;
    else if (box_params->m_overlay.m_bboxColor == "white") color = OSD_COLOR_WHITE;
    else if (box_params->m_overlay.m_bboxColor == "yellow") color = OSD_COLOR_YELLOW;
    else color = OSD_COLOR_RED;
    line_params->color = color;
    line_params->color.alpha = color.alpha != 0 ? color.alpha : box_params->m_overlay.m_bboxOpacity;
    auto line_params_copy = std::make_unique<OSD_LineParams>(*line_params);

    if (buffer)
    {
        GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_LINE, line_params_copy.release());
    }
    else
    {
        OsdMeta meta;
        meta.meta_type = OSD_LINE;
        meta.params = static_cast<void*>(line_params_copy.release());
        GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
    }
}

// Function to count visible corners
bool count_visible_corners(const vector<Point2D>& corners, int img_width, int img_height)
{
    if (corners.empty())
    {
        return false;
    }

    int visible_count = 0;
    for (const auto& corner : corners)
    {
        if (corner.x >= 0 && corner.x < img_width && corner.y >= 0 && corner.y < img_height)
        {
            visible_count++;
        }
    }

    // Return true if at least half of the corners are visible
    return (visible_count >= 4);
}

// Function to clamp a point to image boundaries
void clamp_point(float& x, float& y, int img_width, int img_height)
{
    x = std::max(0.0f, std::min(static_cast<float>(img_width), x));
    y = std::max(0.0f, std::min(static_cast<float>(img_height), y));
}

// Function to generate a consistent color based on class label
bool get_color_from_label(const char* label, std::map<string, vector<int>, std::less<>> colorCode, OSD_ColorParams* color)
{
    // Set alpha to 255 (fully opaque)
    color->alpha = 255;
    // If label is null or empty, return false
    if (!label || label[0] == '\0')
    {
        return false;
    }

    // Only try to get color from provided colorCode map
    if (colorCode.find(label) != colorCode.end())
    {
        auto color_vector = colorCode[label];
        *color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
        return true;
    }

    // If no color found in map, return false to skip drawing
    return false;
}

void NvLLOverlayInternal::draw_bbox_id_cuosd(const Point& left_top, const Point& right_bottom,
    const string& object_id,
    BBoxDrawingData* box_params,
    OsdContext_t context,
    GstBuffer* buffer)
{
    if (!box_params->m_overlay.m_enableBboxId || object_id.empty())
    {
        return;
    }

    const int left = left_top.x;
    const int top = left_top.y;
    const int bottom = right_bottom.y;
    const int right = right_bottom.x;

    OSD_TextParams* text_params = static_cast<OSD_TextParams*>(malloc(sizeof(OSD_TextParams)));
    if (!text_params)
    {
        LOG(error) << "Failed to allocate OSD_TextParams for bbox id" << endl;
        return;
    }

    const string text = object_id;
    char* cstr = static_cast<char*>(calloc(text.size() + 1, sizeof(char)));
    if (cstr)
    {
        strncpy(cstr, text.c_str(), text.size());
        cstr[text.size()] = '\0';
        text_params->text = cstr;
    }
    else
    {
        LOG(error) << "Failed to allocate memory for overlay object id text" << endl;
        free(text_params);
        return;
    }

    if (box_params->m_overlay.m_bboxIdPosition == MIDDLE)
    {
        const int cx = (left_top.x + right_bottom.x) / 2;
        const int cy = (left_top.y + right_bottom.y) / 2;
        const int font_size = box_params->m_overlay.m_bboxThickness * 2;
        const int text_width = static_cast<int>(text.length() * font_size * 0.8);
        const int text_height = font_size;
        text_params->pos_x = cx - text_width;
        text_params->pos_y = cy - text_height;
    }
    else if (box_params->m_overlay.m_bboxIdPosition == TOP_LEFT)
    {
        text_params->pos_x = left;
        text_params->pos_y = top;
    }
    else if (box_params->m_overlay.m_bboxIdPosition == TOP_RIGHT)
    {
        text_params->pos_x = right;
        text_params->pos_y = top;
    }
    else if (box_params->m_overlay.m_bboxIdPosition == BOTTOM_RIGHT)
    {
        text_params->pos_x = right;
        text_params->pos_y = bottom;
    }
    else
    {
        // BOTTOM_LEFT
        text_params->pos_x = left;
        text_params->pos_y = bottom;
    }

    text_params->font_size = box_params->m_overlay.m_bboxThickness * 2;
    text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

    OSD_ColorParams bg_color = OSD_COLOR_RED;
    OSD_ColorParams text_color = OSD_COLOR_WHITE;
    if (box_params->m_overlay.m_bboxIdBgColor == "green") bg_color = OSD_COLOR_GREEN;
    else if (box_params->m_overlay.m_bboxIdBgColor == "blue") bg_color = OSD_COLOR_BLUE;
    else if (box_params->m_overlay.m_bboxIdBgColor == "black") bg_color = OSD_COLOR_BLACK;
    else if (box_params->m_overlay.m_bboxIdBgColor == "white") bg_color = OSD_COLOR_WHITE;
    else if (box_params->m_overlay.m_bboxIdBgColor == "yellow") bg_color = OSD_COLOR_YELLOW;

    if (box_params->m_overlay.m_bboxIdColor == "green") text_color = OSD_COLOR_GREEN;
    else if (box_params->m_overlay.m_bboxIdColor == "blue") text_color = OSD_COLOR_BLUE;
    else if (box_params->m_overlay.m_bboxIdColor == "black") text_color = OSD_COLOR_BLACK;
    else if (box_params->m_overlay.m_bboxIdColor == "red") text_color = OSD_COLOR_RED;
    else if (box_params->m_overlay.m_bboxIdColor == "yellow") text_color = OSD_COLOR_YELLOW;

    text_params->border_color = text_color;
    text_params->bg_color = bg_color;

    if (buffer)
    {
        GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
    }
    else
    {
        OsdMeta meta;
        meta.meta_type = OSD_TEXT;
        meta.params = static_cast<void*>(text_params);
        GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
    }
}

void NvLLOverlayInternal::draw_bbox_id_for_3d_projected_corners(const vector<Point2D>& corners2d,
    const string& object_id,
    BBoxDrawingData* box_params,
    OsdContext_t context,
    GstBuffer* buffer)
{
    if (!box_params->m_overlay.m_enableBboxId || object_id.empty() || corners2d.empty())
    {
        return;
    }

    // Axis-aligned bounds of the projected 3D box in source (sensor) pixel space
    float min_x = corners2d[0].x;
    float max_x = corners2d[0].x;
    float min_y = corners2d[0].y;
    float max_y = corners2d[0].y;
    for (size_t i = 1; i < corners2d.size(); ++i)
    {
        min_x = std::min(min_x, corners2d[i].x);
        max_x = std::max(max_x, corners2d[i].x);
        min_y = std::min(min_y, corners2d[i].y);
        max_y = std::max(max_y, corners2d[i].y);
    }

    int src_left = static_cast<int>(std::floor(min_x));
    int src_top = static_cast<int>(std::floor(min_y));
    int src_right = static_cast<int>(std::ceil(max_x));
    int src_bottom = static_cast<int>(std::ceil(max_y));

    const int max_src_x = std::max(0, m_sourceWidth - 1);
    const int max_src_y = std::max(0, m_sourceHeight - 1);
    src_left = std::clamp(src_left, 0, max_src_x);
    src_right = std::clamp(src_right, 0, max_src_x);
    src_top = std::clamp(src_top, 0, max_src_y);
    src_bottom = std::clamp(src_bottom, 0, max_src_y);
    if (src_right < src_left)
    {
        std::swap(src_left, src_right);
    }
    if (src_bottom < src_top)
    {
        std::swap(src_top, src_bottom);
    }

    const Point left_top = interpolateCoordinate(src_left, src_top, m_sourceWidth, m_sourceHeight, m_width, m_height);
    const Point right_bottom = interpolateCoordinate(src_right, src_bottom, m_sourceWidth, m_sourceHeight, m_width, m_height);

    draw_bbox_id_cuosd(left_top, right_bottom, object_id, box_params, context, buffer);
}

// Function to draw 3D bounding box
int NvLLOverlayInternal::draw_3d_bbox(const vector<Point2D>& corners2d, const string& obj_type,
                                     BBoxDrawingData* box_params, OsdContext_t context, GstBuffer* buffer,
                                     const string& object_id,
                                     const OSD_ColorParams& override_color,
                                     bool first_pass, double confidence)
{
    // Need exactly 8 corners for a 3D box
    if (corners2d.size() != 8)
    {
        LOG(error) << "Invalid number of corners: " << corners2d.size() << endl;
        return 0;
    }

    // Check if enough corners are visible
    if (!count_visible_corners(corners2d, m_sourceWidth, m_sourceHeight))
    {
        return 0;
    }

    if (first_pass == true)
    {
        return 1;
    }

    /*
    Corner order from box3d_to_corners3d:
    corners_norm =
        0: (0,0,0), 1: (0,0,1), 2: (0,1,0), 3: (0,1,1),
        4: (1,0,0), 5: (1,0,1), 6: (1,1,0), 7: (1,1,1)
    */

    // Define line indices for the 3D bounding box edges to match the corner order
    const int line_indices[][2] =
    {
        // Bottom face (z=0)
        {0, 2}, {2, 6}, {6, 4}, {4, 0},
        // Top face (z=1)
        {1, 3}, {3, 7}, {7, 5}, {5, 1},
        // Vertical edges
        {0, 1}, {2, 3}, {4, 5}, {6, 7}
    };

    // Draw the 3D box lines using CUOSD
    for (int i = 0; i < 12; i++)
    {  // 12 edges in a cuboid
        float x1 = corners2d[line_indices[i][0]].x;
        float y1 = corners2d[line_indices[i][0]].y;
        float x2 = corners2d[line_indices[i][1]].x;
        float y2 = corners2d[line_indices[i][1]].y;

        // Check if any point is outside the image bounds
        bool point1_outside = (x1 < 0 || x1 > m_sourceWidth || y1 < 0 || y1 > m_sourceHeight);
        bool point2_outside = (x2 < 0 || x2 > m_sourceWidth || y2 < 0 || y2 > m_sourceHeight);

        // Skip drawing if both points are outside the image
        if (point1_outside && point2_outside)
        {
            continue;
        }

        // Clamp the points to the image boundaries
        clamp_point(x1, y1, m_sourceWidth, m_sourceHeight);
        clamp_point(x2, y2, m_sourceWidth, m_sourceHeight);

        OSD_LineParams* line_params = (OSD_LineParams*)malloc(sizeof(OSD_LineParams));
        if (line_params)
        {
            Point start = interpolateCoordinate(x1, y1, m_sourceWidth, m_sourceHeight, m_width, m_height);
            Point end = interpolateCoordinate(x2, y2, m_sourceWidth, m_sourceHeight, m_width, m_height);
            line_params->pos_x0 = start.x;
            line_params->pos_y0 = start.y;
            line_params->pos_x1 = end.x;
            line_params->pos_y1 = end.y;

            OSD_ColorParams output_color = {0,0,0,0};
            if (override_color.alpha != 0)
            {
                output_color = override_color;
            }
            else
            {
                if (!get_color_from_label(obj_type.c_str(), box_params->m_overlay.m_colorCode, &output_color))
                {
                    output_color = {0,0,0,0};
                }
            }

            draw_line_cuosd(line_params, box_params, context, buffer, output_color);
            free(line_params);
        }
    }

    if (box_params->m_overlay.m_enableBboxId && !object_id.empty())
    {
        draw_bbox_id_for_3d_projected_corners(corners2d, object_id, box_params, context, buffer);
    }

    if (box_params->m_overlay.m_bboxDebug)
    {
        // Add text label at the bottom of the 3D box
        // Calculate the bottom left position for the text
        float min_x = std::numeric_limits<float>::max();
        float max_y = std::numeric_limits<float>::min();

        // Find the bottom face corners (z=0) to determine text position
        for (int i = 0; i < 4; i++)
        {
            // Bottom face corners are 0, 2, 4, 6
            int idx = i * 2;
            min_x = std::min(min_x, corners2d[idx].x);
            max_y = std::max(max_y, corners2d[idx].y);
        }

        // Position text at bottom left of the box
        float text_x = min_x;
        float text_y = max_y + 1; // Offset below the box

        // Clamp text position to image boundaries
        clamp_point(text_x, text_y, m_sourceWidth, m_sourceHeight);

        // Create text label with object ID, type, and confidence with 2 decimal places
        std::stringstream ss;
        ss << object_id << "," << obj_type << "," << std::fixed << std::setprecision(2) << confidence;
        string label_text = ss.str();

        OSD_TextParams* text_params = (OSD_TextParams*)malloc(sizeof(OSD_TextParams));
        if (text_params != nullptr)
        {
            // Use safe strncpy with explicit bounds checking
            char* cstr = (char*)calloc(label_text.size() + 1, sizeof(char));
            if (cstr != nullptr)
            {
                strncpy(cstr, label_text.c_str(), label_text.size());
                cstr[label_text.size()] = '\0';  // Guarantee null termination
                text_params->text = cstr;
            }
            else
            {
                LOG(error) << "Failed to allocate memory for label text" << endl;
                text_params->text = nullptr;
            }

            Point text_pos = interpolateCoordinate(text_x, text_y, m_sourceWidth, m_sourceHeight, m_width, m_height);
            text_params->pos_x = text_pos.x;
            text_params->pos_y = text_pos.y;
            text_params->font_size = DEFAULT_FONT_SIZE_COORDINATES;
            text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

            // Use same color as the box lines
            text_params->border_color = (OSD_ColorParams){255,255,255,255};
            text_params->bg_color = (OSD_ColorParams){77,77,77,255};

            if (buffer)
            {
                GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
            }
            else
            {
                OsdMeta meta;
                meta.meta_type = OSD_TEXT;
                meta.params = (void *)text_params;
                GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
            }
        }
    }

    return 1;
}

void NvLLOverlayInternal::draw_bbox_cuosd(Json::Value & objects, BBoxDrawingData* box_params,
                vector<string> m_bboxList, vector<string> m_classTypeList,
                OsdContext_t context, GstBuffer *buffer)
{
    bool check_id = true;
    bool check_class_type = true;
    if (find(m_bboxList.begin(), m_bboxList.end(), "all") != m_bboxList.end())
    {
        check_id = false;
    }

    if (find(m_classTypeList.begin(), m_classTypeList.end(), "all") != m_classTypeList.end())
    {
        check_class_type = false;
    }

    if (((check_id && find(m_bboxList.begin(), m_bboxList.end(), "none") != m_bboxList.end()) &&
        (check_class_type && find(m_classTypeList.begin(), m_classTypeList.end(), "none") != m_classTypeList.end())) &&
        !m_enablePose && !m_enableHalos)
    {
        return;
    }

    for (uint32_t i = 0; i < objects.size(); i++)
    {
        map<string, float, std::less<>> coordinates;
        string obj_type;
        double confidence;
        vector<Point2D> corners2d;
        vector<Point3D> corners3d;
        bool isProximityClass = false;
        bool isEntrantClass = false;

        string object_id = vst_common::parseMetadataObject(coordinates, obj_type, confidence, objects, i);

        // Check for pose data and draw pose if present
        std::vector<float> keypoints;
        std::string action_label;
        int label_x, label_y;

        if (m_enablePose && check_pose_data(objects[i], coordinates, keypoints, action_label, label_x, label_y))
        {
            draw_pose_cuosd(keypoints, action_label, box_params, context, buffer, label_x, label_y);
        }

        // Check if halos are enabled and data is available (2D bbox: leftX, topY, rightX, bottomY)
        if (m_enableHalos &&
            coordinates.find("leftX") != coordinates.end() &&
            coordinates.find("topY") != coordinates.end() &&
            coordinates.find("rightX") != coordinates.end() &&
            coordinates.find("bottomY") != coordinates.end())
        {
            Point left_top = interpolateCoordinate(coordinates ["leftX"], coordinates ["topY"], m_sourceWidth, m_sourceHeight, m_width, m_height);
            Point right_bottom = interpolateCoordinate(coordinates ["rightX"], coordinates ["bottomY"], m_sourceWidth, m_sourceHeight, m_width, m_height);

            bool draw_text = false;
            bool active = false;
            string active_text = HALO_SAFETY_COMMAND_ACTIVE_STRING;
            string inactive_text = HALO_SAFETY_COMMAND_INACTIVE_STRING;
            if (m_haloSafetyManager)
            {
                active = m_haloSafetyManager->checkHalosData(obj_type, box_params->m_overlay.m_proximityClass, draw_text,
                                                             object_id);

                if (active)
                {
                    if (!GET_CONFIG().halo_safety_active_text.empty())
                    {
                        active_text = GET_CONFIG().halo_safety_active_text;
                    }
                    // Draw ellipse and SAFETY ACTIVE text
                    draw_ellipse_around_2d_bbox(left_top, right_bottom, box_params, context, buffer);
                    m_haloSafetyManager->drawHaloText(left_top, right_bottom, active_text, context, buffer);
                }
                else if (draw_text)
                {
                    if (!GET_CONFIG().halo_safety_inactive_text.empty())
                    {
                        inactive_text = GET_CONFIG().halo_safety_inactive_text;
                    }
                    // Draw only SAFETY INACTIVE text
                    m_haloSafetyManager->drawHaloText(left_top, right_bottom, inactive_text, context, buffer);
                }
            }
        }

        if (coordinates.find("bbox3d") != coordinates.end() && coordinates["bbox3d"] == 1.0f)
        {
            // Convert bbox3d to 8 corner points in world 3D
            corners3d = box3d_to_corners3d(coordinates);

            // Get calibration data and project to 2D image coordinates
            {
                std::lock_guard<std::mutex> guard(m_calibrationLock);
                if (m_calibrationData.find(m_sensorName) != m_calibrationData.end())
                {
                    corners2d = world_to_image_projection(corners3d, m_calibrationData[m_sensorName], m_sourceHeight, false);
                }
                else if (m_bboxParams.m_overlay.m_enableGodsEyeView &&
                    m_calibrationData.find("map") != m_calibrationData.end())
                {
                    corners2d = world_to_image_projection(corners3d, m_calibrationData["map"], m_sourceHeight, true);
                }
                else
                {
                    LOG(error) << "No calibration data found for sensor:" << m_sensorName.c_str() << endl;
                    return;
                }
            }

            // Store corners for proximity detection regardless of object type
            // m_proximityClass is comma separated list of classes
            isProximityClass = is_in_class_list(obj_type, box_params->m_overlay.m_proximityClass);
            // m_entrantClass is also comma separated list of classes
            isEntrantClass = is_in_class_list(obj_type, box_params->m_overlay.m_entrantClass);

            if (isEntrantClass || isProximityClass)
            {
                // Safety halos for 3D: when proximity class present and entrant class empty, draw halo ellipse from 3D bbox 2D projection
                if (m_enableHalos && m_haloSafetyManager &&
                    !box_params->m_overlay.m_proximityClass.empty() &&
                    box_params->m_overlay.m_entrantClass.empty() &&
                    isProximityClass && !corners2d.empty())
                {
                    float left_x = corners2d[0].x, top_y = corners2d[0].y;
                    float right_x = corners2d[0].x, bottom_y = corners2d[0].y;
                    for (size_t idx = 1; idx < corners2d.size(); idx++)
                    {
                        const Point2D& pt = corners2d[idx];
                        if (pt.x < left_x)   left_x = pt.x;
                        if (pt.x > right_x)  right_x = pt.x;
                        if (pt.y < top_y)   top_y = pt.y;
                        if (pt.y > bottom_y) bottom_y = pt.y;
                    }
                    Point left_top = interpolateCoordinate(static_cast<int>(left_x), static_cast<int>(top_y), m_sourceWidth, m_sourceHeight, m_width, m_height);
                    Point right_bottom = interpolateCoordinate(static_cast<int>(right_x), static_cast<int>(bottom_y), m_sourceWidth, m_sourceHeight, m_width, m_height);

                    bool draw_text = false;
                    bool active = m_haloSafetyManager->checkHalosData(obj_type, box_params->m_overlay.m_proximityClass,
                                                                      draw_text, object_id);
                    string active_text = HALO_SAFETY_COMMAND_ACTIVE_STRING;
                    string inactive_text = HALO_SAFETY_COMMAND_INACTIVE_STRING;
                    if (!GET_CONFIG().halo_safety_active_text.empty())
                        active_text = GET_CONFIG().halo_safety_active_text;
                    if (!GET_CONFIG().halo_safety_inactive_text.empty())
                        inactive_text = GET_CONFIG().halo_safety_inactive_text;

                    if (active)
                    {
                        draw_ellipse_around_2d_bbox(left_top, right_bottom, box_params, context, buffer);
                        m_haloSafetyManager->drawHaloText(left_top, right_bottom, active_text, context, buffer);
                    }
                    else if (draw_text)
                    {
                        m_haloSafetyManager->drawHaloText(left_top, right_bottom, inactive_text, context, buffer);
                    }
                }
            }
        }

        // If bbox is disabled, skip bbox drawing
        if (!m_enableBbox || object_id.empty() || obj_type.empty() ||
            (check_id && find(m_bboxList.begin(), m_bboxList.end(), object_id) == m_bboxList.end()) ||
            (check_class_type && find(m_classTypeList.begin(), m_classTypeList.end(), obj_type) == m_classTypeList.end()))
        {
            continue;
        }

        // Check if we have bbox3d coordinates
        if (coordinates.find("bbox3d") != coordinates.end() && coordinates["bbox3d"] == 1.0f)
        {
            if ((isEntrantClass || isProximityClass) &&
                !box_params->m_overlay.m_proximityClass.empty() &&
                !box_params->m_overlay.m_entrantClass.empty())
            {
                // Add 3D bbox to the activeObjectCorners map only if it is visible in the image
                int ret = draw_3d_bbox(corners2d, obj_type, box_params, context, buffer, object_id, {0,0,0,0}, true, confidence);
                if (ret == 0)
                {
                    continue;
                }
                activeObjectCorners[object_id] = {obj_type, corners2d, confidence};
            }
            else
            {
                // Draw 3D bbox only for objects that are not entrant or proximity class
                draw_3d_bbox(corners2d, obj_type, box_params, context, buffer, object_id, {0,0,0,0}, false, confidence);
            }
            continue; // Skip regular bbox drawing
        }

        /* Assign bounding box coordinates */
        OSD_RectParams* rect_params = (OSD_RectParams*)malloc(sizeof(OSD_RectParams));
        Point left_top = {}, right_bottom = {};
        if (rect_params)
        {
            left_top = interpolateCoordinate(coordinates ["leftX"], coordinates ["topY"], m_sourceWidth, m_sourceHeight, m_width, m_height);
            right_bottom = interpolateCoordinate(coordinates ["rightX"], coordinates ["bottomY"], m_sourceWidth, m_sourceHeight, m_width, m_height);
            rect_params->left    = left_top.x;
            rect_params->top     = left_top.y;
            rect_params->right   = right_bottom.x;
            rect_params->bottom  = right_bottom.y;

            OSD_ColorParams bg_color = {0, 0, 0, 0};
            rect_params->bg_color = bg_color;

            /* Set border width */
            rect_params->thickness = box_params->m_overlay.m_bboxThickness;

            OSD_ColorParams border_color = OSD_COLOR_RED;

            // Use color code based on object type from UI
            if (!get_color_from_label(obj_type.c_str(), box_params->m_overlay.m_colorCode, &border_color))
            {
                // Use color code based on bbox color from UI as object type is not found in color code
                if (box_params->m_overlay.m_bboxColor == "green") border_color = OSD_COLOR_GREEN;
                else if (box_params->m_overlay.m_bboxColor == "blue") border_color = OSD_COLOR_BLUE;
                else if (box_params->m_overlay.m_bboxColor == "black") border_color = OSD_COLOR_BLACK;
                else if (box_params->m_overlay.m_bboxColor == "white") border_color = OSD_COLOR_WHITE;
                else if (box_params->m_overlay.m_bboxColor == "yellow") border_color = OSD_COLOR_YELLOW;
                else border_color = OSD_COLOR_RED;
            }
            rect_params->border_color = border_color;
            rect_params->border_color.alpha = box_params->m_overlay.m_bboxOpacity;

            if (buffer != nullptr)
            {
                GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_RECTANGLE, rect_params);
            }
            else
            {
                OsdMeta meta;
                meta.meta_type = OSD_RECTANGLE;
                meta.params = (void *)rect_params;
                GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
            }
        }

        // Display object id in the given bbox id position (same layout as 3D projected AABB)
        if (box_params->m_overlay.m_enableBboxId)
        {
            draw_bbox_id_cuosd(left_top, right_bottom, object_id, box_params, context, buffer);
        }

        if (box_params->m_overlay.m_bboxDebug)
        {
            int left = left_top.x;
            int top = left_top.y;
            int bottom = right_bottom.y;

            // Display coordinates above each bbox.
            OSD_TextParams* text_params = (OSD_TextParams*)malloc(sizeof(OSD_TextParams));
            if (text_params)
            {
                string text = to_string(left) + "    " + to_string(top);
                // Use safe strncpy with explicit bounds checking
                char* cstr = (char*)calloc(text.size() + 1, sizeof(char));
                if (cstr)
                {
                    strncpy(cstr, text.c_str(), text.size());
                    cstr[text.size()] = '\0';  // Guarantee null termination
                    text_params->text = cstr;
                }
                else
                {
                    LOG(error) << "Failed to allocate memory for overlay text" << endl;
                    text_params->text = nullptr;
                }

                text_params->pos_x = left;
                text_params->pos_y = top;
                text_params->font_size = DEFAULT_FONT_SIZE_COORDINATES;
                text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

                text_params->border_color = (OSD_ColorParams){255,255,255,255};
                text_params->bg_color = (OSD_ColorParams){77,77,77,255};

                if (buffer)
                {
                    GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
                }
                else
                {
                    OsdMeta meta;
                    meta.meta_type = OSD_TEXT;
                    meta.params = (void *)text_params;
                    GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
                }
            }

            // Display retail name & confidence under bbox.
            {
                OSD_TextParams* text_params = (OSD_TextParams*)malloc(sizeof(OSD_TextParams));
                if (text_params)
                {
                    string text = obj_type + "    " + to_string(confidence);
                    // Use safe strncpy with explicit bounds checking
                    char* cstr = (char*)calloc(text.size() + 1, sizeof(char));
                    if (cstr)
                    {
                        strncpy(cstr, text.c_str(), text.size());
                        cstr[text.size()] = '\0';  // Guarantee null termination
                        text_params->text = cstr;
                    }

                    text_params->pos_x = left;
                    text_params->pos_y = bottom;
                    text_params->font_size = DEFAULT_FONT_SIZE_COORDINATES;
                    text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

                    text_params->border_color = (OSD_ColorParams){255,255,255,255};
                    text_params->bg_color = (OSD_ColorParams){77,77,77,255};

                    if (buffer)
                    {
                        GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
                    }
                    else
                    {
                        OsdMeta meta;
                        meta.meta_type = OSD_TEXT;
                        meta.params = (void *)text_params;
                        GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
                    }
                }
            }
        }
    }

    // First pass - calculate proximity states
    for (auto& object : activeObjectCorners)
    {
        if (object.first.empty() || (check_id &&
            find(m_bboxList.begin(), m_bboxList.end(), object.first) == m_bboxList.end()))
        {
            continue;
        }
        string obj_type = std::get<0>(object.second);
        string objectId = object.first;
        vector<Point2D> corners2d = std::get<1>(object.second);

        // Check if obj_type is in the comma-separated proximity class list
        bool isProximityClass = is_in_class_list(obj_type, box_params->m_overlay.m_proximityClass);

        if (isProximityClass)
        {
            Circle innerCircle = calculate_bottom_face_bounding_circle(corners2d, box_params->m_overlay.m_proximityAreaFactor);
            Circle outerCircle = calculate_bottom_face_bounding_circle(corners2d, box_params->m_overlay.m_proximityAreaFactor + 1); // 100% larger

            // Get the bottom face center instead of using the centroid of all corners
            Point2D bottomFaceCenter = calculate_bottom_face_center(corners2d);

            ProximityState state;
            state.corners = corners2d;
            state.objectId = objectId;
            state.objType = obj_type;  // Initialize objType
            state.centerX = bottomFaceCenter.x;
            state.centerY = bottomFaceCenter.y;
            state.radius = innerCircle.radius;
            state.confidence = std::get<2>(object.second);

            // Check if the object is in the inner circle
            for (const auto& otherObj : activeObjectCorners)
            {
                if (is_in_class_list(std::get<0>(otherObj.second), box_params->m_overlay.m_entrantClass))
                {
                    const vector<Point2D>& entrantCorners = std::get<1>(otherObj.second);

                    // Get the bottom face corners and center for the entrant
                    vector<Point2D> entrantBottomFaceCorners = extract_bottom_face_corners(entrantCorners);
                    Point2D entrantBottomCenter = calculate_bottom_face_center(entrantCorners);

                    // Store entrant bottom face center
                    if (entrantStates.find(otherObj.first) == entrantStates.end())
                    {
                        ProximityState newState;
                        newState.corners = entrantCorners;
                        newState.objectId = otherObj.first;
                        newState.objType = std::get<0>(otherObj.second);
                        newState.centerX = entrantBottomCenter.x;
                        newState.centerY = entrantBottomCenter.y;
                        newState.radius = 0;
                        newState.confidence = std::get<2>(otherObj.second);
                        entrantStates[otherObj.first] = newState;
                    }
                    else
                    {
                        entrantStates[otherObj.first].centerX = entrantBottomCenter.x;
                        entrantStates[otherObj.first].centerY = entrantBottomCenter.y;
                    }
                    bool inInnerCircle = false;
                    bool inOuterCircle = false;

                    // Check if any bottom face corner of the entrant is in the proximity circles
                    for (const Point2D& corner : entrantBottomFaceCorners)
                    {
                        float dx = corner.x - innerCircle.centerX;
                        float dy = corner.y - innerCircle.centerY;
                        float distance = sqrt(dx*dx + dy*dy);

                        if (distance <= innerCircle.radius)
                        {
                            inInnerCircle = true;
                            break; // Once we know it's in the inner circle, we can stop checking
                        }
                        else if (distance <= outerCircle.radius)
                        {
                            inOuterCircle = true;
                            // Don't break here as we might still find a corner in the inner circle
                        }
                    }

                    // Apply the state to both objects
                    if (inInnerCircle)
                    {
                        state.isInInnerCircle = true;
                        state.connectedEntrants.push_back(otherObj.first);
                        entrantStates[otherObj.first].isInInnerCircle = true;
                        entrantStates[otherObj.first].connectedProximity.push_back(objectId);
                    }
                    else if (inOuterCircle)
                    {
                        state.isInOuterCircle = true;
                        state.connectedEntrants.push_back(otherObj.first);
                        entrantStates[otherObj.first].isInOuterCircle = true;
                        entrantStates[otherObj.first].connectedProximity.push_back(objectId);
                    }
                }
            }
            proximityStates[objectId] = state;
        }
        else if (is_in_class_list(obj_type, box_params->m_overlay.m_entrantClass))
        {
            // Initialize entrant state if not already done
            if (entrantStates.find(objectId) == entrantStates.end())
            {
                ProximityState state;
                state.corners = corners2d;
                state.objectId = objectId;
                state.objType = obj_type;  // Initialize objType

                // Calculate entrant bottom face center
                Point2D bottomFaceCenter = calculate_bottom_face_center(corners2d);

                state.centerX = bottomFaceCenter.x;
                state.centerY = bottomFaceCenter.y;
                state.radius = 0;  // Set a default radius for entrants

                entrantStates[objectId] = state;
            }
        }
    }

    // Second pass - draw 3D bounding boxes with appropriate colors for proximity objects
    // Draw the circle around the proximity objects if circleOnly or circleAndLine is selected
    // Draw the ellipse around the proximity objects if ellipseOnly or ellipseAndLine is selected
    for (const auto& state : proximityStates)
    {
        // Draw the circle using CUOSD - now using the bottom face center and radius
        if (box_params->m_overlay.m_proximityAnimation == "circleOnly" ||
            box_params->m_overlay.m_proximityAnimation == "circleAndLine")
        {
            OSD_CircleParams* circle_params = (OSD_CircleParams*)malloc(sizeof(OSD_CircleParams));
            if (circle_params)
            {
                // Use the bottom face center for the circle
                Point center = interpolateCoordinate(state.second.centerX, state.second.centerY,
                                        m_sourceWidth, m_sourceHeight, m_width, m_height);
                float scaled_radius = state.second.radius * ((float)m_width / m_sourceWidth);

                circle_params->pos_x = center.x;
                circle_params->pos_y = center.y;
                circle_params->radius = scaled_radius;
                circle_params->thickness = 1;

                // Initialize colors to transparent
                circle_params->border_color = {0, 0, 0, 0};
                circle_params->bg_color = {0, 0, 0, 0};

                // Set border color if defined
                bool hasBorder = false;
                if (box_params->m_overlay.m_colorCode.find("proximity_bubble_border") != box_params->m_overlay.m_colorCode.end())
                {
                    auto color_vector = box_params->m_overlay.m_colorCode["proximity_bubble_border"];
                    circle_params->border_color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                    hasBorder = true;
                }

                // Set background color based on state if defined
                bool hasBackground = false;
                if (state.second.isInInnerCircle && 
                    box_params->m_overlay.m_colorCode.find("proximity_bubble_inner") != box_params->m_overlay.m_colorCode.end())
                {
                    auto color_vector = box_params->m_overlay.m_colorCode["proximity_bubble_inner"];
                    circle_params->bg_color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                    hasBackground = true;
                }
                else if (state.second.isInOuterCircle && 
                         box_params->m_overlay.m_colorCode.find("proximity_bubble_outer") != box_params->m_overlay.m_colorCode.end())
                {
                    auto color_vector = box_params->m_overlay.m_colorCode["proximity_bubble_outer"];
                    circle_params->bg_color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                    hasBackground = true;
                }
                else if (box_params->m_overlay.m_colorCode.find("proximity_bubble") != box_params->m_overlay.m_colorCode.end())
                {
                    auto color_vector = box_params->m_overlay.m_colorCode["proximity_bubble"];
                    circle_params->bg_color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                    hasBackground = true;
                }

                // Draw if we have either a border or background
                if (hasBorder || hasBackground)
                {
                    if (buffer)
                    {
                        GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_CIRCLE, circle_params);
                    }
                    else
                    {
                        OsdMeta meta;
                        meta.meta_type = OSD_CIRCLE;
                        meta.params = (void*)circle_params;
                        GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
                    }
                }
                else
                {
                    free(circle_params);
                }
            }
        }

        // Draw the ellipse using CUOSD - now using the bottom face center and radius
        if (box_params->m_overlay.m_proximityAnimation == "ellipseOnly" ||
            box_params->m_overlay.m_proximityAnimation == "ellipseAndLine")
        {
            OSD_EllipseParams* ellipse_params = (OSD_EllipseParams*)malloc(sizeof(OSD_EllipseParams));
            if (ellipse_params)
            {
                // Check if we have bbox3d coordinates in the original JSON
                bool found_3d_bbox = false;

// Projection of 3D sphere over bbox to 2D ellipse
#if 0
                float centerX, centerY, width, height, yaw;
                for (Json::Value::ArrayIndex i = 0; i < objects.size(); i++)
                {
                    Json::Value object = objects[i];
                    if (object.isMember("id") && object["id"].asString() == state.second.objectId)
                    {
                        // Found the object, check if it has bbox3d
                        if (object.isMember("bbox3d") && object["bbox3d"].isObject() &&
                            object["bbox3d"].isMember("coordinates") &&
                            object["bbox3d"]["coordinates"].isArray() &&
                            object["bbox3d"]["coordinates"].size() >= 12 &&
                            object["bbox3d"]["coordinates"][3].asFloat() > 0.0f &&
                            object["bbox3d"]["coordinates"][4].asFloat() > 0.0f &&
                            object["bbox3d"]["coordinates"][5].asFloat() > 0.0f)
                        {
                            // Extract coordinates
                            map<string, float, std::less<>> coordinates;
                            coordinates["x"] = object["bbox3d"]["coordinates"][0].asFloat();
                            coordinates["y"] = object["bbox3d"]["coordinates"][1].asFloat();
                            coordinates["z"] = object["bbox3d"]["coordinates"][2].asFloat();
                            coordinates["width"] = object["bbox3d"]["coordinates"][3].asFloat();
                            coordinates["length"] = object["bbox3d"]["coordinates"][4].asFloat();
                            coordinates["height"] = object["bbox3d"]["coordinates"][5].asFloat();
                            coordinates["yaw"] = object["bbox3d"]["coordinates"][8].asFloat();

                            // Get calibration data
                            std::lock_guard<std::mutex> guard(m_calibrationLock);
                            if (m_calibrationData.find(m_sensorName) != m_calibrationData.end())
                            {
                                // Use 3D sphere projection for ellipse parameters
                                int ret = calculate_3d_sphere_to_2d_ellipse(coordinates,
                                                                m_calibrationData[m_sensorName],
                                                                box_params->m_overlay.m_proximityAreaFactor,
                                                                centerX, centerY, width, height, yaw,
                                                                m_sourceWidth, m_sourceHeight);
                                if (ret == 0)
                                {
                                    continue;
                                }

                                // Scale to display resolution
                                Point center = interpolateCoordinate(centerX, centerY,
                                                                m_sourceWidth, m_sourceHeight, m_width, m_height);
                                ellipse_params->pos_x = center.x;
                                ellipse_params->pos_y = center.y;
                                ellipse_params->width = width * ((float)m_width / m_sourceWidth);
                                ellipse_params->height = height * ((float)m_height / m_sourceHeight);
                                ellipse_params->yaw = yaw;

                                found_3d_bbox = true;
                                break;
                            }
                        }
                    }
                }
#endif

                // If we couldn't find 3D bbox data, fall back to the existing method
                if (!found_3d_bbox)
                {
                    // Calculate 2D yaw from 3D bounding box
                    float yaw = 0.0f;
                    if (state.second.corners.size() == 8)
                    {
                        // Extract bottom face corners (z=0): indices 0, 2, 6, 4
                        Point2D bottom_front_left = state.second.corners[0];   // (0,0,0)
                        Point2D bottom_back_left = state.second.corners[2];    // (0,1,0)
                        Point2D bottom_back_right = state.second.corners[6];   // (1,1,0)
                        Point2D bottom_front_right = state.second.corners[4];  // (1,0,0)

                        // Calculate the centroid of the bottom face for more accurate positioning
                        float centerX = (bottom_front_left.x + bottom_back_left.x + bottom_back_right.x + bottom_front_right.x) / 4.0f;
                        float centerY = (bottom_front_left.y + bottom_back_left.y + bottom_back_right.y + bottom_front_right.y) / 4.0f;

                        // Update ellipse center to use the calculated centroid
                        Point center = interpolateCoordinate(centerX, centerY, m_sourceWidth, m_sourceHeight, m_width, m_height);
                        ellipse_params->pos_x = center.x;
                        ellipse_params->pos_y = center.y;

                        // Calculate direction vectors for all four edges of the bottom face
                        float dx1 = bottom_front_right.x - bottom_front_left.x;  // Front edge
                        float dy1 = bottom_front_right.y - bottom_front_left.y;
                        float dx2 = bottom_back_right.x - bottom_back_left.x;    // Back edge
                        float dy2 = bottom_back_right.y - bottom_back_left.y;
                        float dx3 = bottom_back_left.x - bottom_front_left.x;    // Left edge
                        float dy3 = bottom_back_left.y - bottom_front_left.y;
                        float dx4 = bottom_back_right.x - bottom_front_right.x;  // Right edge
                        float dy4 = bottom_back_right.y - bottom_front_right.y;

                        // Calculate lengths of each edge
                        float front_length = std::sqrt(dx1*dx1 + dy1*dy1);
                        float back_length = std::sqrt(dx2*dx2 + dy2*dy2);
                        float left_length = std::sqrt(dx3*dx3 + dy3*dy3);
                        float right_length = std::sqrt(dx4*dx4 + dy4*dy4);

                        // Determine which axis is longer (width vs depth)
                        float width_avg = (front_length + back_length) / 2.0f;
                        float depth_avg = (left_length + right_length) / 2.0f;

                        // Average the direction vectors to get more stable principal axes
                        float primary_dx, primary_dy;
                        float secondary_dx, secondary_dy;
                        float primary_length, secondary_length;

                        if (width_avg >= depth_avg)
                        {
                            // Width is the primary axis (front-back edges)
                            primary_dx = (dx1 + dx2) / 2.0f;
                            primary_dy = (dy1 + dy2) / 2.0f;
                            primary_length = width_avg;

                            secondary_dx = (dx3 + dx4) / 2.0f;
                            secondary_dy = (dy3 + dy4) / 2.0f;
                            secondary_length = depth_avg;
                        }
                        else
                        {
                            // Depth is the primary axis (left-right edges)
                            primary_dx = (dx3 + dx4) / 2.0f;
                            primary_dy = (dy3 + dy4) / 2.0f;
                            primary_length = depth_avg;

                            secondary_dx = (dx1 + dx2) / 2.0f;
                            secondary_dy = (dy1 + dy2) / 2.0f;
                            secondary_length = width_avg;
                        }

                        // Normalize the primary direction vector
                        if (primary_length > 0)
                        {
                            primary_dx /= primary_length;
                            primary_dy /= primary_length;
                            // Calculate the angle (yaw) from the primary axis
                            yaw = std::atan2(primary_dy, primary_dx);
                        }
                        else if (secondary_length > 0)
                        {
                            secondary_dx /= secondary_length;
                            secondary_dy /= secondary_length;
                            // Calculate the angle (yaw) from the secondary axis
                            yaw = std::atan2(secondary_dy, secondary_dx);
                        }

                        // Set ellipse dimensions based on the actual bottom face dimensions
                        // Clamp proximityAreaFactor to prevent float overflow
                        double clampedFactor = std::min(box_params->m_overlay.m_proximityAreaFactor, MAX_PROXIMITY_AREA_FACTOR);
                        float scaleFactor = static_cast<float>(clampedFactor * 3.0);
                        ellipse_params->width = primary_length * scaleFactor * ((float)m_width / m_sourceWidth);
                        ellipse_params->height = secondary_length * scaleFactor * ((float)m_height / m_sourceHeight);

                        // Ensure the width is always the longer axis
                        if (ellipse_params->height > ellipse_params->width)
                        {
                            std::swap(ellipse_params->width, ellipse_params->height);
                            // Adjust yaw by 90 degrees when swapping axes
                            yaw += M_PI / 2.0f;
                        }

                        // Normalize yaw to [-π, π]
                        while (yaw > M_PI) yaw -= 2.0f * M_PI;
                        while (yaw < -M_PI) yaw += 2.0f * M_PI;
                    }
                    else
                    {
                        // Use the bottom face center for the ellipse
                        Point center = interpolateCoordinate(state.second.centerX, state.second.centerY,
                                                        m_sourceWidth, m_sourceHeight, m_width, m_height);
                        ellipse_params->pos_x = center.x;
                        ellipse_params->pos_y = center.y;
                        // Fallback to radius-based calculation if corners aren't available
                        // Clamp proximityAreaFactor to prevent float overflow
                        double clampedFactor = std::min(box_params->m_overlay.m_proximityAreaFactor, MAX_PROXIMITY_AREA_FACTOR);
                        ellipse_params->width = state.second.radius * static_cast<float>(clampedFactor) * ((float)m_width / m_sourceWidth);
                        ellipse_params->height = state.second.radius * static_cast<float>(clampedFactor + 0.5) * ((float)m_height / m_sourceHeight);
                        if (state.second.corners.size() == 8)
                        {
                            // Extract bottom face corners (z=0): indices 0, 2, 6, 4
                            Point2D front_left = state.second.corners[0];   // (0,0,0)
                            Point2D front_right = state.second.corners[4];  // (1,0,0)

                            // Calculate angle of the front edge in 2D space
                            float dx = front_right.x - front_left.x;
                            float dy = front_right.y - front_left.y;
                            // Calculate angle in radians
                            yaw = atan2(dy, dx);
                        }
                    }

                    ellipse_params->yaw = yaw;
                    ellipse_params->thickness = 1;
                    // Initialize colors to transparent
                    ellipse_params->border_color = {0, 0, 0, 0};
                    ellipse_params->bg_color = {0, 0, 0, 0};

                    bool hasBorder = false;
                    bool hasBackground = false;

                    // Set border color if defined
                    if (box_params->m_overlay.m_colorCode.find("proximity_bubble_border") != box_params->m_overlay.m_colorCode.end())
                    {
                        auto color_vector = box_params->m_overlay.m_colorCode["proximity_bubble_border"];
                        ellipse_params->border_color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                        hasBorder = true;
                    }

                    // Set background color based on state
                    if (state.second.isInInnerCircle &&
                        box_params->m_overlay.m_colorCode.find("proximity_bubble_inner") != box_params->m_overlay.m_colorCode.end())
                    {
                        auto color_vector = box_params->m_overlay.m_colorCode["proximity_bubble_inner"];
                        ellipse_params->bg_color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                        hasBackground = true;
                    }
                    else if (state.second.isInOuterCircle &&
                            box_params->m_overlay.m_colorCode.find("proximity_bubble_outer") != box_params->m_overlay.m_colorCode.end())
                    {
                        auto color_vector = box_params->m_overlay.m_colorCode["proximity_bubble_outer"];
                        ellipse_params->bg_color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                        hasBackground = true;
                    }
                    else if (box_params->m_overlay.m_colorCode.find("proximity_bubble") != box_params->m_overlay.m_colorCode.end())
                    {
                        auto color_vector = box_params->m_overlay.m_colorCode["proximity_bubble"];
                        ellipse_params->bg_color = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                        hasBackground = true;
                    }

                    if (hasBackground || hasBorder)
                    {
                        if (!hasBorder)
                        {
                            // non-zero alpha is needed to draw ellipse
                            ellipse_params->border_color = {0, 0, 0, 1};
                        }
                        if (buffer)
                        {
                            GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_ELLIPSE, ellipse_params);
                        }
                        else
                        {
                            OsdMeta meta;
                            meta.meta_type = OSD_ELLIPSE;
                            meta.params = (void*)ellipse_params;
                            GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
                        }
                    }
                    else
                    {
                        free(ellipse_params);
                    }
                }
            }
        }

        OSD_ColorParams color;
        {
            // Get the matching class from the comma-separated list for color retrieval
            string matchingClass = get_matching_class(state.second.objType, box_params->m_overlay.m_proximityClass);
            if (!get_color_from_label(matchingClass.c_str(), box_params->m_overlay.m_colorCode, &color))
            {
                continue; // Skip drawing this object if no valid color found
            }
        }
        // Draw the 3D bounding box with the appropriate color
        int ret = draw_3d_bbox(state.second.corners,
                    get_matching_class(state.second.objType, box_params->m_overlay.m_proximityClass),
                    box_params, context, buffer, state.second.objectId, color, false, state.second.confidence);
        if (ret == 0)
        {
            continue;
        }

    }

    // For entrant objects, draw 3D bounding box with color dependent on their individual proximity
    for (const auto& state : entrantStates)
    {
        OSD_ColorParams color;
        {
            // Get the matching class from the comma-separated list for color retrieval
            string matchingClass = get_matching_class(state.second.objType, box_params->m_overlay.m_entrantClass);
            if (!get_color_from_label(matchingClass.c_str(), box_params->m_overlay.m_colorCode, &color))
            {
                continue; // Skip drawing this object if no valid color found
            }
        }

        // Draw the 3D bounding box with the appropriate color
        int ret = draw_3d_bbox(state.second.corners,
                    get_matching_class(state.second.objType, box_params->m_overlay.m_entrantClass),
                    box_params, context, buffer, state.second.objectId, color, false, state.second.confidence);
        if (ret == 0)
        {
            continue;
        }
    }

    // Draw connection lines between objects in proximity
    if (box_params->m_overlay.m_proximityAnimation == "circleAndLine" ||
        box_params->m_overlay.m_proximityAnimation == "lineOnly" ||
        box_params->m_overlay.m_proximityAnimation == "ellipseAndLine")
    {
        for (const auto& state : proximityStates)
        {
            for (const auto& entrantId : state.second.connectedEntrants)
            {
                if (entrantStates.find(entrantId) != entrantStates.end())
                {
                    // Draw line between bottom face centers
                    OSD_LineParams* line_params = (OSD_LineParams*)malloc(sizeof(OSD_LineParams));
                    if (line_params)
                    {
                        // Use the bottom face centers for both proximity and entrant objects
                        Point start = interpolateCoordinate(state.second.centerX, state.second.centerY,
                                                        m_sourceWidth, m_sourceHeight, m_width, m_height);
                        Point end = interpolateCoordinate(entrantStates[entrantId].centerX, entrantStates[entrantId].centerY,
                                                        m_sourceWidth, m_sourceHeight, m_width, m_height);

                        line_params->pos_x0 = start.x;
                        line_params->pos_y0 = start.y;
                        line_params->pos_x1 = end.x;
                        line_params->pos_y1 = end.y;

                        // Use white color for the line
                        OSD_ColorParams lineColor = OSD_COLOR_WHITE;
                        if (box_params->m_overlay.m_colorCode.find("proximity_line") != box_params->m_overlay.m_colorCode.end())
                        {
                            auto color_vector = box_params->m_overlay.m_colorCode["proximity_line"];
                            lineColor = {color_vector[0], color_vector[1], color_vector[2], color_vector[3]};
                        }
                        else
                        {
                            free(line_params);
                            continue;
                        }

                        draw_line_cuosd(line_params, box_params, context, buffer, lineColor, 1);

                        // Calculate the distance between objects in 2D space
                        float dx = state.second.centerX - entrantStates[entrantId].centerX;
                        float dy = state.second.centerY - entrantStates[entrantId].centerY;
                        float distance = sqrt(dx*dx + dy*dy) / (float)100.0f;

                        // Format distance as text (1 decimal places)
                        std::stringstream ss;
                        ss << std::fixed << std::setprecision(1) << distance;
                        std::string distance_text = ss.str() + "m";

                        // Calculate position for the text (midpoint of the line)
                        int text_x = (start.x + end.x) / 2;
                        int text_y = ((start.y + end.y) / 2) - 2; // Offset above the line

                        // Create text parameters
                        OSD_TextParams* text_params = (OSD_TextParams*)malloc(sizeof(OSD_TextParams));
                        if (text_params != nullptr)
                        {
                            // Use safe strncpy with explicit bounds checking
                            char* cstr = (char*)calloc(distance_text.size() + 1, sizeof(char));
                            if (cstr != nullptr)
                            {
                                strncpy(cstr, distance_text.c_str(), distance_text.size());
                                cstr[distance_text.size()] = '\0';  // Guarantee null termination
                                text_params->text = cstr;
                            }
                            else
                            {
                                LOG(error) << "Failed to allocate memory for distance text" << endl;
                                text_params->text = nullptr;
                            }

                            text_params->pos_x = text_x;
                            text_params->pos_y = text_y;
                            text_params->font_size = DEFAULT_FONT_SIZE_COORDINATES;
                            text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

                            /* Text background color */
                            text_params->border_color=(OSD_ColorParams){255,255,255,255};
                            text_params->bg_color=(OSD_ColorParams){77,77,77,200};

                            if (buffer)
                            {
                                GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
                            }
                            else
                            {
                                OsdMeta meta;
                                meta.meta_type = OSD_TEXT;
                                meta.params = (void *)text_params;
                                GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
                            }
                        }
                        free (line_params);
                        line_params = nullptr;
                    }
                }
            }
        }
    }

    // Clean up
    activeObjectCorners.clear();
    proximityStates.clear();
    entrantStates.clear();
}

void draw_point_cuosd(OSD_PointParams* point_params,
                BBoxDrawingData* box_params, OsdContext_t context, GstBuffer *buffer)
{
    /* Set circle radius */
    point_params->radius = box_params->m_overlay.m_bboxThickness;

    OSD_ColorParams color = OSD_COLOR_RED;
    if (box_params->m_overlay.m_bboxColor == "green") color = OSD_COLOR_GREEN;
    else if (box_params->m_overlay.m_bboxColor == "blue") color = OSD_COLOR_BLUE;
    else if (box_params->m_overlay.m_bboxColor == "black") color = OSD_COLOR_BLACK;
    else if (box_params->m_overlay.m_bboxColor == "white") color = OSD_COLOR_WHITE;
    else if (box_params->m_overlay.m_bboxColor == "yellow") color = OSD_COLOR_YELLOW;
    else color = OSD_COLOR_RED;
    point_params->color = color;
    point_params->color.alpha = box_params->m_overlay.m_bboxOpacity;
    auto point_params_copy = std::make_unique<OSD_PointParams>(*point_params);

    if (buffer)
    {
        GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_POINT, point_params_copy.release());
    }
    else
    {
        OsdMeta meta;
        meta.meta_type = OSD_POINT;
        meta.params = static_cast<void*>(point_params_copy.release());
        GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
    }
}

void draw_arrow_cuosd(OSD_ArrowParams* arrow_params,
                BBoxDrawingData* box_params, OsdContext_t context, GstBuffer *buffer)
{
    /* Set arrow width and size */
    arrow_params->thickness = box_params->m_overlay.m_bboxThickness;
    arrow_params->arrow_size = box_params->m_overlay.m_bboxThickness * ARROW_SIZE_SCALE_PARAMETER;
    arrow_params->is_interpolation = true;

    OSD_ColorParams color = OSD_COLOR_RED;
    if (box_params->m_overlay.m_bboxColor == "green") color = OSD_COLOR_GREEN;
    else if (box_params->m_overlay.m_bboxColor == "blue") color = OSD_COLOR_BLUE;
    else if (box_params->m_overlay.m_bboxColor == "black") color = OSD_COLOR_BLACK;
    else if (box_params->m_overlay.m_bboxColor == "white") color = OSD_COLOR_WHITE;
    else if (box_params->m_overlay.m_bboxColor == "yellow") color = OSD_COLOR_YELLOW;
    else color = OSD_COLOR_RED;
    arrow_params->color = color;
    arrow_params->color.alpha = box_params->m_overlay.m_bboxOpacity;
    auto arrow_params_copy = std::make_unique<OSD_ArrowParams>(*arrow_params);

    if (buffer)
    {
        GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_ARROW, arrow_params_copy.release());
    }
    else
    {
        OsdMeta meta;
        meta.meta_type = OSD_ARROW;
        meta.params = static_cast<void*>(arrow_params_copy.release());
        GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
    }
}

bool process_tripwire_stats(const string &sensorName, const string &tripwireId, const string &fromTimestamp, const string &toTimestamp, map<string, pair<int, int>, std::less<>> &diff_types, int &total_entry_count, int &total_exit_count)
{
    string url = GET_CONFIG().analytic_server_address;
    string api = ANALYTICS_API_GET_TRIPWIRE_STATS + sensorName;
    string answer_str;
    api += "&tripwireId=" + tripwireId + "&fromTimestamp=" + fromTimestamp + "&toTimestamp=" + toTimestamp + "&bucketSizeInSec=" + to_string(BUCKET_SIZE_IN_SEC_FOR_TRIPWIRE);

    if (!curlGetRequest(url + api, answer_str))
    {
        LOG(error) << "Failed to get tripwire stats details from: " << url + api << endl;
        return false;
    }

    Json::Value json_value = stringToJson(answer_str);
    Json::Value stat_details = json_value.get("tripwires", Json::Value::null);
    Json::Value histogram_details = Json::Value::null;

    if (stat_details != Json::Value::null)
    {
        histogram_details = stat_details[0].get("histogram", Json::Value::null);
    }

    if ((histogram_details != Json::Value::null) && (histogram_details.isArray()))
    {

        for (uint32_t i = 0; i < histogram_details.size(); i++)
        {
            Json::Value events_details = histogram_details[i].get("events", Json::Value::null);
            if (events_details != Json::Value::null && events_details.isArray())
            {
                for (uint32_t j = 0; j < events_details.size(); j++)
                {
                    Json::Value event = events_details[j];
                    Json::Value event_type = event.get("type", Json::Value::null);
                    Json::Value objects = event.get("objects", Json::Value::null);
                    if (objects != Json::Value::null && objects.isArray())
                    {
                        for (uint32_t k = 0; k < objects.size(); k++)
                        {
                            int count = objects[k].get("count", 0).asInt();
                            Json::Value object_type = objects[k].get("type", Json::Value::null);
                            if (event_type != Json::Value::null && event_type.asString() == "entry" && object_type != Json::Value::null && object_type.asString() == "*")
                            {
                                total_entry_count += count; // if object_type == "*" it represents the total count
                            }
                            else if (event_type != Json::Value::null && event_type.asString() == "exit" && object_type != Json::Value::null && object_type.asString() == "*")
                            {
                                total_exit_count += count; // if object_type == "*" it represents the total count
                            }
                            else if (event_type != Json::Value::null && event_type.asString() == "entry" && object_type != Json::Value::null && object_type.asString() != "")
                            {
                                diff_types[object_type.asString()].first++; // Storing entry of diff type of objects
                            }
                            else if (event_type != Json::Value::null && event_type.asString() == "exit" && object_type != Json::Value::null && object_type.asString() != "")
                            {
                                diff_types[object_type.asString()].second++; // Storing exit of diff type of objects
                            }
                        }
                    }
                }
            }
        }
    }
    return true;
}

bool NvLLOverlayInternal::doDraw (void* data, GstMetaUnion *meta, int64_t pts)
{
    bool ret = false;
    if (m_useId)
    {
        ret = processOsdSinkPadBufferProbeStreamer(data, meta->vstMeta);
    }
    ret = processOsdSinkPadBufferProbe(data, meta, pts);
    return ret;
}

void NvLLOverlayInternal::readTripwire()
{
    string url = GET_CONFIG().analytic_server_address;
    struct timeval currTime;
    int64_t time = 0;
    bool check_id = true;
    string utc_time_3600s_before, utc_time_2700s_before, utc_time_1800s_before, utc_time_900s_before, utc_time_now;

    while (m_tripwireExit == false)
    {
        if (m_lastTripwireReadTime)
        {
            m_tripwireSync.wait(ANALYTICS_SERVER_QUERY_INTERVAL_MSEC);
        }

        {
            std::lock_guard<std::mutex> guard(m_idLock);
            if (find(m_idList[TRIPWIRE].begin(), m_idList[TRIPWIRE].end(), "all") != m_idList[TRIPWIRE].end())
            {
                check_id = false;
            }

            if (check_id && find(m_idList[TRIPWIRE].begin(), m_idList[TRIPWIRE].end(), "none") != m_idList[TRIPWIRE].end())
            {
                continue;
            }
        }
        gettimeofday(&currTime, nullptr);
        time = (currTime.tv_sec * 1000000) + (currTime.tv_usec);

        std::lock_guard<std::mutex> guard(m_tripwireLock);
        std::map<string, Tripwire>::iterator it;
        Point p;

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        Resolution resolution;
        resolution = GET_CONFIG().webrtc_out_default_resolution;
        if (!resolution.empty() || NvHwDetection::getInstance()->m_useNvV4l2Enc == false)
        {
            if (m_bboxParams.m_overlay.m_bboxThickness > 3 && m_width <= WIDTH_480p)
            {
                m_bboxParams.m_overlay.m_bboxThickness = m_bboxParams.m_overlay.m_bboxThickness - 3;
            }
        }
#endif
        if (!m_lastTripwireReadTime || ((time - m_lastTripwireReadTime) >= ANALYTICS_READ_INTERVAL_USEC))
        {
            m_lastTripwireReadTime = time;
            // Remove existing tripwire details
            for (it = m_tripwireList.begin(); it != m_tripwireList.end(); it++)
            {
                Tripwire tripwire = it->second;
                for (uint32_t j = 0; j < MAX_LINES; j++)
                {
                    if (tripwire.wires[j])
                    {
                        free(tripwire.wires[j]);
                        tripwire.wires[j] = nullptr;
                    }
                }
                for (uint32_t j = 0; j < MAX_POINTS; j++)
                {
                    if (tripwire.endpoints[j])
                    {
                        free(tripwire.endpoints[j]);
                        tripwire.endpoints[j] = nullptr;
                    }
                }
                for (uint32_t j = 0; j < MAX_ARROWS; j++)
                {
                    if (tripwire.direction[j])
                    {
                        free(tripwire.direction[j]);
                        tripwire.direction[j] = nullptr;
                    }
                }
            }
            m_tripwireList.clear();

            /* Get the tripwire coordinates from the eMDAT server */
            string tripwire_api = ANALYTICS_API_GET_TRIPWIRE + m_sensorName;
            string answer;
            if (!curlGetRequest(url + tripwire_api, answer))
            {
                LOG(error) << "Failed to get tripwire details from: " << url + tripwire_api << endl;
                continue;
            }

            Json::Value json_value = stringToJson(answer);
            Json::Value tripwire_details = json_value.get("tripwires", Json::Value::null);
            if (tripwire_details != Json::Value::null && tripwire_details.isArray())
            {
                for (uint32_t i = 0; i < tripwire_details.size(); i++)
                {
                    Tripwire tripwire;
                    tripwire.direction_count = tripwire.endpoints_count = tripwire.wires_count = 0;
                    for (uint32_t j = 0; j < MAX_LINES; j++)
                    {
                        tripwire.wires[j] = (OSD_LineParams *)malloc(sizeof(OSD_LineParams));
                    }
                    for (uint32_t j = 0; j < MAX_POINTS; j++)
                    {
                        tripwire.endpoints[j] = (OSD_PointParams *)malloc(sizeof(OSD_PointParams));
                    }
                    for (uint32_t j = 0; j < MAX_ARROWS; j++)
                    {
                        tripwire.direction[j] = (OSD_ArrowParams *)malloc(sizeof(OSD_ArrowParams));
                    }

                    Json::Value wire = tripwire_details[i].get("wire", Json::Value::null);
                    Json::Value directions = tripwire_details[i].get("direction", Json::Value::null);

                    if (tripwire_details[i].get("id", Json::Value::null) != Json::Value::null)
                    {
                        tripwire.id = tripwire_details[i]["id"].asString();
                    }
                    else
                    {
                        LOG(error) << "No ID for tripwire" << endl;
                    }
                    if (tripwire_details[i].get("name", Json::Value::null) != Json::Value::null)
                    {
                        tripwire.name = tripwire_details[i]["name"].asString();
                    }

                    if (wire != Json::Value::null)
                    {
                        for (uint32_t j = 0; (j + 1) < wire.size(); j++)
                        {
                            p = interpolateCoordinate(wire[j]["x"].asInt(), wire[j]["y"].asInt(), m_sourceWidth, m_sourceHeight, m_width, m_height);
                            tripwire.wires[tripwire.wires_count]->pos_x0 = p.x;
                            tripwire.wires[tripwire.wires_count]->pos_y0 = p.y;
                            tripwire.endpoints[tripwire.endpoints_count]->pos_x = tripwire.wires[tripwire.wires_count]->pos_x0;
                            tripwire.endpoints[tripwire.endpoints_count]->pos_y = tripwire.wires[tripwire.wires_count]->pos_y0;
                            tripwire.endpoints_count = (tripwire.endpoints_count + 1) % MAX_POINTS;

                            p = interpolateCoordinate(wire[j + 1]["x"].asInt(), wire[j + 1]["y"].asInt(), m_sourceWidth, m_sourceHeight, m_width, m_height);
                            tripwire.wires[tripwire.wires_count]->pos_x1 = p.x;
                            tripwire.wires[tripwire.wires_count]->pos_y1 = p.y;
                            tripwire.endpoints[tripwire.endpoints_count]->pos_x = tripwire.wires[tripwire.wires_count]->pos_x1;
                            tripwire.endpoints[tripwire.endpoints_count]->pos_y = tripwire.wires[tripwire.wires_count]->pos_y1;

                            tripwire.endpoints_count = (tripwire.endpoints_count + 1) % MAX_POINTS;
                            tripwire.wires_count = (tripwire.wires_count + 1) % MAX_LINES;
                        }
                    }
                    if (directions != Json::Value::null)
                    {
                        if (directions.get("p1", Json::Value::null) != Json::Value::null &&
                            directions.get("p2", Json::Value::null) != Json::Value::null)
                        {
                            p = interpolateCoordinate(directions["p1"]["x"].asInt(), directions["p1"]["y"].asInt(), m_sourceWidth, m_sourceHeight, m_width, m_height);
                            tripwire.direction[tripwire.direction_count]->pos_x0 = p.x;
                            tripwire.direction[tripwire.direction_count]->pos_y0 = p.y;
                            tripwire.endpoints[tripwire.endpoints_count]->pos_x = tripwire.direction[tripwire.direction_count]->pos_x0;
                            tripwire.endpoints[tripwire.endpoints_count]->pos_y = tripwire.direction[tripwire.direction_count]->pos_y0;
                            tripwire.endpoints_count = (tripwire.endpoints_count + 1) % MAX_POINTS;

                            p = interpolateCoordinate(directions["p2"]["x"].asInt(), directions["p2"]["y"].asInt(), m_sourceWidth, m_sourceHeight, m_width, m_height);
                            tripwire.direction[tripwire.direction_count]->pos_x1 = p.x;
                            tripwire.direction[tripwire.direction_count]->pos_y1 = p.y;
                            tripwire.endpoints[tripwire.endpoints_count]->pos_x = tripwire.direction[tripwire.direction_count]->pos_x1;
                            tripwire.endpoints[tripwire.endpoints_count]->pos_y = tripwire.direction[tripwire.direction_count]->pos_y1;
                            tripwire.endpoints_count = (tripwire.endpoints_count + 1) % MAX_POINTS;
                            tripwire.direction_count = (tripwire.direction_count + 1) % MAX_ARROWS;
                        }
                    }
                    // Add to the list
                    m_tripwireList[tripwire.id] = tripwire;
                }
            }
        }
        utc_time_3600s_before = getOffsetUtcTime(-TRIPWIRE_STATS_INTERVAL_MSEC4);
        utc_time_2700s_before = getOffsetUtcTime(-TRIPWIRE_STATS_INTERVAL_MSEC3);
        utc_time_1800s_before = getOffsetUtcTime(-TRIPWIRE_STATS_INTERVAL_MSEC2);
        utc_time_900s_before = getOffsetUtcTime(-TRIPWIRE_STATS_INTERVAL_MSEC1);
        utc_time_now = getOffsetUtcTime(0);
        for (it = m_tripwireList.begin(); it != m_tripwireList.end(); it++)
        {
            /* Get the tripwire coordinates from the emdx server */
            Tripwire tripwire = it->second;
            {
                std::lock_guard<std::mutex> guard(m_idLock);
                // Check for id in the overlay list and process only if in list
                if (check_id && find(m_idList[TRIPWIRE].begin(), m_idList[TRIPWIRE].end(),
                                     tripwire.id) == m_idList[TRIPWIRE].end())
                    continue;
            }

            map<string, pair<int, int>, std::less<>> diff_types;
            int total_entry_count = 0;
            int total_exit_count = 0;

            bool api_response1 = process_tripwire_stats(m_sensorName, it->first, utc_time_3600s_before, utc_time_2700s_before, diff_types, total_entry_count, total_exit_count); // Tripwire details of past 45-60 min
            bool api_response2 = process_tripwire_stats(m_sensorName, it->first, utc_time_2700s_before, utc_time_1800s_before, diff_types, total_entry_count, total_exit_count); // Tripwire details of past 30-45 min
            bool api_response3 = process_tripwire_stats(m_sensorName, it->first, utc_time_1800s_before, utc_time_900s_before, diff_types, total_entry_count, total_exit_count);  // Tripwire details of past 15-30 min
            bool api_response4 = process_tripwire_stats(m_sensorName, it->first, utc_time_900s_before, utc_time_now, diff_types, total_entry_count, total_exit_count);           // Tripwire details for past 15 min

            if (api_response1 && api_response2 && api_response3 && api_response4)
            {
                string text = "Total\n";
                text += "in = " + to_string(total_entry_count) + " out = " + to_string(total_exit_count);
                for (const auto &it : diff_types)
                {
                    text += "\n";
                    text += it.first + " in = " + to_string(it.second.first) + " out = " + to_string(it.second.second);
                }
                tripwire.stats = text;
                it->second = tripwire;
            }
            diff_types.clear();
        }
    }
}

void NvLLOverlayInternal::drawTripwire(GstBuffer* buffer)
{
    bool check_id = true;
    {
        std::lock_guard<std::mutex> guard(m_idLock);
        if (find(m_idList[TRIPWIRE].begin(), m_idList[TRIPWIRE].end(), "all") != m_idList[TRIPWIRE].end())
        {
            check_id = false;
        }

        if (check_id && find(m_idList[TRIPWIRE].begin(), m_idList[TRIPWIRE].end(), "none") != m_idList[TRIPWIRE].end())
        {
            return;
        }
    }

    std::lock_guard<std::mutex> guard(m_tripwireLock);
    std::map<string, Tripwire>::iterator it = m_tripwireList.begin();
    // Draw tripwires based on rules
    for ( ; it != m_tripwireList.end(); it++)
    {
        Tripwire tripwire = it->second;
        {
            std::lock_guard<std::mutex> guard(m_idLock);
            // Check for id in the overlay list and process only if in list
            if (check_id && find(m_idList[TRIPWIRE].begin(), m_idList[TRIPWIRE].end(),
                    tripwire.id) == m_idList[TRIPWIRE].end())
                continue;
        }

        for (uint32_t j = 0; j < tripwire.wires_count; j++)
        {
            draw_line_cuosd(tripwire.wires[j], &m_bboxParams, (OsdContext_t)osd_ctx, m_isGst ? buffer : nullptr);
        }
        for (uint32_t j = 0; j < tripwire.endpoints_count; j++)
        {
            draw_point_cuosd(tripwire.endpoints[j], &m_bboxParams, (OsdContext_t)osd_ctx, m_isGst ? buffer : nullptr);
        }
        for (uint32_t j = 0; j < tripwire.direction_count && j < MAX_ARROWS; j++)
        {
            draw_arrow_cuosd(tripwire.direction[j], &m_bboxParams, (OsdContext_t)osd_ctx, m_isGst ? buffer : nullptr);
        }
        if (tripwire.stats.size())
        {
            OSD_TextParams* text_params=(OSD_TextParams*)malloc(sizeof(OSD_TextParams));
            if (text_params != nullptr)
            {
                char* cstr = (char*)malloc(tripwire.stats.size() + 1);
                if (cstr != nullptr)
                {
                    strcpy(cstr, tripwire.stats.c_str());
                    text_params->text = cstr;
                }

                /* Now set the offsets where the string should appear */
                text_params->pos_x = tripwire.direction[0]->pos_x0;
                text_params->pos_y = tripwire.direction[0]->pos_y0;
                text_params->font_size = DEFAULT_FONT_SIZE_COORDINATES +
                            m_bboxParams.m_overlay.m_bboxThickness;
                text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

                /* Text background color */
                text_params->border_color=(OSD_ColorParams){255,255,255,255};
                text_params->bg_color=(OSD_ColorParams){77,77,77,200};

                if (m_isGst)
                {
                    GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
                }
                else
                {
                    OsdMeta meta;
                    meta.meta_type = OSD_TEXT;
                    meta.params = (void *)text_params;
                    GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
                }
            }
        }
    }
}

void NvLLOverlayInternal::readRoi()
{
    string url = GET_CONFIG().analytic_server_address;
    struct timeval currTime;
    int64_t time = 0;
    bool check_id = true;
    string utc_time_1s_before;

    while (m_roiExit == false)
    {
        if (m_lastRoiReadTime)
        {
            m_roiSync.wait(ANALYTICS_SERVER_QUERY_INTERVAL_MSEC);
        }

        {
            std::lock_guard<std::mutex> guard(m_idLock);
            if (find(m_idList[ROI].begin(), m_idList[ROI].end(), "all") != m_idList[ROI].end())
            {
                check_id = false;
            }

            if (check_id && find(m_idList[ROI].begin(), m_idList[ROI].end(), "none") != m_idList[ROI].end())
            {
                continue;
            }
        }

        gettimeofday(&currTime, nullptr);
        time = (currTime.tv_sec * 1000000) + (currTime.tv_usec);

        std::lock_guard<std::mutex> guard(m_roiLock);
        std::map<string, Roi>::iterator it;
        Point p;

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        Resolution resolution;
        resolution = GET_CONFIG().webrtc_out_default_resolution;
        if (!resolution.empty() || NvHwDetection::getInstance()->m_useNvV4l2Enc == false)
        {
            if (m_bboxParams.m_overlay.m_bboxThickness > 3 && m_width <= WIDTH_480p)
            {
                m_bboxParams.m_overlay.m_bboxThickness = m_bboxParams.m_overlay.m_bboxThickness - 3;
            }
        }
#endif
        if (!m_lastRoiReadTime || ((time - m_lastRoiReadTime) >= ANALYTICS_READ_INTERVAL_USEC))
        {
            m_lastRoiReadTime = time;
            for (it = m_roiList.begin(); it != m_roiList.end(); it++)
            {
                Roi roi = it->second;
                for (uint32_t j = 0; j < MAX_LINES; j++)
                {
                    if (roi.lines[j])
                    {
                        free(roi.lines[j]);
                        roi.lines[j] = nullptr;
                    }
                }
                for (uint32_t j = 0; j < MAX_POINTS; j++)
                {
                    if (roi.endpoints[j])
                    {
                        free(roi.endpoints[j]);
                        roi.endpoints[j] = nullptr;
                    }
                }
            }
            m_roiList.clear();

            /* Get the roi coordinates from the eMDAT server */
            string roi_api = ANALYTICS_API_GET_ROI + m_sensorName;
            string answer;
            if (!curlGetRequest(url + roi_api, answer))
            {
                LOG(error) << "Failed to get roi details from: " << url + roi_api << endl;
                continue;
            }

            Json::Value json_value = stringToJson(answer);
            Json::Value roi_details = json_value.get("rois", Json::Value::null);
            if (roi_details != Json::Value::null && roi_details.isArray())
            {
                for (uint32_t i = 0; i < roi_details.size(); i++)
                {
                    Roi roi;
                    roi.lines_count = roi.endpoints_count = 0;
                    for (uint32_t j = 0; j < MAX_LINES; j++)
                    {
                        roi.lines[j] = (OSD_LineParams *)malloc(sizeof(OSD_LineParams));
                    }
                    for (uint32_t j = 0; j < MAX_POINTS; j++)
                    {
                        roi.endpoints[j] = (OSD_PointParams *)malloc(sizeof(OSD_PointParams));
                    }
                    Json::Value roi_coord = roi_details[i].get("coordinates", Json::Value::null);
                    if (roi_details[i].get("id", Json::Value::null) != Json::Value::null)
                    {
                        roi.id = roi_details[i]["id"].asString();
                    }
                    else
                    {
                        LOG(error) << "No ID for ROI, Ignoring." << endl;
                    }
                    if (roi_details[i].get("name", Json::Value::null) != Json::Value::null)
                    {
                        roi.name = roi_details[i]["name"].asString();
                    }

                    if (roi_coord.isArray())
                    {
                        p = interpolateCoordinate(roi_coord[0]["x"].asInt(), roi_coord[0]["y"].asInt(), m_sourceWidth, m_sourceHeight, m_width, m_height);
                        roi.lines[roi.lines_count]->pos_x1 = p.x;
                        roi.lines[roi.lines_count]->pos_y1 = p.y;
                        roi.endpoints[roi.endpoints_count]->pos_x = roi.lines[roi.lines_count]->pos_x1;
                        roi.endpoints[roi.endpoints_count]->pos_y = roi.lines[roi.lines_count]->pos_y1;
                        roi.endpoints_count = (roi.endpoints_count + 1) % MAX_POINTS;

                        p = interpolateCoordinate(roi_coord[roi_coord.size() - 1]["x"].asInt(), roi_coord[roi_coord.size() - 1]["y"].asInt(), m_sourceWidth, m_sourceHeight, m_width, m_height);
                        roi.lines[roi.lines_count]->pos_x0 = p.x;
                        roi.lines[roi.lines_count]->pos_y0 = p.y;
                        roi.endpoints[roi.endpoints_count]->pos_x = roi.lines[roi.lines_count]->pos_x0;
                        roi.endpoints[roi.endpoints_count]->pos_y = roi.lines[roi.lines_count]->pos_y0;

                        roi.endpoints_count = (roi.endpoints_count + 1) % MAX_POINTS;
                        roi.lines_count = (roi.lines_count + 1) % MAX_LINES;
                        for (uint32_t j = 1; j < roi_coord.size(); j++)
                        {
                            p = interpolateCoordinate(roi_coord[j]["x"].asInt(), roi_coord[j]["y"].asInt(), m_sourceWidth, m_sourceHeight, m_width, m_height);
                            roi.lines[roi.lines_count]->pos_x0 = p.x;
                            roi.lines[roi.lines_count]->pos_y0 = p.y;
                            roi.endpoints[roi.endpoints_count]->pos_x = roi.lines[roi.lines_count]->pos_x0;
                            roi.endpoints[roi.endpoints_count]->pos_y = roi.lines[roi.lines_count]->pos_y0;
                            roi.endpoints_count = (roi.endpoints_count + 1) % MAX_POINTS;

                            p = interpolateCoordinate(roi_coord[j - 1]["x"].asInt(), roi_coord[j - 1]["y"].asInt(), m_sourceWidth, m_sourceHeight, m_width, m_height);
                            roi.lines[roi.lines_count]->pos_x1 = p.x;
                            roi.lines[roi.lines_count]->pos_y1 = p.y;
                            roi.endpoints[roi.endpoints_count]->pos_x = roi.lines[roi.lines_count]->pos_x1;
                            roi.endpoints[roi.endpoints_count]->pos_y = roi.lines[roi.lines_count]->pos_y1;

                            roi.endpoints_count = (roi.endpoints_count + 1) % MAX_POINTS;
                            roi.lines_count = (roi.lines_count + 1) % MAX_LINES;
                        }
                    }
                    // Add to the list
                    m_roiList[roi.id] = roi;
                }
            }
        }
        utc_time_1s_before = getOffsetUtcTime(-ROI_STATS_INTERVAL_MSEC);
        for (it = m_roiList.begin(); it != m_roiList.end(); it++)
        {
            /* Get the ROI coordinates from the emdx server */
            string api = ANALYTICS_API_GET_ROI_STATS + m_sensorName;
            string answer_str;
            Roi roi = it->second;
            {
                std::lock_guard<std::mutex> guard(m_idLock);
                // Check for id in the overlay list and process only if in list
                if (check_id && find(m_idList[ROI].begin(), m_idList[ROI].end(),
                        roi.id) == m_idList[ROI].end())
                    continue;
            }
            api += "&roiId=" + it->first + "&fromTimestamp=" + utc_time_1s_before + "&bucketSizeInSec=" + to_string(BUCKET_SIZE_IN_SEC_FOR_ROI);
            if (!curlGetRequest(url + api, answer_str))
            {
                LOG(error) << "Failed to get roi stats details from: " << url + api << endl;
            }

            Json::Value json_value = stringToJson(answer_str);
            Json::Value stat_details = json_value.get("rois", Json::Value::null);
            Json::Value histogram_details = Json::Value::null;

            if (stat_details != Json::Value::null)
            {
                histogram_details = stat_details[0].get("histogram", Json::Value::null);
            }
            if (histogram_details != Json::Value::null && histogram_details.isArray())
            {
                string text = "roi ";
                if (histogram_details.size())
                {

                    Json::Value objects = histogram_details[0].get("objects", Json::Value::null);
                    if (objects != Json::Value::null && objects.isArray())
                    {

                        Json::Value object_type = objects[0].get("type", Json::Value::null);
                        if (object_type != Json::Value::null && object_type.asString() == "*")
                        {
                            text += "current = " + to_string(objects[0].get("maxCount", 0).asInt64()) + " ";
                        }
                    }
                    roi.stats = text;
                }
            }
            it->second = roi;
        }
    }
}

void NvLLOverlayInternal::drawRoi(GstBuffer* buffer)
{
    bool check_id = true;
    {
        std::lock_guard<std::mutex> guard(m_idLock);
        if (find(m_idList[ROI].begin(), m_idList[ROI].end(), "all") != m_idList[ROI].end())
        {
            check_id = false;
        }

        if (check_id && find(m_idList[ROI].begin(), m_idList[ROI].end(), "none") != m_idList[ROI].end())
        {
            return;
        }
    }

    std::lock_guard<std::mutex> guard(m_roiLock);
    std::map<string, Roi>::iterator it = m_roiList.begin();
    // Draw rois based on rules
    for ( ; it != m_roiList.end(); it++)
    {
        Roi roi = it->second;
        {
            std::lock_guard<std::mutex> guard(m_idLock);
            // Check for id in the overlay list and process only if in list
            if (check_id && find(m_idList[ROI].begin(), m_idList[ROI].end(), roi.id) == m_idList[ROI].end())
                continue;
        }

        for (uint32_t j = 0; j < roi.lines_count; j++)
        {
            draw_line_cuosd(roi.lines[j], &m_bboxParams, (OsdContext_t)osd_ctx, m_isGst ? buffer : nullptr);
        }
        for (uint32_t j = 0; j < roi.endpoints_count; j++)
        {
            draw_point_cuosd(roi.endpoints[j], &m_bboxParams, (OsdContext_t)osd_ctx, m_isGst ? buffer : nullptr);
        }
        if (roi.stats.size())
        {
            OSD_TextParams* text_params=(OSD_TextParams*)malloc(sizeof(OSD_TextParams));
            if (text_params != nullptr)
            {
                char* cstr = (char*)malloc(roi.stats.size() + 1);
                if (cstr != nullptr)
                {
                    strcpy(cstr, roi.stats.c_str());
                    text_params->text = cstr;
                }

                /* Now set the offsets where the string should appear */
                text_params->pos_x = roi.endpoints[0]->pos_x;
                text_params->pos_y = roi.endpoints[0]->pos_y;
                text_params->font_size = DEFAULT_FONT_SIZE_COORDINATES +
                            m_bboxParams.m_overlay.m_bboxThickness;
                text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

                /* Text background color */
                text_params->border_color=(OSD_ColorParams){255,255,255,255};
                text_params->bg_color=(OSD_ColorParams){77,77,77,200};

                if (m_isGst)
                {
                    GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
                }
                else
                {
                    OsdMeta meta;
                    meta.meta_type = OSD_TEXT;
                    meta.params = (void *)text_params;
                    GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
                }
            }
        }
    }
}

// Helper function to convert OpenCV matrix to Json::Value
Json::Value matrixToJson(const cv::Mat& matrix)
{
    Json::Value j(Json::arrayValue);
    for (int i = 0; i < matrix.rows; ++i)
    {
        Json::Value row(Json::arrayValue);
        for (int j = 0; j < matrix.cols; ++j)
        {
            row.append(matrix.at<float>(i, j));
        }
        j.append(row);
    }
    return j;
}

// Load camera calibration information from a BEVFormer scene JSON file
static Json::Value loadCalibBEVFormerScene(const std::string &calibFilePath)
{
    try
    {
        std::ifstream file(calibFilePath);
        if (!file.is_open())
        {
            LOG(error) << "Failed to open file " << calibFilePath << endl;
            return Json::nullValue;
        }

        Json::Value calibration;
        Json::Reader reader;
        if (!reader.parse(file, calibration))
        {
            LOG(error) << "Failed to parse calibration data from file" << endl;
            return Json::nullValue;
        }
        return calibration;
    }
    catch (const std::exception &e)
    {
        LOG(error) << "Error: " << e.what() << endl;
        return Json::nullValue;
    }
}

// Load camera calibration data from a synthetic JSON file and convert it to a calibration dictionary
static Json::Value loadCalibSyntheticJson(const std::string &calibFilePath, bool recentering)
{
    try
    {
        std::ifstream file(calibFilePath);
        float translationToGlobalCoordinatesX = 0.0f;
        float translationToGlobalCoordinatesY = 0.0f;
        float scaleFactor = 1.0f;

        if (!file.is_open())
        {
            LOG(error) << "Failed to open file " << calibFilePath << endl;
            return Json::nullValue;
        }

        Json::Value calibJson;
        Json::Reader reader;
        if (!reader.parse(file, calibJson))
        {
            LOG(error) << "Failed to parse calibration data from file" << endl;
            return Json::nullValue;
        }

        Json::Value calibDict;

        if (!calibJson.isMember("sensors") || !calibJson["sensors"].isArray())
        {
            LOG(error) << "Invalid calibration data format: missing sensors array" << endl;
            return Json::nullValue;
        }

        for (const auto &sensor : calibJson["sensors"])
        {
            if (!sensor.isMember("id") || !sensor["id"].isString())
            {
                continue;
            }

            std::string camName = sensor["id"].asString();

            if (!sensor.isMember("intrinsicMatrix") || !sensor["intrinsicMatrix"].isArray() ||
                !sensor.isMember("extrinsicMatrix") || !sensor["extrinsicMatrix"].isArray())
            {
                LOG(error) << "Missing required matrices for sensor " << camName << endl;
                continue;
            }

            // Parse translationToGlobalCoordinates
            if (sensor.isMember("translationToGlobalCoordinates") &&
                sensor["translationToGlobalCoordinates"].isObject())
            {
                if (sensor["translationToGlobalCoordinates"].isMember("x") &&
                    sensor["translationToGlobalCoordinates"]["x"].isNumeric())
                {
                    translationToGlobalCoordinatesX = sensor["translationToGlobalCoordinates"]["x"].asFloat();
                }
                if (sensor["translationToGlobalCoordinates"].isMember("y") &&
                    sensor["translationToGlobalCoordinates"]["y"].isNumeric())
                {
                    translationToGlobalCoordinatesY = sensor["translationToGlobalCoordinates"]["y"].asFloat();
                }
            }

            // Parse scaleFactor
            if (sensor.isMember("scaleFactor") && sensor["scaleFactor"].isNumeric())
            {
                scaleFactor = sensor["scaleFactor"].asFloat();
            }

            // Convert JSON arrays to OpenCV matrices
            cv::Mat intrinExt = cv::Mat::eye(4, 4, CV_32F);
            std::vector<float> intrinsicFlat;
            for (const auto &val : sensor["intrinsicMatrix"])
            {
                intrinsicFlat.push_back(val.asFloat());
            }
            cv::Mat intrinsicMat(3, 3, CV_32F, intrinsicFlat.data());
            intrinsicMat.copyTo(intrinExt(cv::Rect(0, 0, 3, 3)));

            cv::Mat extrin = cv::Mat::eye(4, 4, CV_32F);
            std::vector<float> extrinsicFlat;
            for (const auto &val : sensor["extrinsicMatrix"])
            {
                extrinsicFlat.push_back(val.asFloat());
            }
            cv::Mat extrinsicMat(3, 4, CV_32F, extrinsicFlat.data());
            extrinsicMat.copyTo(extrin(cv::Rect(0, 0, 4, 3)));

            cv::Mat projW2C;

            if (recentering)
            {
                projW2C = extrin.clone();
            }
            else
            {
                // Left-handed to right-handed conversion
                cv::Mat yFlip = cv::Mat::eye(4, 4, CV_32F);
                yFlip.at<float>(1, 1) = -1;
                cv::Mat zFlip = cv::Mat::eye(4, 4, CV_32F);
                zFlip.at<float>(2, 2) = -1;
                extrin = extrin * yFlip * zFlip;
                projW2C = extrin.inv();
            }

            cv::Mat projW2P = intrinExt * projW2C;

            calibDict[camName]["intrinsic matrix"] = matrixToJson(intrinExt(cv::Rect(0, 0, 3, 3)));
            calibDict[camName]["projection matrix w2c"] = matrixToJson(projW2C);
            calibDict[camName]["projection matrix w2p"] = matrixToJson(projW2P);
            calibDict[camName]["translationToGlobalCoordinates"] = Json::Value(Json::objectValue);
            calibDict[camName]["translationToGlobalCoordinates"]["x"] = translationToGlobalCoordinatesX;
            calibDict[camName]["translationToGlobalCoordinates"]["y"] = translationToGlobalCoordinatesY;
            calibDict[camName]["scaleFactor"] = scaleFactor;
        }
        return calibDict;
    }
    catch (const std::exception &e)
    {
        LOG(error) << "Error: " << e.what() << endl;
        return Json::nullValue;
    }
}

// Function to load group information from JSON
static std::pair<std::string, std::map<std::string, std::vector<float>>> loadGroup(
    const Json::Value &groupDict, const std::string &key)
{
    try
    {
        // Check if required fields exist
        if (!groupDict.isMember("type") || !groupDict.isMember("name"))
        {
            return {"", {}};
        }

        // First check if the type matches the key
        if (groupDict["type"].asString() != key)
        {
            return {"", {}};
        }

        // If we get here, the type matches the key, so extract the group name
        std::string groupName = groupDict["name"].asString();
        std::map<std::string, std::vector<float>> groupInfoDict;

        // Safely extract origin
        if (groupDict.isMember("origin"))
        {
            std::vector<float> origin;
            if (groupDict["origin"].isArray())
            {
                for (const auto &val : groupDict["origin"])
                {
                    origin.push_back(val.asFloat());
                }
            }
            else if (groupDict["origin"].isObject())
            {
                if (groupDict["origin"].isMember("x") && groupDict["origin"].isMember("y"))
                {
                    origin.push_back(groupDict["origin"]["x"].asFloat());
                    origin.push_back(groupDict["origin"]["y"].asFloat());
                }
            }
            else
            {
                origin = {0.0f, 0.0f}; // Default values
            }
            groupInfoDict["origin"] = origin;
        }

        // Safely extract dimensions
        if (groupDict.isMember("dimensions"))
        {
            std::vector<float> dimensions;
            if (groupDict["dimensions"].isArray())
            {
                for (const auto &val : groupDict["dimensions"])
                {
                    dimensions.push_back(val.asFloat());
                }
            }
            else if (groupDict["dimensions"].isObject())
            {
                if (groupDict["dimensions"].isMember("width"))
                {
                    dimensions.push_back(groupDict["dimensions"]["width"].asFloat());
                }
                if (groupDict["dimensions"].isMember("height"))
                {
                    dimensions.push_back(groupDict["dimensions"]["height"].asFloat());
                }
                if (groupDict["dimensions"].isMember("x"))
                {
                    dimensions.push_back(groupDict["dimensions"]["x"].asFloat());
                }
                if (groupDict["dimensions"].isMember("y"))
                {
                    dimensions.push_back(groupDict["dimensions"]["y"].asFloat());
                }
            }
            groupInfoDict["dimensions"] = dimensions;
        }
        return {groupName, groupInfoDict};
    }
    catch (const std::exception &e)
    {
        LOG(error) << "Exception in loadGroup: " << e.what() << endl;
        return {"", {}};
    }
}

// Function to load calibration data for synthetic camera groups
static std::pair<std::map<std::string, Json::Value, std::less<>>, std::map<std::string, Json::Value, std::less<>>>
loadCalibSyntheticCameraGroupsJson(const std::string &calibFilePath, bool recentering)
{
    try
    {
        // Open and parse the JSON file
        std::ifstream file(calibFilePath);
        if (!file.is_open())
        {
            LOG(error) << "Failed to open file: " << calibFilePath << endl;
            return {{}, {}};
        }

        Json::Value calibJson;
        Json::Reader reader;
        if (!reader.parse(file, calibJson))
        {
            LOG(error) << "Failed to parse calibration data from file" << endl;
            return {{}, {}};
        }

        if (!calibJson.isMember("sensors") || !calibJson["sensors"].isArray())
        {
            LOG(error) << "Invalid calibration data format: missing sensors array" << endl;
            return {{}, {}};
        }

        // Initialize dictionaries to store calibration and group area data
        std::map<std::string, Json::Value, std::less<>> calibDict;
        std::map<std::string, Json::Value, std::less<>> groupAreaDict;
        float translationToGlobalCoordinatesX = 0.0f;
        float translationToGlobalCoordinatesY = 0.0f;
        float scaleFactor = 1.0f;

        // Process each sensor in the JSON
        for (const auto &sensor : calibJson["sensors"])
        {
            if (!sensor.isMember("id") || !sensor["id"].isString())
            {
                continue;
            }

            std::string camName = sensor["id"].asString();

            // Get BEV group information
            if (!sensor.isMember("group"))
            {
                LOG(error) << "Sensor " << camName << " does not have a 'group' field" << endl;
                continue;
            }

            auto [bevGroupName, bevGroupInfoDict] = loadGroup(sensor["group"], "bev");

            if (bevGroupName.empty())
            {
                continue;
            }

            // Initialize the dictionaries for this BEV group if not already done
            if (calibDict.find(bevGroupName) == calibDict.end())
            {
                calibDict[bevGroupName] = Json::Value(Json::objectValue);

                // Convert std::map to Json::Value for group area info
                Json::Value groupAreaInfo(Json::objectValue);
                for (const auto& [key, value] : bevGroupInfoDict)
                {
                    Json::Value arrayValue(Json::arrayValue);
                    for (const auto& v : value)
                    {
                        arrayValue.append(v);
                    }
                    groupAreaInfo[key] = arrayValue;
                }
                groupAreaDict[bevGroupName] = groupAreaInfo;
            }

            try {
                // Process matrices similar to loadCalibSyntheticJson
                if (!sensor.isMember("intrinsicMatrix") || !sensor["intrinsicMatrix"].isArray() ||
                    !sensor.isMember("extrinsicMatrix") || !sensor["extrinsicMatrix"].isArray())
                {
                    LOG(error) << "Missing required matrices for sensor " << camName << endl;
                    continue;
                }

                // Convert intrinsic matrix (3x3)
                std::vector<float> intrinsicFlat;
                const Json::Value& intrinsicMatrix = sensor["intrinsicMatrix"];
                if (!intrinsicMatrix.isArray() || intrinsicMatrix.size() != 3)
                {
                    throw std::runtime_error("Invalid intrinsic matrix format - expected 3x3 array");
                }
                for (const auto& row : intrinsicMatrix)
                {
                    if (!row.isArray() || row.size() != 3)
                    {
                        throw std::runtime_error("Invalid intrinsic matrix row format - expected 3 values");
                    }
                    for (const auto& val : row)
                    {
                        if (!val.isConvertibleTo(Json::ValueType::realValue))
                        {
                            throw std::runtime_error("Intrinsic matrix contains non-numeric values");
                        }
                        intrinsicFlat.push_back(val.asFloat());
                    }
                }

                // Convert extrinsic matrix (3x4)
                std::vector<float> extrinsicFlat;
                const Json::Value& extrinsicMatrix = sensor["extrinsicMatrix"];
                if (!extrinsicMatrix.isArray() || extrinsicMatrix.size() != 3)
                {
                    throw std::runtime_error("Invalid extrinsic matrix format - expected 3x4 array");
                }
                for (const auto& row : extrinsicMatrix)
                {
                    if (!row.isArray() || row.size() != 4)
                    {
                        throw std::runtime_error("Invalid extrinsic matrix row format - expected 4 values");
                    }
                    for (const auto& val : row)
                    {
                        if (!val.isConvertibleTo(Json::ValueType::realValue))
                        {
                            throw std::runtime_error("Extrinsic matrix contains non-numeric values");
                        }
                        extrinsicFlat.push_back(val.asFloat());
                    }
                }

                // Parse translationToGlobalCoordinates
                if (sensor.isMember("translationToGlobalCoordinates") &&
                    sensor["translationToGlobalCoordinates"].isObject())
                {
                    if (sensor["translationToGlobalCoordinates"].isMember("x") &&
                        sensor["translationToGlobalCoordinates"]["x"].isNumeric())
                    {
                        translationToGlobalCoordinatesX = sensor["translationToGlobalCoordinates"]["x"].asFloat();
                    }
                    if (sensor["translationToGlobalCoordinates"].isMember("y") &&
                        sensor["translationToGlobalCoordinates"]["y"].isNumeric())
                    {
                        translationToGlobalCoordinatesY = sensor["translationToGlobalCoordinates"]["y"].asFloat();
                    }
                }

                // Parse scaleFactor
                if (sensor.isMember("scaleFactor") && sensor["scaleFactor"].isNumeric())
                {
                    scaleFactor = sensor["scaleFactor"].asFloat();
                }

                // Create and populate OpenCV matrices
                cv::Mat intrinExt = cv::Mat::eye(4, 4, CV_32F);
                cv::Mat intrinsicMat(3, 3, CV_32F, intrinsicFlat.data());
                intrinsicMat.copyTo(intrinExt(cv::Rect(0, 0, 3, 3)));

                cv::Mat extrin = cv::Mat::eye(4, 4, CV_32F);
                cv::Mat extrinsicMat(3, 4, CV_32F, extrinsicFlat.data());
                extrinsicMat.copyTo(extrin(cv::Rect(0, 0, 4, 3)));

                cv::Mat projW2C = extrin.clone();
                if (recentering && !groupAreaDict.empty() && groupAreaDict[bevGroupName].isMember("origin"))
                {
                    // Apply recentering if group area information exists
                    const auto& origin = groupAreaDict[bevGroupName]["origin"];
                    if (origin.size() >= 2)
                    {
                        // Convert to camera-to-world, apply translation, then back to world-to-camera
                        cv::Mat projC2W = projW2C.inv();
                        projC2W.at<float>(0, 3) -= origin[0].asFloat();  // X coordinate
                        projC2W.at<float>(1, 3) -= origin[1].asFloat();  // Y coordinate
                        projW2C = projC2W.inv();
                    }
                }

                // Calculate world-to-pixel projection matrix
                cv::Mat projW2P = intrinExt * projW2C;

                // Store the matrices in the calibration dictionary
                Json::Value calibDictCam(Json::objectValue);
                calibDictCam["intrinsic matrix"] = matrixToJson(intrinExt(cv::Rect(0, 0, 3, 3)));
                calibDictCam["projection matrix w2c"] = matrixToJson(projW2C);
                calibDictCam["projection matrix w2p"] = matrixToJson(projW2P);
                calibDictCam["translationToGlobalCoordinates"] = Json::Value(Json::objectValue);
                calibDictCam["translationToGlobalCoordinates"]["x"] = translationToGlobalCoordinatesX;
                calibDictCam["translationToGlobalCoordinates"]["y"] = translationToGlobalCoordinatesY;
                calibDictCam["scaleFactor"] = scaleFactor;

                calibDict[bevGroupName][camName] = calibDictCam;
            }
            catch (const std::exception &e)
            {
                LOG(error) << "Error processing matrices for sensor " << camName << ": " << e.what() << endl;
                continue;  // Skip this sensor and try the next one
            }
        }
        return {calibDict, groupAreaDict};
    }
    catch (const std::exception &e)
    {
        LOG(error) << "Error loading calibration data: " << e.what() << endl;
        return {{}, {}};
    }
}

// Load camera calibration data from file
static std::pair<Json::Value, Json::Value> loadCalib(const std::string &calibFilePath, const std::string &calibMode, bool useCameraGroups, bool recentering)
{
    Json::Value groupAreaDict(Json::nullValue);
    Json::Value calibDict(Json::objectValue);

    try {
        if (calibMode == "synthetic")
        {
            if (useCameraGroups)
            {
                auto [calib, groupArea] = loadCalibSyntheticCameraGroupsJson(calibFilePath, recentering);

                // Convert from map to Json::Value
                for (const auto &[groupName, groupData] : calib)
                {
                    calibDict[groupName] = groupData;
                }

                // Convert from map to Json::Value
                groupAreaDict = Json::Value(Json::objectValue);
                for (const auto &[groupName, groupData] : groupArea)
                {
                    groupAreaDict[groupName] = groupData;
                }
            }
            else
            {
                calibDict = loadCalibSyntheticJson(calibFilePath, recentering);
            }
        }
        else
        {
            calibDict = loadCalibBEVFormerScene(calibFilePath);
        }

        if (calibDict.empty())
        {
            LOG(error) << "Warning: Calibration dictionary is empty after loading " << calibFilePath << endl;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Error in loadCalib: " << e.what() << endl;
    }

    return {calibDict, groupAreaDict};
}

void NvLLOverlayInternal::readCalibrationData()
{
    string filepath = GET_CONFIG().calibration_file_path;
    string calibMode = GET_CONFIG().calibration_mode;
    bool useCameraGroups = GET_CONFIG().use_camera_groups;
    bool recentering = GET_CONFIG().enable_recentering;

    LOG(info) << "Loading calibration data from file: " << filepath << endl;
    LOG(info) << "Calibration mode: " << calibMode << endl;
    LOG(info) << "Use camera groups: " << useCameraGroups << endl;
    LOG(info) << "Recentering: " << recentering << endl;

    {
        std::lock_guard<std::mutex> guard_id(m_idLock);
        std::lock_guard<std::mutex> guard_classType(m_classTypeLock);
        if (find(m_idList[BBOX].begin(), m_idList[BBOX].end(), "none") != m_idList[BBOX].end() &&
            find(m_classTypeList.begin(), m_classTypeList.end(), "none") != m_classTypeList.end())
        {
            return;
        }
    }

    try 
    {
        // Load calibration data using the new loadCalib function
        auto [calibDict, groupAreaDict] = loadCalib(filepath, calibMode, useCameraGroups, recentering);

        if (calibDict.empty()) 
        {
            LOG(error) << "Failed to load calibration data from file: " << filepath << endl;
            return;
        }

        // Process calibration data based on camera groups
        if (useCameraGroups)
        {
            // Handle grouped camera calibration data
            Json::Value::Members groupNames = calibDict.getMemberNames();
            for (const auto& groupName : groupNames)
            {
                Json::Value::Members camIds = calibDict[groupName].getMemberNames();
                for (const auto& camId : camIds)
                {
                    const Json::Value& camData = calibDict[groupName][camId];
                    CalibrationData data;
                    data.name = camId;

                    // Parse intrinsic matrix
                    if (camData.isMember("intrinsic matrix"))
                    {
                        const Json::Value& intrinsic = camData["intrinsic matrix"];
                        for (const auto& row : intrinsic)
                        {
                            std::vector<float> matrixRow;
                            for (Json::Value::ArrayIndex i = 0; i < row.size(); ++i)
                            {
                                matrixRow.push_back(row[i].asFloat());
                            }
                            data.intrinsicMatrix.push_back(matrixRow);
                        }
                    }

                    // Parse projection matrix w2c (world to camera)
                    if (camData.isMember("projection matrix w2c"))
                    {
                        const Json::Value& w2c = camData["projection matrix w2c"];
                        for (const auto& row : w2c)
                        {
                            std::vector<float> matrixRow;
                            for (Json::Value::ArrayIndex i = 0; i < row.size(); ++i)
                            {
                                data.proj_w2c_matrix.push_back(row[i].asFloat());
                            }
                        }
                    }

                    if (camData.isMember("projection matrix w2p"))
                    {
                        const Json::Value& w2p = camData["projection matrix w2p"];
                        for (const auto& row : w2p)
                        {
                            std::vector<float> matrixRow;
                            for (Json::Value::ArrayIndex i = 0; i < row.size(); ++i)
                            {
                                data.proj_w2p_matrix.push_back(row[i].asFloat());
                            }
                        }
                    }

                    if (camData.isMember("translationToGlobalCoordinates"))
                    {
                        const Json::Value& translationToGlobalCoordinates = camData["translationToGlobalCoordinates"];
                        data.translationToGlobalCoordinates.x = translationToGlobalCoordinates["x"].asFloat();
                        data.translationToGlobalCoordinates.y = translationToGlobalCoordinates["y"].asFloat();
                    }

                    data.scaleFactor = 1.0f;
                    if (camData.isMember("scaleFactor"))
                    {
                        data.scaleFactor = camData["scaleFactor"].asFloat();
                    }

                    LOG(info) << "Calibration data for " << camId << " found" << endl;
                    m_calibrationData[camId] = data;
                }
            }
        }
        else
        {
            // Handle non-grouped camera calibration data
            Json::Value::Members camIds = calibDict.getMemberNames();
            for (const auto& camId : camIds)
            {
                const Json::Value& camData = calibDict[camId];
                CalibrationData data;
                data.name = camId;

                // Parse intrinsic matrix
                if (camData.isMember("intrinsic matrix"))
                {
                    const Json::Value& intrinsic = camData["intrinsic matrix"];
                    for (const auto& row : intrinsic)
                    {
                        std::vector<float> matrixRow;
                        for (Json::Value::ArrayIndex i = 0; i < row.size(); ++i)
                        {
                            matrixRow.push_back(row[i].asFloat());
                        }
                        data.intrinsicMatrix.push_back(matrixRow);
                    }
                }

                // Parse projection matrix w2c (world to camera)
                if (camData.isMember("projection matrix w2c"))
                {
                    const Json::Value& w2c = camData["projection matrix w2c"];
                    for (const auto& row : w2c)
                    {
                        std::vector<float> matrixRow;
                        for (Json::Value::ArrayIndex i = 0; i < row.size(); ++i)
                        {
                            data.proj_w2c_matrix.push_back(row[i].asFloat());
                        }
                    }
                }
                // Parse translationToGlobalCoordinates
                if (camData.isMember("translationToGlobalCoordinates"))
                {
                    const Json::Value& translationToGlobalCoordinates = camData["translationToGlobalCoordinates"];
                    data.translationToGlobalCoordinates.x = translationToGlobalCoordinates["x"].asFloat();
                    data.translationToGlobalCoordinates.y = translationToGlobalCoordinates["y"].asFloat();
                }

                data.scaleFactor = 1.0f;
                if (camData.isMember("scaleFactor"))
                {
                    data.scaleFactor = camData["scaleFactor"].asFloat();
                }

                LOG(info) << "Calibration data for " << camId << " found" << endl;
                m_calibrationData[camId] = data;
            }
        }

        // Add map calibration data if needed
        if (!m_calibrationData.empty())
        {
            auto firstCam = m_calibrationData.begin()->second;
            firstCam.name = "map";
            m_calibrationData["map"] = firstCam;
        }
    }
    catch (const std::exception& e)
    {
        LOG(error) << "Error processing calibration data: " << e.what() << endl;
    }
}

bool NvLLOverlayInternal::processOsdSinkPadBufferProbe (void* buffer, GstMetaUnion *union_meta, int64_t pts)
{
    GstNvIpcMeta* ipc_meta = nullptr;
    GstNvVstMeta* vst_meta = nullptr;
    void* meta = nullptr;
    if (GET_CONFIG().enable_ipc_path && m_enableBbox)
    {
        meta = ipc_meta = union_meta->ipcMeta;
    }
    else
    {
        meta = vst_meta = union_meta->vstMeta;
    }

#ifdef USE_CUOSD
    void *ip_buffer = buffer;
    if (GET_OSD_INSTANCE()->isError())
    {
        LOG(info) << "Could not get libs" << endl;
        return false;
    }
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    // Running in CPU mode
    if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
    {
        if (!m_cpuCtx)
        {
            m_cpuCtx = new OsdCpuDataContext();
        }
        m_cpuCtx->width = m_width;
        m_cpuCtx->height = m_height;
        m_cpuCtx->data = &buffer;
        m_cpuCtx->size = (m_width * m_height * 3) / 2;
        ip_buffer = (OsdCpuDataContext *)m_cpuCtx;
    }
#endif
    string frameTimestamp;
    int64_t frameTS = 0;
    int64_t stored_frame_TS = 0;
    if (meta && GET_CONFIG().enable_ipc_path == false)
    {
        /* ms for live playback from onFrame */
        frameTS = ((GstNvVstMeta*)meta)->pts;
        LOG(verbose) << "Overlay frame timestamp: " << frameTS << endl;
    }
    else
    {
        /* ns for recorded playback */
        frameTS = pts;
    }

    /* Store the TS, as the conversion to us will result is 0 */
    stored_frame_TS = frameTS;

    if (frameTS == 0)
    {
        struct timeval tv;
        gettimeofday(&tv, nullptr);
        frameTS = (tv.tv_sec * 1000000000) + (tv.tv_usec * 1000);

        //return true;
    }

    /* Convert it into micro second */
    frameTS = getTimestampInMicroSecond(frameTS);

    /* PICTURE API and DOWNLOAD API */
    if (GET_CONFIG().enable_mega_simulation && frameTS == 0)
    {
        frameTS = stored_frame_TS / 1000;
    }

    if (m_bboxParams.m_overlay.m_bboxDebug)
    {
        frameTimestamp = convertEpocToISO8601_2(frameTS);
    }

    if (GET_CONFIG().enable_gem_drawing)
    {
        if (m_enableTripwire)
        {
            drawTripwire(m_isGst ? (GstBuffer *) buffer : nullptr);
        }
        if (m_enableRoi)
        {
            drawRoi(m_isGst ? (GstBuffer *) buffer : nullptr);
        }
    }

    if (GET_CONFIG().enable_ipc_path == true && m_enableBbox)
    {
        if (meta)
        {
            for (uint32_t i = 0; i < ((GstNvIpcMeta*)meta)->num_rects; i++)
            {
                Point left_top = {}, right_bottom = {};

                /* Assign bounding box coordinates */
                OSD_RectParams* rect_params = (OSD_RectParams*)malloc(sizeof(OSD_RectParams));

                if (rect_params)
                {
                    left_top     = interpolateCoordinate(((GstNvIpcMeta*)meta)->rect_params[i].left, ((GstNvIpcMeta*)meta)->rect_params[i].top, m_ipcSourceWidth, m_ipcSourceHeight, m_width, m_height);
                    right_bottom = interpolateCoordinate(((GstNvIpcMeta*)meta)->rect_params[i].left + ((GstNvIpcMeta*)meta)->rect_params[i].width, ((GstNvIpcMeta*)meta)->rect_params[i].top + ((GstNvIpcMeta*)meta)->rect_params[i].height,
                                                         m_ipcSourceWidth, m_ipcSourceHeight, m_width, m_height);
                }

                rect_params->left    = left_top.x;
                rect_params->top     = left_top.y;
                rect_params->right   = right_bottom.x;
                rect_params->bottom  = right_bottom.y;

                OSD_ColorParams bg_color = {0, 0, 0, 0};
                rect_params->bg_color = bg_color;

                /* Set border width */
                rect_params->thickness = m_bboxParams.m_overlay.m_bboxThickness;

                OSD_ColorParams border_color = OSD_COLOR_RED;
                if      (m_bboxParams.m_overlay.m_bboxColor == "green" ) border_color = OSD_COLOR_GREEN;
                else if (m_bboxParams.m_overlay.m_bboxColor == "blue"  ) border_color = OSD_COLOR_BLUE;
                else if (m_bboxParams.m_overlay.m_bboxColor == "black" ) border_color = OSD_COLOR_BLACK;
                else if (m_bboxParams.m_overlay.m_bboxColor == "white" ) border_color = OSD_COLOR_WHITE;
                else if (m_bboxParams.m_overlay.m_bboxColor == "yellow") border_color = OSD_COLOR_YELLOW;
                else border_color = OSD_COLOR_RED;
                rect_params->border_color = border_color;
                rect_params->border_color.alpha = m_bboxParams.m_overlay.m_bboxOpacity;

                OsdMeta meta;
                meta.meta_type = OSD_RECTANGLE;
                meta.params = (void *)rect_params;
                GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
            }
        }
    }
    if (m_isWaitForESQuery && m_replayMetadataStore)
    {
        m_replayMetadataStore->waitForMetadata();
    }

    if (m_enableSensorNameText)
    {
        OSD_TextParams* text_params=(OSD_TextParams*)malloc(sizeof(OSD_TextParams));
        if (text_params != nullptr)
        {
            char* cstr = (char*)malloc(m_sensorName.size() + 1);
            if (cstr != nullptr)
            {
                strcpy(cstr, m_sensorName.c_str());
                text_params->text = cstr;
            }

            /* Consumer is compositor, scale the font size according to the height of video */
            text_params->font_size = ((float)m_height / (float)HEIGHT_1080p) * DEFAULT_FONT_SIZE;
            text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());
            /* Now set the offsets where the string should appear */
            text_params->pos_x = std::min(m_sensorNameTextPosX, (int)(m_bboxParams.m_frameSize.m_width - m_sensorName.size()));
            text_params->pos_y = std::min(m_sensorNameTextPosY, (int)(m_bboxParams.m_frameSize.m_height - text_params->font_size));

            /* Text background color */
            text_params->border_color=(OSD_ColorParams){255,255,255,255};
            text_params->bg_color=(OSD_ColorParams){77,77,77,200};

            if (m_isGst)
            {
                GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta((GstBuffer *)buffer, OSD_TEXT, text_params);
            }
            else
            {
                OsdMeta meta;
                meta.meta_type = OSD_TEXT;
                meta.params = (void *)text_params;
                GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
            }
        }
    }

    Json::Value metadata = getMetadata(frameTS);
    if (metadata == Json::nullValue)
    {
        if (m_bboxParams.m_overlay.m_bboxDebug)
        {
#ifdef USE_CUOSD
            OSD_TextParams* text_params=(OSD_TextParams*)malloc(sizeof(OSD_TextParams));
            if (text_params != nullptr)
            {
                string text = getUTCtoLocalISOTime(frameTS);
                char* cstr = (char*)malloc(text.size() + 1);
                if (cstr != nullptr)
                {
                    strcpy(cstr, text.c_str());
                    text_params->text = cstr;
                }

                /* Now set the offsets where the string should appear */
                text_params->pos_x = std::min(10, m_width);
                text_params->pos_y = std::min(10, m_height);
                text_params->font_size = interpolateFontSize(m_sourceWidth, m_width);
                text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

                /* Text background color */
                text_params->border_color=(OSD_ColorParams){255,255,255,255};
                text_params->bg_color=(OSD_ColorParams){77,77,77,200};

                if (m_isGst)
                {
                    GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta((GstBuffer *)buffer, OSD_TEXT, text_params);
                }
                else
                {
                    OsdMeta meta;
                    meta.meta_type = OSD_TEXT;
                    meta.params = (void *)text_params;
                    GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
                }
            }
            if (!m_isGst)
            {
                GET_OSD_INSTANCE()->osd_draw((OsdContext_t)osd_ctx, ip_buffer);
            }
            return true;
#endif
        }
        if (m_replayMetadataStore && m_replayMetadataStore->isSearching())
        {
            if (!m_isGst)
            {
                GET_OSD_INSTANCE()->osd_draw((OsdContext_t)osd_ctx, ip_buffer);
            }
            LOG(warning) << "Return from here" << endl;
            return true;
        }
        if (!GET_CONFIG().enable_gem_drawing)
        {
            LOG(info) << "Metadata empty, removing probe" << endl;
            return false;
        }
        else
        {
            if (!m_isGst)
            {
                GET_OSD_INSTANCE()->osd_draw((OsdContext_t)osd_ctx, ip_buffer);
            }
            return true;
        }
    }

    if (metadata == Json::nullValue)
    {
        if (!m_isGst)
        {
            GET_OSD_INSTANCE()->osd_draw((OsdContext_t)osd_ctx, ip_buffer);
        }
        return true;
    }

    uint32_t tolerance = m_bboxParams.m_timestampTolerance;
    int64_t elasticTS;

    elasticTS = metadata["epocTime"].asUInt64() * 1000;
    string match_check ="", elasticTimestamp = "";
    if (m_bboxParams.m_overlay.m_bboxDebug)
    {
        elasticTimestamp = convertEpocToISO8601_2(elasticTS);
    }

    bool skip_frame = GET_CONFIG().enable_overlay_skip_frame;
    int num_objects = 0;
    if (m_bboxParams.m_overlay.m_enableGodsEyeView ||
        elasticTS == frameTS || (abs(elasticTS - frameTS) < tolerance)) // Remove tolerance part for exact matching
    {
        Json::Value objects = metadata["objects"];
        if (m_bboxParams.m_overlay.m_bboxDebug)
        {
            std::lock_guard<std::mutex> lock(m_debugData);
            match_check = MATCH;
            num_objects = objects.size();
            m_bboxParams.m_numObjects.push_back(make_tuple(frameTimestamp, elasticTimestamp, num_objects));
            if (abs(elasticTS - frameTS) > MIN_TOLERANCE)
            {
                m_bboxParams.m_shifts.push_back(make_pair(frameTimestamp, elasticTimestamp));
            }
        }
#ifdef USE_CUOSD
        {
            std::lock_guard<std::mutex> guard(m_idLock);
            draw_bbox_cuosd(objects, &m_bboxParams, m_idList[BBOX], m_classTypeList, (OsdContext_t)osd_ctx, m_isGst ? (GstBuffer *) buffer : nullptr);
        }
#endif
    }
    else if (frameTS > elasticTS)   // discard meta data of frames passed
    {
        if (m_bboxParams.m_overlay.m_bboxDebug)
        {
            match_check = INCREMENT;
        }
        while((frameTS > elasticTS) && metadata != Json::nullValue)
        {
            metadata = getMetadata(frameTS);
            if (metadata == Json::nullValue)
            {
                break;
            }
            elasticTS = metadata["epocTime"].asUInt64() * 1000;
        }
        // Re-check if match occurs, otherwise we might miss drawing on a frame.
        if (elasticTS == frameTS || (abs(elasticTS - frameTS) < tolerance)) // Remove tolerance part for exact matching
        {
            Json::Value objects = metadata["objects"];
            if (m_bboxParams.m_overlay.m_bboxDebug)
            {
                std::lock_guard<std::mutex> lock(m_debugData);
                elasticTimestamp = convertEpocToISO8601_2(elasticTS);
                match_check = MATCH;
                num_objects = objects.size();
                m_bboxParams.m_numObjects.push_back(make_tuple(frameTimestamp, elasticTimestamp, num_objects));
                if (abs(elasticTS - frameTS) > MIN_TOLERANCE)
                {
                    m_bboxParams.m_shifts.push_back(make_pair(frameTimestamp, elasticTimestamp));
                }
            }
#ifdef USE_CUOSD
            {
                std::lock_guard<std::mutex> guard(m_idLock);
                draw_bbox_cuosd(objects, &m_bboxParams, m_idList[BBOX], m_classTypeList, (OsdContext_t)osd_ctx, m_isGst ? (GstBuffer *) buffer : nullptr);
            }
#endif
        }
        if (skip_frame)
        {
            return false;
        }
    }
    else
    {
        if (skip_frame)
        {
            return false;
        }
        if (m_bboxParams.m_overlay.m_bboxDebug)
        {
            std::lock_guard<std::mutex> lock(m_debugData);
            match_check = UNMATCH;
            m_bboxParams.m_mismatches.push_back(make_pair(frameTimestamp, elasticTimestamp));
        }
    }

#ifdef USE_CUOSD
    if (m_bboxParams.m_overlay.m_bboxDebug)
    {
        OSD_TextParams* text_params=(OSD_TextParams*)malloc(sizeof(OSD_TextParams));
        int font_size = interpolateFontSize(m_sourceWidth, m_width);
        if (text_params != nullptr)
        {
            string text = frameTimestamp + "   " + elasticTimestamp + "  " + match_check;
            if (match_check == MATCH)
            {
                text = text + "  #objects: " + to_string(num_objects);
            }
            char* cstr = (char*)malloc(text.size() + 1);
            if (cstr != nullptr)
            {
                strcpy(cstr, text.c_str());
                text_params->text = cstr;
            }

            /* Now set the offsets where the string should appear */
            text_params->pos_x = std::min(10, m_width);
            text_params->pos_y = std::min(900, m_height-100);
            text_params->font_size = font_size;
            text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

            /* Text background color */
            text_params->border_color=(OSD_ColorParams){255,255,255,255};
            text_params->bg_color=(OSD_ColorParams){77,77,77,200};

            if (m_isGst)
            {
                GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta((GstBuffer *)buffer, OSD_TEXT, text_params);
            }
            else
            {
                OsdMeta meta;
                meta.meta_type = OSD_TEXT;
                meta.params = (void *)text_params;
                GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
            }
        }
        // Display latency of the frame;
        if (m_bboxParams.m_isLive)
        {
            OSD_TextParams* text_params_latency = (OSD_TextParams*)malloc(sizeof(OSD_TextParams));
            if (text_params_latency != nullptr)
            {
                int64_t current_time_ns = std::chrono::duration_cast<std::chrono::nanoseconds>(std::chrono::system_clock::now().time_since_epoch()).count();
                string text = "Latency: " + to_string((current_time_ns - stored_frame_TS) / 1000000) + "ms";
                char* cstr = (char*)malloc(text.size() + 1);
                if (cstr != nullptr)
                {
                    strcpy(cstr, text.c_str());
                    text_params_latency->text = cstr;
                }

                /* Now set the offsets where the string should appear */
                text_params_latency->pos_x = std::min(10, m_width);
                text_params_latency->pos_y = std::min(900 + (3 * font_size), m_height - 100 + (3 * font_size));
                text_params_latency->font_size = font_size;
                text_params_latency->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

                /* Text background color */
                text_params_latency->border_color=(OSD_ColorParams){255,255,255,255};
                text_params_latency->bg_color=(OSD_ColorParams){77,77,77,200};

                if (m_isGst)
                {
                    GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta((GstBuffer *)buffer, OSD_TEXT, text_params_latency);
                }
                else
                {
                    OsdMeta meta;
                    meta.meta_type = OSD_TEXT;
                    meta.params = (void *)text_params_latency;
                    GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
                }
            }
        }
    }
#endif
#endif
    if (!m_isGst)
    {
        GET_OSD_INSTANCE()->osd_draw((OsdContext_t)osd_ctx, ip_buffer);
    }
    return true;
}

Json::Value NvLLOverlayInternal::getMetadata(int64_t frameTS)
{
    Json::Value metadata = Json::nullValue;
    if (m_metadataStore)
    {
        metadata = m_metadataStore->getMetadata(frameTS);
    }
    return metadata;
}

bool NvLLOverlayInternal::processOsdSinkPadBufferProbeStreamer (void* buffer, GstNvVstMeta *meta)
{
#ifdef USE_CUOSD
    void *ip_buffer = buffer;
    if (GET_OSD_INSTANCE()->isError())
    {
        LOG(info) << "Could not get libs" << endl;
        return false;
    }
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    // Running in CPU mode
    if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
    {
        if (!m_cpuCtx)
        {
            m_cpuCtx = new OsdCpuDataContext();
        }
        m_cpuCtx->width = m_width;
        m_cpuCtx->height = m_height;
        m_cpuCtx->data = &buffer;
        m_cpuCtx->size = (m_width * m_height * 3) / 2;
        ip_buffer = (OsdCpuDataContext *)m_cpuCtx;
    }
#endif

    int64_t frameTS = 0;
    if (meta)
    {
        frameTS = meta->id;
    }
    if (frameTS == 0)
    {
        return true;
    }

    if (m_bboxParams.m_overlay.m_bboxDebug)
    {
#ifdef USE_CUOSD
        OSD_TextParams* text_params=(OSD_TextParams*)malloc(sizeof(OSD_TextParams));
        if (text_params != nullptr)
        {
            string time_str = getRelativeTimeUsingFrameId(frameTS, m_bboxParams.m_frameRate);
            string text = "id:" + to_string(frameTS) + string(" ts: ") + time_str ;
            char* cstr = (char*)malloc(text.size() + 1);
            if (cstr != nullptr)
            {
                strcpy(cstr, text.c_str());
                text_params->text = cstr;
            }

            /* Now set the offsets where the string should appear */
            text_params->pos_x = std::min(10, m_width);
            text_params->pos_y = std::min(10, m_height);
            text_params->font_size = interpolateFontSize(m_sourceWidth, m_width);
            text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

            /* Text background color */
            text_params->border_color=(OSD_ColorParams){255,255,255,255};
            text_params->bg_color=(OSD_ColorParams){77,77,77,200};

            if (m_isGst)
            {
                GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta((GstBuffer *)buffer, OSD_TEXT, text_params);
            }
            else
            {
                OsdMeta meta;
                meta.meta_type = OSD_TEXT;
                meta.params = (void *)text_params;
                GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
            }
        }
#endif
    }

    Json::Value metadata = getMetadata(frameTS);
    if (metadata == Json::nullValue)
    {
        if ((m_replayMetadataStore && m_replayMetadataStore->isSearching())
            || m_bboxParams.m_overlay.m_bboxDebug)
        {
            if (!m_isGst)
            {
                GET_OSD_INSTANCE()->osd_draw((OsdContext_t)osd_ctx, ip_buffer);
            }
            return true;
        }
        LOG(warning) << "Metadata empty, removing probe" << endl;
        return false;
    }

    if (metadata == Json::nullValue)
    {
        LOG(warning) << "Metadata empty, Skipping the frame" << endl;
        if (!m_isGst)
        {
            GET_OSD_INSTANCE()->osd_draw((OsdContext_t)osd_ctx, ip_buffer);
        }
        return true;
    }

#ifndef EMBED_FRAMEID
#endif
    int64_t elasticFrameId = stringToInt(metadata.get("id", EMPTY_STRING).asString());

    int num_objects = 0;
    string match_check ="";
    if (elasticFrameId == frameTS)
    {
        LOG(verbose) << "Timestamp MATCH: " << frameTS << " AND " << elasticFrameId << endl;
        Json::Value objects = metadata.get("objects", EMPTY_STRING);

        if (m_bboxParams.m_overlay.m_bboxDebug)
        {
            match_check = MATCH;
            num_objects = objects.size();
        }

#ifdef USE_CUOSD
#ifndef EMBED_FRAMEID
        {
            std::lock_guard<std::mutex> guard(m_idLock);
            draw_bbox_cuosd(objects, &m_bboxParams, m_idList[BBOX], m_classTypeList, (OsdContext_t)osd_ctx, m_isGst ? (GstBuffer *) buffer : nullptr);
        }
#endif
#endif
    }
    else if (frameTS > elasticFrameId)   // discard meta data of frames passed
    {
        if (m_bboxParams.m_overlay.m_bboxDebug)
        {
            match_check = INCREMENT;
        }
        LOG(verbose) << "Looping to match timestamps:  " << frameTS << " and " << elasticFrameId << endl;
        while((frameTS > elasticFrameId) && metadata != Json::nullValue)
        {
            metadata = getMetadata(frameTS);
            if (metadata == Json::nullValue)
            {
                break;
            }
            elasticFrameId = stringToInt(metadata.get("id", EMPTY_STRING).asString());
        }

        // Re-check if match occurs, otherwise we might miss drawing on a frame.
        if (elasticFrameId == frameTS) // Remove tolerance part for exact matching
        {
            LOG(verbose) << "Timestamp MATCH: " << frameTS << " AND " << elasticFrameId << endl;
            Json::Value objects = metadata["objects"];
            {
                match_check = MATCH;
                num_objects = objects.size();
            }
#ifdef USE_CUOSD
#ifndef EMBED_FRAMEID
            {
                std::lock_guard<std::mutex> guard(m_idLock);
                draw_bbox_cuosd(objects, &m_bboxParams, m_idList[BBOX], m_classTypeList, (OsdContext_t)osd_ctx, m_isGst ? (GstBuffer *) buffer : nullptr);
            }
#endif
#endif
        }
    }
    else
    {
        if (m_bboxParams.m_overlay.m_bboxDebug)
        {
            match_check = UNMATCH;
        }
        LOG(verbose) << "Timestamp UNMATCHED: " << frameTS << " AND " << elasticFrameId << endl;
    }

    if (m_bboxParams.m_overlay.m_bboxDebug)
    {
#ifdef USE_CUOSD
        OSD_TextParams* text_params=(OSD_TextParams*)malloc(sizeof(OSD_TextParams));
        if (text_params != nullptr)
        {
            string text = to_string(frameTS) + "   " + to_string(elasticFrameId) + "  " + match_check;
            if (match_check == MATCH)
            {
                text = text + "  #objects: " + to_string(num_objects);
            }
            char* cstr = (char*)malloc(text.size() + 1);
            if (cstr != nullptr)
            {
                strcpy(cstr, text.c_str());
                text_params->text = cstr;
            }

            /* Now set the offsets where the string should appe ar */
            text_params->pos_x = std::min(10, m_width);
            text_params->pos_y = std::min(900, m_height-100);
            text_params->font_size = interpolateFontSize(m_sourceWidth, m_width);
            text_params->font_type = strdup(GET_CONFIG().overlay_text_font_type.c_str());

            /* Text background color */
            text_params->border_color=(OSD_ColorParams){255,255,255,255};
            text_params->bg_color=(OSD_ColorParams){77,77,77,200};

            if (m_isGst)
            {
                GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta((GstBuffer *)buffer, OSD_TEXT, text_params);
            }
            else
            {
                OsdMeta meta;
                meta.meta_type = OSD_TEXT;
                meta.params = (void *)text_params;
                GET_OSD_INSTANCE()->osd_add_metadata((OsdContext_t)osd_ctx, &meta);
            }
        }
#endif
    }
#endif
    if (!m_isGst)
    {
        GET_OSD_INSTANCE()->osd_draw((OsdContext_t)osd_ctx, ip_buffer);
    }
    return true;
}

NvOsdLibs* NvOsdLibs::_instance = nullptr;

NvOsdLibs* NvOsdLibs::getInstance()
{
    if (_instance == nullptr)
    {
        _instance = new NvOsdLibs();
    }
    return _instance;
}

void NvOsdLibs::deleteInstance()
{
    if (_instance)
    {
        delete _instance;
        _instance = nullptr;  // Reset to nullptr after deletion
    }
}

NvOsdLibs::NvOsdLibs()
    : osd_init (nullptr)
    , osd_destroy (nullptr)
    , osd_add_metadata (nullptr)
    , osd_draw (nullptr)
    , osd_global_init (nullptr)
    , osd_global_destroy (nullptr)
    , gst_buffer_add_cu_osd_meta (nullptr)
    , handle_nvCuLib (nullptr)
    , handle_nvCuosdmetaLib (nullptr)
    , error(false)
{
    const char* lib_path;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libllosd.so");
    handle_nvCuLib = dlopen(lib_path, RTLD_LAZY);
    if (!handle_nvCuLib)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libllosd.so");
        handle_nvCuLib = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libllosd.so");
    handle_nvCuLib = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!handle_nvCuLib)
    {
        LOG(error) << "Cannot open osd library: " << dlerror() << endl;
        error = true;
    }
    else
    {
        dlerror();
        osd_init = (osd_init_t) dlsym (handle_nvCuLib, "osd_init");
        DL_ERROR_EXIT
        osd_destroy = (osd_destroy_t) dlsym (handle_nvCuLib, "osd_destroy");
        DL_ERROR_EXIT
        osd_add_metadata = (osd_add_metadata_t) dlsym (handle_nvCuLib, "osd_add_metadata");
        DL_ERROR_EXIT
        osd_draw = (osd_draw_t) dlsym (handle_nvCuLib, "osd_draw");
        DL_ERROR_EXIT
        osd_global_init = (osd_global_init_t) dlsym (handle_nvCuLib, "osd_global_init");
        DL_ERROR_EXIT
        osd_global_destroy = (osd_global_destroy_t) dlsym (handle_nvCuLib, "osd_global_destroy");
        DL_ERROR_EXIT
    }
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_ARCH64, "libgstcuosdmeta.so");
    handle_nvCuosdmetaLib = dlopen(lib_path, RTLD_LAZY);
    if (!handle_nvCuosdmetaLib)
    {
        lib_path = CONCATENATE_STRINGS(RELATIVE_PREBUILT_LIBRARY_PATH_ARCH64, "libgstcuosdmeta.so");
        handle_nvCuosdmetaLib = dlopen(lib_path, RTLD_LAZY);
    }
#else
    lib_path = CONCATENATE_STRINGS(ABSOLUTE_PREBUILT_LIBRARY_PATH_X86_64, "libgstcuosdmeta.so");
    handle_nvCuosdmetaLib = dlopen(lib_path, RTLD_LAZY);
#endif
    if (!handle_nvCuosdmetaLib)
    {
        LOG(error) << "Cannot open cuosdmeta library: " << dlerror() << endl;
        error = true;
    }
    else
    {
        dlerror();
        gst_buffer_add_cu_osd_meta = (gst_buffer_add_cu_osd_meta_t) dlsym(handle_nvCuosdmetaLib, "gst_buffer_add_cu_osd_meta");

        const char *dlsym_error = dlerror();
        if (dlsym_error)
        {
            LOG(error) << "Cannot load symbol 'cuosdmeta':" << dlsym_error << endl;
            dlclose(handle_nvCuosdmetaLib);
        }
    }

    return;

close_dl:
    LOG(error) << "Cannot load symbol 'llosd'" << endl;
    error = true;
    if (handle_nvCuLib) dlclose(handle_nvCuLib);
}

NvOsdLibs::~NvOsdLibs()
{
    dlclose(handle_nvCuLib);
    dlclose(handle_nvCuosdmetaLib);
}

void NvLLOverlayInternal::updateSourceResolution(int width, int height)
{
    m_sourceWidth = width;
    m_sourceHeight = height;
}

void NvLLOverlayInternal::updateIPCStreamResolution(int width, int height)
{
    m_ipcSourceWidth  = width;
    m_ipcSourceHeight = height;
}

NvLLOverlayInternal::NvLLOverlayInternal()
{
}

void NvLLOverlayInternal::enableOverlay(OverlayParams& params, bool use_frameid, bool wait_for_es_query)
{
    /* Getting frames bbox coordinates */
    SearchParams inData(params.m_startTime, params.m_endTime,
                        params.m_sensorName);
    if (use_frameid)
    {
        inData.m_useId = true;
        inData.m_search_after = 0;
        m_useId = true;
    }

    if (wait_for_es_query)
    {
        m_isWaitForESQuery = wait_for_es_query;
    }

    m_prevEnableOverlay = m_enableBbox || m_enableTripwire || m_enableRoi ||
                          m_enableSensorNameText || m_enablePose || m_enableHalos;
    m_sensorName = params.m_sensorName;
    m_bboxParams.m_searchParams = inData;
    m_bboxParams.m_isLive = params.m_isLive;
    m_bboxParams.m_timestampTolerance = GET_CONFIG().bbox_tolerance_ms * 1000;
    if (m_bboxParams.m_timestampTolerance == 0)
    {
        m_bboxParams.m_timestampTolerance = uint((1.0 / params.m_frameRate) * 1000) * 1000;
    }
    m_bboxParams.m_frameSize = params.m_frameSize;
    m_bboxParams.m_frameRate = params.m_frameRate;
    m_bboxParams.m_overlay = params.m_bboxParams;
    m_enableBbox = params.m_bboxParams.m_enableBbox;
    m_enablePose = params.m_bboxParams.m_enablePose;
    m_enableHalos = params.m_bboxParams.m_enableHalos;
    m_enableSensorNameText = params.m_bboxParams.m_enableSensorNameText;
    m_sensorNameTextPosX = params.m_bboxParams.m_sensorNameTextPosX;
    m_sensorNameTextPosY = params.m_bboxParams.m_sensorNameTextPosY;
    if (m_bboxParams.m_overlay.m_enableGodsEyeView)
    {
        m_enableSensorNameText = false;
    }
    m_width = params.m_frameSize.m_width;
    m_height = params.m_frameSize.m_height;
    for (uint32_t i = 0; i < OVERLAYCOUNT; i++)
    {
        std::lock_guard<std::mutex> guard(m_idLock);
        m_idList[i] = params.m_bboxParams.m_overlayIdList[i];
    }
    {
        std::lock_guard<std::mutex> guard(m_classTypeLock);
        m_classTypeList = params.m_bboxParams.m_overlayClassTypeList;
    }
    if (params.m_startTime.empty())
    {
        LOG(info) << "Live overlay, skip elasticSearch query" << endl;
        return;
    }
    if (m_bboxParams.m_isLive == false)
    {
        m_replayMetadataStore = std::dynamic_pointer_cast<ReplayMetadataStore>(m_metadataStore);
        if (m_replayMetadataStore)
        {
            if (m_enableBbox || m_enablePose || m_enableHalos)
            {
                m_replayMetadataStore->fetchMetadata();
            }
        }
    }
    if (GET_CONFIG().enable_gem_drawing)
    {
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        m_enableTripwire = params.m_bboxParams.m_enableTripwire;
        m_enableRoi = params.m_bboxParams.m_enableROI;
        if (m_enableTripwire && !m_readTripwireThread.joinable())
        {
            m_tripwireExit = false;
            m_lastTripwireReadTime = 0;
            m_readTripwireThread = std::thread(&NvLLOverlayInternal::readTripwire, this);
        }
        else if (!m_enableTripwire)
        {
            m_tripwireExit = true;
            if (m_readTripwireThread.joinable())
            {
                m_readTripwireThread.join();
            }
        }
        if (m_enableRoi && !m_readRoiThread.joinable())
        {
            m_roiExit = false;
            m_lastRoiReadTime = 0;
            m_readRoiThread = std::thread(&NvLLOverlayInternal::readRoi, this);
        }
        else if (!m_enableRoi)
        {
            m_roiExit = true;
            if (m_readRoiThread.joinable())
            {
                m_readRoiThread.join();
            }
        }
#else
        m_enableTripwire = false;
        m_enableRoi = false;
        if (params.m_bboxParams.m_enableTripwire)
        {
            LOG(warning) << "Tripwire overlay feature is not enabled for x86 platform" << endl;
        }
        if (params.m_bboxParams.m_enableROI)
        {
            LOG(warning) << "ROI overlay feature is not enabled for x86 platform" << endl;
        }
#endif
    }
    if (m_enableHalos)
    {
        m_haloSafetyManager = std::make_unique<HaloSafetyManager>();
    }
    // Initialize halo safety listener
    if (m_enableHalos && !HaloSafetyCommandListener::getInstance()->isRunning())
    {
        HaloSafetyCommandListener::getInstance()->start();
    }
    if ((m_enableBbox || m_enableHalos) && m_calibrationData.empty())
    {
        readCalibrationData();
    }
    else if (!m_enableBbox)
    {
        m_calibrationData.clear();
    }
}

NvLLOverlayInternal::NvLLOverlayInternal(OverlayParams& params,
                        std::shared_ptr<IMetadataStore> metadataStore,
                        bool use_frameid, bool wait_for_es_query)
{
    m_metadataStore = metadataStore;
    bool enable_cpu_mode = GET_CONFIG().use_software_path || g_isGpuPresent == false;
    osd_ctx = GET_OSD_INSTANCE()->osd_init(enable_cpu_mode, g_gpuIndex);

    enableOverlay(params, use_frameid, wait_for_es_query);
}

NvLLOverlayInternal::~NvLLOverlayInternal()
{
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    m_metaWait.signal();
#endif
    if (GET_CONFIG().enable_gem_drawing)
    {
        m_tripwireExit = true;
        m_roiExit = true;
        m_tripwireSync.signal();
        m_roiSync.signal();
        if (m_readTripwireThread.joinable())
        {
            m_readTripwireThread.join();
        }
        if (m_readRoiThread.joinable())
        {
            m_readRoiThread.join();
        }

        std::map<string, Tripwire>::iterator it = m_tripwireList.begin();
        for ( ; it != m_tripwireList.end(); it++)
        {
            Tripwire tripwire = it->second;
            for (uint32_t j = 0; j < MAX_LINES; j++)
            {
                if (tripwire.wires[j])
                {
                    free(tripwire.wires[j]);
                    tripwire.wires[j] = nullptr;
                }
            }
            for (uint32_t j = 0; j < MAX_POINTS; j++)
            {
                if (tripwire.endpoints[j])
                {
                    free(tripwire.endpoints[j]);
                    tripwire.endpoints[j] = nullptr;
                }
            }
            for (uint32_t j = 0; j < MAX_ARROWS; j++)
            {
                if (tripwire.direction[j])
                {
                    free(tripwire.direction[j]);
                    tripwire.direction[j] = nullptr;
                }
            }
        }
        std::map<string, Roi>::iterator it2 = m_roiList.begin();
        for ( ; it2 != m_roiList.end(); it2++)
        {
            Roi roi = it2->second;
            for (uint32_t j = 0; j < MAX_LINES; j++)
            {
                if (roi.lines[j])
                {
                    free(roi.lines[j]);
                    roi.lines[j] = nullptr;
                }
            }
            for (uint32_t j = 0; j < MAX_POINTS; j++)
            {
                if (roi.endpoints[j])
                {
                    free(roi.endpoints[j]);
                    roi.endpoints[j] = nullptr;
                }
            }
        }
        m_tripwireList.clear();
        m_roiList.clear();
    }
    if (m_elasticTask.valid())
    {
        try
        {
            m_elasticTask.get();
        }
        catch(const std::exception& e)
        {
            LOG(error) << "Caught Exception for m_elasticTask Async task: " <<  e.what() << endl;
        }
    }
    for (uint32_t i = 0; i < OVERLAYCOUNT; i++)
    {
        std::lock_guard<std::mutex> guard(m_idLock);
        m_idList[i].clear();
    }
    if (osd_ctx && GET_OSD_INSTANCE()->osd_destroy)
    {
        GET_OSD_INSTANCE()->osd_destroy((OsdContext_t)osd_ctx);
        osd_ctx = nullptr;
    }
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    if (m_cpuCtx)
    {
        delete m_cpuCtx;
        m_cpuCtx = nullptr;
    }
#endif
    m_calibrationData.clear();
    activeObjectCorners.clear();
    proximityStates.clear();
    entrantStates.clear();
}

bool NvLLOverlayInternal::isOverlayEnabled()
{
    bool is_overlay = m_enableBbox || m_enableTripwire || m_enableRoi
                     || m_enableSensorNameText || m_enablePose || m_enableHalos;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    bool is_sw_mode = GET_CONFIG().use_software_path || g_isGpuPresent == false;
    return !is_sw_mode && is_overlay;
#else
    return is_overlay;
#endif
}

GstPadProbeReturn osd_sink_pad_buffer_probe (GstPad* pad, GstPadProbeInfo* info, gpointer u_data)
{
    NvLLOverlayInternal* overlay = (NvLLOverlayInternal*)u_data;
    bool ret = true;
    GstMetaUnion meta_union;
    if (overlay && info)
    {
        GstBuffer *buffer;
        int64_t frameTS = 0;
        buffer = GST_PAD_PROBE_INFO_BUFFER (info);
        buffer = gst_buffer_make_writable (buffer);
        if (buffer == nullptr)
        {
            LOG(error) << "Cannot write buffer" << endl;
            return GST_PAD_PROBE_OK;
        }
        /* Get vst metadata of the buffer */
        GstNvVstMeta *meta;
        meta = meta_union.vstMeta = GST_NV_VST_META_GET (buffer);
        if (meta)
        {
            /* ms for live playback from onFrame */
            frameTS = meta->pts;
        }
        else
        {
            /* ns for recorded playback */
            frameTS = GST_BUFFER_PTS (buffer);
        }

        if (overlay->m_useId)
        {
            ret = overlay->processOsdSinkPadBufferProbeStreamer(buffer, meta);
        }
        ret = overlay->processOsdSinkPadBufferProbe(buffer, &meta_union, frameTS);
        return (ret == true) ? GST_PAD_PROBE_OK : GST_PAD_PROBE_REMOVE;
    }
    return GST_PAD_PROBE_REMOVE;
}


GstElement* NvLLOverlayInternal::create()
{
    LOG (info) << "Creating Gstreamer overlay pipeline" << endl;
    GstElement*  overlay_bin = nullptr;
    GstPad *osd_sink_pad = nullptr;
    GstPad *sink_pad = nullptr, *source_pad = nullptr, *ghost_sourcepad = nullptr, *ghost_sinkpad = nullptr;
    overlay_bin = gst_bin_new ("nvoverlay");
    bool isLive = false;
    GstElement* latency_queue = nullptr;
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    GstElement *converter2 = nullptr, *filter2 = nullptr, *converter1 = nullptr, *filter1 = nullptr;
#endif
    SearchParams inData = m_bboxParams.m_searchParams;
    if (inData.m_start_time.empty())
    {
        LOG(info) << "Live Overlay Playback, adding delay" << endl;
        isLive = true;
        latency_queue = gst_element_factory_make ("queue", nullptr);
        if (!latency_queue)
        {
            LOG (error) << "Gstreamer element creation failed" << endl;
            return nullptr;
        }
        gst_bin_add (GST_BIN (overlay_bin), latency_queue);
        g_object_set (G_OBJECT (latency_queue), "max-size-buffers", 0, "max-size-time", 0, "max-size-bytes", 0, "min-threshold-time", 100000000, nullptr);
    }
#ifdef USE_CUOSD
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    GstRegistry *registry;
    registry = gst_registry_get();
    gst_registry_scan_path(registry, "prebuilts/aarch64/gst-plugins");
#endif
    m_nvosd     = gst_element_factory_make ("cuosd", nullptr);
#endif
    m_filter    = gst_element_factory_make ("capsfilter", nullptr);

#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    /* SW path creates overlay_bin as follows :
     * videoconvert ! video/x-raw, format=RGBA ! cuosd ! video/x-raw, format=RGBA ! videoconvert ! video/x-raw, format=I420
     */
    if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
    {
        converter2 = gst_element_factory_make ("videoconvert", nullptr);
        filter2 = gst_element_factory_make ("capsfilter", nullptr);
        gst_bin_add_many (GST_BIN (overlay_bin), converter2, filter2, nullptr);
        converter1 = gst_element_factory_make ("videoconvert", nullptr);
        filter1 = gst_element_factory_make ("capsfilter", nullptr);
        gst_bin_add_many (GST_BIN (overlay_bin), converter1, filter1, nullptr);
    }
    /* overlay_bin with HW Dec and SW Enc as follows :
     * cuosd ! video/x-raw(memory:NVMM), format=NV12 ! nvvideoconvert ! video/x-raw, format=I420
     */
    else if (false == NvHwDetection::getInstance()->m_useNvV4l2Enc)
    {
        converter2 = gst_element_factory_make ("nvvideoconvert", nullptr);
        filter2 = gst_element_factory_make ("capsfilter", nullptr);

        if (!converter2 || !filter2)
        {
            LOG (error) << "Gstreamer elements creation failed -"
                        << " nvvideoconvert(converter2): " << (converter2 ? "OK" : "NULL")
                        << ", capsfilter(filter2): " << (filter2 ? "OK" : "NULL")
                        << ", use_software_path: " << GET_CONFIG().use_software_path
                        << ", g_isGpuPresent: " << g_isGpuPresent
                        << ", m_useNvV4l2Enc: " << NvHwDetection::getInstance()->m_useNvV4l2Enc
                        << endl;
            return nullptr;
        }
        gst_bin_add_many (GST_BIN (overlay_bin), converter2, filter2, nullptr);
    }
#endif

    if (!overlay_bin || !m_nvosd || !m_filter )
    {
        LOG (error) << "Gstreamer element creation failed" << endl;
        return nullptr;
    }
    gst_bin_add_many (GST_BIN (overlay_bin), m_nvosd, m_filter, nullptr);

#ifdef USE_CUOSD
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
    {
        g_object_set (G_OBJECT (m_nvosd), "enable-cpu-mode" , true, nullptr);
    }
    else
    {
        g_object_set (G_OBJECT (m_nvosd), "gpu-id"   , g_gpuIndex, nullptr);
    }
#else
    g_object_set (G_OBJECT (m_nvosd), "gpu-id"   , g_gpuIndex, nullptr);
#endif
    if (!gst_element_link (m_nvosd, m_filter))
    {
        LOG (error) << "Overlay Elements could not be linked" << endl;
        return nullptr;
    }
#endif

#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    source_pad = gst_element_get_static_pad (m_filter, "src");
#else
    if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
    {
        if (!gst_element_link_many (converter1, filter1, m_nvosd, nullptr))
        {
            LOG (error) << "Before Converter Elements could not be linked" << endl;
        }
        if (!gst_element_link_many (m_filter, converter2, filter2, nullptr))
        {
            LOG (error) << "After Converter Elements could not be linked" << endl;
        }
        source_pad = gst_element_get_static_pad (filter2, "src");
    }
    else if (false == NvHwDetection::getInstance()->m_useNvV4l2Enc)
    {
        if (!gst_element_link_many (m_filter, converter2, filter2, nullptr))
        {
            LOG (error) << "After Converter Elements could not be linked" << endl;
        }
        source_pad = gst_element_get_static_pad (filter2, "src");
    }
    else
    {
        source_pad = gst_element_get_static_pad (m_filter, "src");
    }
#endif
    if (source_pad)
    {
        ghost_sourcepad = gst_ghost_pad_new ("src", source_pad);
        gst_pad_set_active (ghost_sourcepad, TRUE);
        gst_element_add_pad (overlay_bin, ghost_sourcepad);
        gst_object_unref (source_pad);
    }
    else
    {
        LOG(error) << "Failed to get src pad from nvvideoconvert" << endl;
        return nullptr;
    }

#ifdef USE_CUOSD
    if(isLive)
    {
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        if (!gst_element_link (latency_queue, m_nvosd))
        {
            LOG (error) << "Queue Element could not be linked" << endl;
            return nullptr;
        }
#else
        if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
        {
            if (!gst_element_link (latency_queue, converter1))
            {
                LOG (error) << "Queue Element could not be linked" << endl;
                return nullptr;
            }
        }
        else
        {
            if (!gst_element_link (latency_queue, m_nvosd))
            {
                LOG (error) << "Queue Element could not be linked" << endl;
                return nullptr;
            }
        }
#endif
        sink_pad = gst_element_get_static_pad (latency_queue, "sink");
    }
    else
    {
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
        sink_pad = gst_element_get_static_pad (m_nvosd, "sink");
#else
        if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
        {
            sink_pad = gst_element_get_static_pad (converter1, "sink");
        }
        else
        {
            sink_pad = gst_element_get_static_pad (m_nvosd, "sink");
        }
#endif
    }
#endif
    if (sink_pad)
    {
        ghost_sinkpad = gst_ghost_pad_new ("sink", sink_pad);
        gst_pad_set_active (ghost_sinkpad, TRUE);
        gst_element_add_pad (overlay_bin, ghost_sinkpad);
        gst_object_unref (sink_pad);
    }
    else
    {
        LOG(error) << "Failed to get sink pad from nvstreammux" << endl;
        return nullptr;
    }

    GstCaps *caps_filter  = nullptr;
#if defined(AARCH64_PLATFORM) || defined(JETSON_PLATFORM)
    caps_filter = gst_caps_from_string ("video/x-raw(memory:NVMM),format=NV12");
#else
    if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
    {
        caps_filter = gst_caps_from_string ("video/x-raw,format=RGBA");
    }
    else
    {
        caps_filter = gst_caps_from_string ("video/x-raw(memory:NVMM),format=NV12");
    }
#endif
    g_object_set (G_OBJECT (m_filter), "caps", caps_filter, nullptr);
    gst_caps_unref (caps_filter);
#if !defined(AARCH64_PLATFORM) && !defined(JETSON_PLATFORM)
    if (GET_CONFIG().use_software_path || g_isGpuPresent == false)
    {
        GstCaps *caps_filter1  = nullptr;
        caps_filter1 = gst_caps_from_string ("video/x-raw,format=RGBA");
        g_object_set (G_OBJECT (filter1), "caps", caps_filter1, nullptr);
        gst_caps_unref (caps_filter1);
        GstCaps *caps_filter2  = nullptr;
        caps_filter2 = gst_caps_from_string ("video/x-raw,format=I420");
        g_object_set (G_OBJECT (filter2), "caps", caps_filter2, nullptr);
        gst_caps_unref (caps_filter2);
    }
    else if (false == NvHwDetection::getInstance()->m_useNvV4l2Enc)
    {
        GstCaps *caps_filter2  = nullptr;
        caps_filter2 = gst_caps_from_string ("video/x-raw,format=I420");
        g_object_set (G_OBJECT (filter2), "caps", caps_filter2, nullptr);
        gst_caps_unref (caps_filter2);
    }
#endif
    osd_sink_pad = gst_element_get_static_pad (m_nvosd, "sink");
    if (!osd_sink_pad)
    {
        LOG (error) << "Unable to get sink pad" << endl;
        return nullptr;
    }
    else
    {
        gst_pad_add_probe (osd_sink_pad, GST_PAD_PROBE_TYPE_BUFFER,
                            osd_sink_pad_buffer_probe,
                            (void*)this, nullptr);
        gst_object_unref (osd_sink_pad);
        osd_sink_pad = nullptr;
    }

    nv_vms::DeviceConfig config =  GET_CONFIG();
    m_use_protobuf = config.use_video_metadata_protobuf;

    m_isGst = true;
    if (osd_ctx)
    {
        GET_OSD_INSTANCE()->osd_destroy((OsdContext_t)osd_ctx);
        osd_ctx = nullptr;
    }
    return overlay_bin;
}

void NvLLOverlayInternal::updateIdList(std::vector<string> idList[OVERLAYCOUNT])
{
    std::lock_guard<std::mutex> guard(m_idLock);
    for (uint32_t i = 0; i < OVERLAYCOUNT; i++)
    {
        m_idList[i].clear();
        m_idList[i].assign(idList[i].begin(), idList[i].end());
    }
}

void NvLLOverlayInternal::updateClassTypeList(std::vector<string> classTypeList)
{
    std::lock_guard<std::mutex> guard(m_classTypeLock);
    m_classTypeList.clear();
    m_classTypeList.assign(classTypeList.begin(), classTypeList.end());
}

void NvLLOverlayInternal::addFrameTs(int64_t ts)
{
    std::lock_guard<std::mutex> lock(m_bboxParams.m_frameTSLock);
    m_bboxParams.m_frameTSQueue.push(ts);
    m_bboxParams.m_frameTsQueueCond.notify_all();
}

Json::Value NvLLOverlayInternal::getOverlayStatus(uint64_t firstFrameTS)
{
    Json::Value overlay;
    Json::Int64 metadata_size = 0;
    if (m_metadataStore)
    {
        metadata_size = m_metadataStore->getMetadataSize();
    }
    overlay["metadata_size"] = metadata_size;
    overlay["first_frame_timestamp"] = convertEpocToISO8601_2(firstFrameTS);

    Json::Value mismatch;
    for(auto mismatch_term: m_bboxParams.m_mismatches)
    {
        Json::Value mismatch_value;
        mismatch_value["frameTS"] = mismatch_term.first;
        mismatch_value["elasticTS"] = mismatch_term.second;
        mismatch.append(mismatch_value);
    }
    overlay["mismatch"] = mismatch;

    Json::Value bbox_shift;
    for(auto shift: m_bboxParams.m_shifts)
    {
        Json::Value bbox_shift_value;
        bbox_shift_value["frameTS"] = shift.first;
        bbox_shift_value["elasticTS"] = shift.second;
        bbox_shift.append(bbox_shift_value);
    }
    overlay["bbox_shift"] = bbox_shift;

    Json::Value objects;
    for(auto object: m_bboxParams.m_numObjects)
    {
        Json::Value objects_value;
        objects_value["frameTS"] = std::get<0>(object);
        objects_value["elasticTS"] = std::get<1>(object);
        Json::Int64 num_objects = std::get<2>(object);
        objects_value["objects"] = num_objects;
        objects.append(objects_value);
    }
    overlay["#objects"] = objects;

    Json::Value capture_period;
    if (m_bboxParams.m_numObjects.size() > 0)
    {
        auto start_tuple = m_bboxParams.m_numObjects[0];
        auto end_tuple = m_bboxParams.m_numObjects[m_bboxParams.m_numObjects.size()-1];
        capture_period["start_time"] = std::get<1>(start_tuple);
        capture_period["end_time"] = std::get<1>(end_tuple);
    }
    overlay["capture_period"] = capture_period;

    m_bboxParams.m_mismatches.clear();
    m_bboxParams.m_shifts.clear();
    m_bboxParams.m_numObjects.clear();
    return overlay;
}

void NvLLOverlayInternal::fetchMetadataAgain (string new_start)
{
    LOG(info) << "Re-fetching metadata from elasticSearch with start: " << new_start << endl;
    if (m_replayMetadataStore)
    {
        m_replayMetadataStore->fetchMetadataAgain(new_start);
    }
}

// Function to check for pose data in JSON object and extract relevant information
bool NvLLOverlayInternal::check_pose_data(const Json::Value& object,
                                          const std::map<std::string, float, std::less<>>& coordinates,
                                          std::vector<float>& keypoints,
                                          std::string& action_label,
                                          int& label_x,
                                          int& label_y)
{
    // Check if pose data exists
    if (!object.isMember("pose") || !object["pose"].isObject())
    {
        LOG(warning) << "No pose data found in object" << endl;
        return false;
    }

    Json::Value pose_data = object["pose"];
    if (!pose_data.isMember("keypoints") || !pose_data["keypoints"].isArray())
    {
        LOG(warning) << "Pose data missing keypoints or keypoints not array" << endl;
        return false;
    }

    // Extract keypoints in RPM format (4 values per keypoint: x, y, z, confidence)
    Json::Value keypoints_array = pose_data["keypoints"];
    keypoints.clear();

    // Check if keypoints are in structured format (array of objects with coordinates)
    if (keypoints_array.size() > 0 && keypoints_array[0].isObject())
    {
        // Structured format: each keypoint is an object with coordinates array
        keypoints.reserve(keypoints_array.size() * 4);
        for (Json::Value::ArrayIndex j = 0; j < keypoints_array.size(); j++)
        {
            if (keypoints_array[j].isMember("coordinates") && keypoints_array[j]["coordinates"].isArray())
            {
                Json::Value coords = keypoints_array[j]["coordinates"];
                if (coords.size() >= 4)
                {
                    keypoints.push_back(coords[0].asFloat()); // x
                    keypoints.push_back(coords[1].asFloat()); // y
                    keypoints.push_back(coords[2].asFloat()); // z
                    keypoints.push_back(coords[3].asFloat()); // confidence
                }
                else
                {
                    LOG(warning) << "Keypoint " << j << " has insufficient coordinates: " << coords.size() << endl;
                }
            }
            else
            {
                LOG(warning) << "Keypoint " << j << " missing coordinates" << endl;
            }
        }
    }
    else
    {
        // Flattened format: convert to 4-values-per-keypoint format
        keypoints.reserve(keypoints_array.size());
        for (Json::Value::ArrayIndex j = 0; j < keypoints_array.size(); j++)
        {
            keypoints.push_back(keypoints_array[j].asFloat());
        }
    }

    // Extract action label if available
    action_label = "";
    if (pose_data.isMember("action") && pose_data["action"].isString())
    {
        action_label = pose_data["action"].asString();

        // Also add confidence if available
        if (pose_data.isMember("action_confidence") && pose_data["action_confidence"].isNumeric())
        {
            float confidence = pose_data["action_confidence"].asFloat();
            std::stringstream ss;
            ss << std::fixed << std::setprecision(4) << confidence;
            action_label += " (" + ss.str() + ")";
        }
    }
    else if (object.isMember("action") && object["action"].isString())
    {
        action_label = object["action"].asString();
    }
    else if (pose_data.isMember("actions") && pose_data["actions"].isArray() && pose_data["actions"].size() > 0)
    {
        // Fallback: if no primary action but actions array exists, use the first one
        Json::Value firstAction = pose_data["actions"][0];
        if (firstAction.isMember("type") && firstAction["type"].isString())
        {
            action_label = firstAction["type"].asString();
            if (firstAction.isMember("confidence") && firstAction["confidence"].isNumeric())
            {
                float confidence = firstAction["confidence"].asFloat();
                std::stringstream ss;
                ss << std::fixed << std::setprecision(4) << confidence;
                action_label += " (" + ss.str() + ")";
            }
        }
    }
    else
    {
        LOG(info) << "No action data found in pose" << endl;
    }

    // Calculate position for action label (next to the bounding box)
    label_x = 20;
    label_y = 20;
    if (coordinates.find("leftX") != coordinates.end() && coordinates.find("topY") != coordinates.end())
    {
        label_x = static_cast<int>(coordinates.at("leftX"));
        label_y = static_cast<int>(coordinates.at("topY")) + 30; // Offset below bbox
    }

    return true;
}

// Pose drawing functionality adapted from RPM implementation
void NvLLOverlayInternal::draw_pose_cuosd(const std::vector<float>& keypoints,
                                          const std::string& action_label,
                                          BBoxDrawingData* box_params,
                                          OsdContext_t context,
                                          GstBuffer* buffer,
                                          int x, int y)
{
    const int keypoint_radius = 3;
    const int keypoint_line_width = 2;
    const int numKeyPoints = 18;

    // Validate keypoint data (should be numKeyPoints * 4 for x,y,z,confidence format)
    if (keypoints.size() < static_cast<size_t>(numKeyPoints * 4))
    {
        LOG(warning) << "Insufficient keypoint data for pose drawing. Expected: "
                    << numKeyPoints * 4 << ", Got: " << keypoints.size() << endl;
        return;
    }

    // Additional safety check for null pointer
    if (!box_params)
    {
        LOG(error) << "box_params is null in draw_pose_cuosd" << endl;
        return;
    }

    // RPM Joints: 0: base of spine, 1: middle of spine, 2: neck,
    //             3: left shoulder, 4: left elbow, 5: left wrist,
    //             6: right shoulder, 7: right elbow, 8: right wrist,
    //             9: left hip, 10: left knee, 11: left ankle,
    //             12: right hip, 13: right knee, 14: right ankle,
    //             15: nose, 16: left ear, 17: right ear

// Pose joint index constants
    constexpr int BASE_OF_SPINE = 0;
    constexpr int MIDDLE_OF_SPINE = 1;
    constexpr int NECK = 2;
    constexpr int LEFT_SHOULDER = 3;
    constexpr int LEFT_ELBOW = 4;
    constexpr int LEFT_WRIST = 5;
    constexpr int RIGHT_SHOULDER = 6;
    constexpr int RIGHT_ELBOW = 7;
    constexpr int RIGHT_WRIST = 8;
    constexpr int LEFT_HIP = 9;
    constexpr int LEFT_KNEE = 10;
    constexpr int LEFT_ANKLE = 11;
    constexpr int RIGHT_HIP = 12;
    constexpr int RIGHT_KNEE = 13;
    constexpr int RIGHT_ANKLE = 14;
    constexpr int NOSE = 15;
    constexpr int LEFT_EAR = 16;
    constexpr int RIGHT_EAR = 17;

    const int idx_bones[] = { LEFT_SHOULDER, LEFT_ELBOW, LEFT_ELBOW, LEFT_WRIST, //left arm
                              RIGHT_SHOULDER, RIGHT_ELBOW, RIGHT_ELBOW, RIGHT_WRIST, //right arm
                              LEFT_HIP, LEFT_KNEE, LEFT_KNEE, LEFT_ANKLE, //left leg
                              RIGHT_HIP, RIGHT_KNEE, RIGHT_KNEE, RIGHT_ANKLE, //right leg
                              LEFT_HIP, RIGHT_HIP, LEFT_SHOULDER, RIGHT_SHOULDER, //hip and shoulder
                              BASE_OF_SPINE, MIDDLE_OF_SPINE, MIDDLE_OF_SPINE, NECK, //spine neck
                              NOSE, LEFT_EAR, LEFT_EAR, RIGHT_EAR, RIGHT_EAR, NOSE, NOSE, NECK
                              };

    const OSD_ColorParams bone_colors[] = {
                            OSD_COLOR_RED,      // left arm
                            OSD_COLOR_RED,
                            OSD_COLOR_GREEN,    // right arm
                            OSD_COLOR_BLUE,
                            OSD_COLOR_BLUE,
                            OSD_COLOR_RED,      // left leg
                            OSD_COLOR_RED,
                            OSD_COLOR_RED,
                            OSD_COLOR_RED,
                            OSD_COLOR_RED,
                            OSD_COLOR_BLUE,     // right leg
                            OSD_COLOR_BLUE,
                            OSD_COLOR_BLUE,
                            OSD_COLOR_BLUE,
                            OSD_COLOR_RED,      // torso
                            OSD_COLOR_BLUE,
                            OSD_COLOR_GREEN,    // head
                            OSD_COLOR_GREEN,
                            OSD_COLOR_GREEN,
                            OSD_COLOR_GREEN,
                            OSD_COLOR_GREEN,
                            OSD_COLOR_GREEN,
                            OSD_COLOR_BLUE,
                            OSD_COLOR_RED,
                            OSD_COLOR_GREEN
                            };

    const size_t bone_colors_count = sizeof(bone_colors) / sizeof(bone_colors[0]);

    // Validate idx_bones array - ensure all indices are within bounds
    const int num_bones = sizeof(idx_bones) / (2 * sizeof(idx_bones[0]));
    for (int i = 0; i < num_bones * 2; i++)
    {
        if (idx_bones[i] >= numKeyPoints)
        {
            LOG(error) << "Invalid bone index " << idx_bones[i] << " >= " << numKeyPoints << endl;
            return;
        }
    }

    // Draw keypoints as circles (matching RPM implementation)
    for (int i = 0; i < numKeyPoints; i++)
    {
        // Enhanced bounds checking for array access
        size_t x_index = static_cast<size_t>(4 * i);
        size_t y_index = static_cast<size_t>(4 * i + 1);

        if (x_index >= keypoints.size() || y_index >= keypoints.size())
        {
            LOG(warning) << "Keypoint index out of bounds: " << i << endl;
            continue;
        }

        // Use RPM-style indexing: keypoints[4*i] for x, keypoints[4*i+1] for y
        float kx = keypoints[x_index];
        float ky = keypoints[y_index];
        //float confidence = keypoints[4 * i + 3]; // confidence is at index 3

        // Draw all keypoints (removed confidence check to match RPM behavior)
        auto circle_params = std::make_unique<OSD_CircleParams>();
        if (circle_params)
        {
            Point start = interpolateCoordinate(kx, ky, m_sourceWidth, m_sourceHeight, m_width, m_height);
            circle_params->pos_x = start.x;
            circle_params->pos_y = start.y;
            circle_params->radius = keypoint_radius;
            circle_params->thickness = 1;
            circle_params->border_color = OSD_COLOR_RED;
            circle_params->border_color.alpha = box_params->m_overlay.m_bboxOpacity;
            circle_params->bg_color = OSD_COLOR_RED;
            circle_params->bg_color.alpha = box_params->m_overlay.m_bboxOpacity;

            auto circle_params_copy = std::make_unique<OSD_CircleParams>(*circle_params);
            if (circle_params_copy)
            {
                if (buffer)
                {
                    GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_CIRCLE, circle_params_copy.release());
                }
                else
                {
                    OsdMeta meta;
                    meta.meta_type = OSD_CIRCLE;
                    meta.params = static_cast<void*>(circle_params_copy.release());
                    GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
                }
            }
            else
            {
                LOG(error) << "Failed to allocate circle_params_copy" << endl;
            }
        }
        else
        {
            LOG(error) << "Failed to allocate circle_params" << endl;
        }
    }

    // Draw skeleton connections (matching RPM implementation)
    for (int i = 0; i < num_bones; i++)
    {
        int i0 = idx_bones[2 * i];
        int i1 = idx_bones[2 * i + 1];

        // Enhanced bounds checking - verify indices are valid
        if (i0 < 0 || i0 >= numKeyPoints || i1 < 0 || i1 >= numKeyPoints)
        {
            LOG(warning) << "Invalid bone indices: " << i0 << ", " << i1 << endl;
            continue;
        }

        // Additional bounds checking for keypoints array access
        size_t x1_index = static_cast<size_t>(4 * i0);
        size_t y1_index = static_cast<size_t>(4 * i0 + 1);
        size_t x2_index = static_cast<size_t>(4 * i1);
        size_t y2_index = static_cast<size_t>(4 * i1 + 1);

        if (x1_index >= keypoints.size() || y1_index >= keypoints.size() ||
            x2_index >= keypoints.size() || y2_index >= keypoints.size())
        {
            LOG(warning) << "Bone keypoint indices out of bounds" << endl;
            continue;
        }

        // Use RPM-style indexing: keypoints[4*i] for x, keypoints[4*i+1] for y
        float x1 = keypoints[x1_index];
        float y1 = keypoints[y1_index];
        float x2 = keypoints[x2_index];
        float y2 = keypoints[y2_index];

        // Draw all connections (removed confidence check to match RPM behavior)
        auto line_params = std::make_unique<OSD_LineParams>();
        if (line_params)
        {
            Point start = interpolateCoordinate(x1, y1, m_sourceWidth, m_sourceHeight, m_width, m_height);
            Point end = interpolateCoordinate(x2, y2, m_sourceWidth, m_sourceHeight, m_width, m_height);
            line_params->pos_x0 = start.x;
            line_params->pos_y0 = start.y;
            line_params->pos_x1 = end.x;
            line_params->pos_y1 = end.y;
            line_params->thickness = keypoint_line_width;
            line_params->is_interpolation = true;

            // Use the same color scheme as RPM with bounds checking
            OSD_ColorParams line_color = (static_cast<size_t>(i) < bone_colors_count) ?
                                        bone_colors[i] : OSD_COLOR_WHITE;
            line_color.alpha = box_params->m_overlay.m_bboxOpacity;

            // Use direct OSD calls instead of helper function to match RPM approach
            auto line_params_copy = std::make_unique<OSD_LineParams>(*line_params);
            if (line_params_copy)
            {
                line_params_copy->color = line_color;

                if (buffer)
                {
                    GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_LINE, line_params_copy.release());
                }
                else
                {
                    OsdMeta meta;
                    meta.meta_type = OSD_LINE;
                    meta.params = static_cast<void*>(line_params_copy.release());
                    GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
                }
            }
            else
            {
                LOG(error) << "Failed to allocate line_params_copy" << endl;
            }
        }
        else
        {
            LOG(error) << "Failed to allocate line_params" << endl;
        }
    }

    // Draw action label if provided
    if (!action_label.empty())
    {
        Point text_pos = interpolateCoordinate(x, y, m_sourceWidth, m_sourceHeight, m_width, m_height);

        OSD_TextParams* text_params = (OSD_TextParams*)malloc(sizeof(OSD_TextParams));
        if (text_params)
        {
            // Use safe strncpy with explicit bounds checking
            char* cstr = (char*)calloc(action_label.size() + 1, sizeof(char));
            if (cstr)
            {
                strncpy(cstr, action_label.c_str(), action_label.size());
                cstr[action_label.size()] = '\0';  // Guarantee null termination
                text_params->text = cstr;
            }
            else
            {
                LOG(error) << "Failed to allocate memory for action label text" << endl;
                text_params->text = nullptr;
            }

            text_params->pos_x = text_pos.x;
            text_params->pos_y = text_pos.y;
            text_params->font_size = DEFAULT_FONT_SIZE_COORDINATES;

            // Add error checking for strdup
            const char* font_type_str = GET_CONFIG().overlay_text_font_type.c_str();
            text_params->font_type = strdup(font_type_str);
            if (!text_params->font_type)
            {
                LOG(error) << "Failed to duplicate font type string" << endl;
                text_params->font_type = nullptr;
            }

            text_params->border_color = (OSD_ColorParams){255, 255, 255, 255};
            text_params->bg_color = (OSD_ColorParams){0, 0, 0, 200};

            // Only proceed if we have valid text data
            if (text_params->text)
            {
                if (buffer)
                {
                    GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_TEXT, text_params);
                }
                else
                {
                    OsdMeta meta;
                    meta.meta_type = OSD_TEXT;
                    meta.params = (void *)text_params;
                    GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
                }
            }
            else
            {
                // Clean up if we failed to allocate text
                if (text_params->font_type)
                {
                    free(text_params->font_type);
                }
                free(text_params);
            }
        }
        else
        {
            LOG(error) << "Failed to allocate text_params" << endl;
        }
    }
}

void NvLLOverlayInternal::draw_ellipse_around_2d_bbox(const Point& left_top, const Point& right_bottom, BBoxDrawingData* box_params, OsdContext_t context, GstBuffer* buffer)
{
    int left = 0, right = 0, bottom = 0;

    left    = left_top.x;
    right   = right_bottom.x;
    bottom  = right_bottom.y;

    OSD_EllipseParams* ellipse_params = (OSD_EllipseParams*)malloc(sizeof(OSD_EllipseParams));
    if (ellipse_params)
    {
        // Calculate the midpoint of the bottom line of the 2D box
        float centerX = (left + right) / 2.0f;  // Midpoint X of bottom line
        float centerY = bottom;                 // Y coordinate of bottom line

        // Update ellipse center to use the midpoint of the bottom line
        Point center = interpolateCoordinate(centerX, centerY, m_sourceWidth, m_sourceHeight, m_width, m_height);
        ellipse_params->pos_x = center.x;
        ellipse_params->pos_y = center.y;

        // Calculate the 2D box dimensions
        float box_width = right - left;
        float box_height = box_width * DEFAULT_ELLIPSE_HEIGHT_FACTOR;

        // Set ellipse dimensions based on the 2D box dimensions with 33% more scaled factor
        // Clamp proximityAreaFactor to prevent float overflow
        double clampedFactor = std::min(box_params->m_overlay.m_proximityAreaFactor, MAX_PROXIMITY_AREA_FACTOR);
        float scaleFactor = static_cast<float>(clampedFactor * DEFAULT_ELLIPSE_SCALE_FACTOR);
        ellipse_params->width = box_width * scaleFactor;
        ellipse_params->height = box_height * scaleFactor;

        ellipse_params->yaw = 0.0f;
        ellipse_params->thickness = 1;
        // Set border color to white with full opacity
        ellipse_params->border_color = OSD_COLOR_WHITE;
        // Set background color to red transparent
        ellipse_params->bg_color = OSD_COLOR_RED_TRANSPARENT;

        if (buffer)
        {
            GET_OSD_INSTANCE()->gst_buffer_add_cu_osd_meta(buffer, OSD_ELLIPSE, ellipse_params);
        }
        else
        {
            OsdMeta meta;
            meta.meta_type = OSD_ELLIPSE;
            meta.params = (void*)ellipse_params;
            GET_OSD_INSTANCE()->osd_add_metadata(context, &meta);
        }
    }
}
