/*
* SPDX-FileCopyrightText: Copyright (c) 2025-2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
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

#include <cstring>
#include <iostream>
#include "nvdsinfer_custom_impl.h"
#include <cassert>
#include <cmath>
#include <algorithm>
#include <map>

#define MIN(a,b) ((a) < (b) ? (a) : (b))
#define MAX(a,b) ((a) > (b) ? (a) : (b))
#define CLIP(a,min,max) (MAX(MIN(a, max), min))
#define DIVIDE_AND_ROUND_UP(a, b) ((a + b - 1) / b)

struct MrcnnRawDetection {
    float y1, x1, y2, x2, class_id, score;
};
/* This is a sample bounding box parsing function for the sample FasterRCNN
 *
 * detector model provided with the SDK. */

/* C-linkage to prevent name-mangling */
extern "C"
bool NvDsInferParseCustomNMSTLT (
         std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
         NvDsInferNetworkInfo  const &networkInfo,
         NvDsInferParseDetectionParams const &detectionParams,
         std::vector<NvDsInferObjectDetectionInfo> &objectList);

extern "C"
bool NvDsInferParseCustomBatchedNMSTLT (
         std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
         NvDsInferNetworkInfo  const &networkInfo,
         NvDsInferParseDetectionParams const &detectionParams,
         std::vector<NvDsInferObjectDetectionInfo> &objectList);

extern "C"
bool NvDsInferParseCustomDDETRTAO (std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
                                NvDsInferNetworkInfo const &networkInfo,
                                NvDsInferParseDetectionParams const &detectionParams,
                                std::vector<NvDsInferObjectDetectionInfo> &objectList);

extern "C"
bool NvDsInferParseCustomEfficientDetTAO (
         std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
         NvDsInferNetworkInfo  const &networkInfo,
         NvDsInferParseDetectionParams const &detectionParams,
         std::vector<NvDsInferObjectDetectionInfo> &objectList);


extern "C"
bool NvDsInferParseCustomNMSTLT (std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
                                   NvDsInferNetworkInfo  const &networkInfo,
                                   NvDsInferParseDetectionParams const &detectionParams,
                                   std::vector<NvDsInferObjectDetectionInfo> &objectList) {
    if(outputLayersInfo.size() != 2)
    {
        std::cerr << "Mismatch in the number of output buffers."
                  << "Expected 2 output buffers, detected in the network :"
                  << outputLayersInfo.size() << std::endl;
        return false;
    }

    // Host memory for "nms" which has 2 output bindings:
    // the order is bboxes and keep_count
    float* out_nms = (float *) outputLayersInfo[0].buffer;
    int * p_keep_count = (int *) outputLayersInfo[1].buffer;
    const int out_class_size = detectionParams.numClassesConfigured;
    const float threshold = detectionParams.perClassThreshold[0];

    float* det;

    for (int i = 0; i < p_keep_count[0]; i++) {
        det = out_nms + i * 7;

        // Output format for each detection is stored in the below order
        // [image_id, label, confidence, xmin, ymin, xmax, ymax]
        if ( det[2] < threshold) continue;
        assert((int) det[1] < out_class_size);

#if 0
        std::cout << "id/label/conf/ x/y x/y -- "
                  << det[0] << " " << det[1] << " " << det[2] << " "
                  << det[3] << " " << det[4] << " " << det[5] << " " << det[6] << std::endl;
#endif
        NvDsInferObjectDetectionInfo object;
            object.classId = (int) det[1];
            object.detectionConfidence = det[2];

            /* Clip object box co-ordinates to network resolution */
            object.left = CLIP(det[3] * networkInfo.width, 0, networkInfo.width - 1);
            object.top = CLIP(det[4] * networkInfo.height, 0, networkInfo.height - 1);
            object.width = CLIP((det[5] - det[3]) * networkInfo.width, 0, networkInfo.width - 1);
            object.height = CLIP((det[6] - det[4]) * networkInfo.height, 0, networkInfo.height - 1);

            objectList.push_back(object);
    }

    return true;
}

extern "C"
bool NvDsInferParseCustomBatchedNMSTLT (
         std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
         NvDsInferNetworkInfo  const &networkInfo,
         NvDsInferParseDetectionParams const &detectionParams,
         std::vector<NvDsInferObjectDetectionInfo> &objectList) {

	 if(outputLayersInfo.size() != 4)
    {
        std::cerr << "Mismatch in the number of output buffers."
                  << "Expected 4 output buffers, detected in the network :"
                  << outputLayersInfo.size() << std::endl;
        return false;
    }

    /* Host memory for "BatchedNMS"
       BatchedNMS has 4 output bindings, the order is:
       keepCount, bboxes, scores, classes
    */
    int* p_keep_count = (int *) outputLayersInfo[0].buffer;
    float* p_bboxes = (float *) outputLayersInfo[1].buffer;
    float* p_scores = (float *) outputLayersInfo[2].buffer;
    float* p_classes = (float *) outputLayersInfo[3].buffer;

    const float threshold = detectionParams.perClassThreshold[0];

    const int keep_top_k = 200;
    const char* log_enable = std::getenv("ENABLE_DEBUG");

    if(log_enable != NULL && std::stoi(log_enable)) {
        std::cout <<"keep cout"
              <<p_keep_count[0] << std::endl;
    }

    for (int i = 0; i < p_keep_count[0] && objectList.size() <= keep_top_k; i++) {

        if ( p_scores[i] < threshold) continue;

        if(log_enable != NULL && std::stoi(log_enable)) {
            std::cout << "label/conf/ x/y x/y -- "
                      << p_classes[i] << " " << p_scores[i] << " "
                      << p_bboxes[4*i] << " " << p_bboxes[4*i+1] << " " << p_bboxes[4*i+2] << " "<< p_bboxes[4*i+3] << " " << std::endl;
        }

        if((unsigned int) p_classes[i] >= detectionParams.numClassesConfigured) continue;
        if(p_bboxes[4*i+2] < p_bboxes[4*i] || p_bboxes[4*i+3] < p_bboxes[4*i+1]) continue;

        NvDsInferObjectDetectionInfo object;
        object.classId = (int) p_classes[i];
        object.detectionConfidence = p_scores[i];

        /* Clip object box co-ordinates to network resolution */
        object.left = CLIP(p_bboxes[4*i] * networkInfo.width, 0, networkInfo.width - 1);
        object.top = CLIP(p_bboxes[4*i+1] * networkInfo.height, 0, networkInfo.height - 1);
        object.width = CLIP(p_bboxes[4*i+2] * networkInfo.width, 0, networkInfo.width - 1) - object.left;
        object.height = CLIP(p_bboxes[4*i+3] * networkInfo.height, 0, networkInfo.height - 1) - object.top;

        if(object.height < 0 || object.width < 0)
            continue;
        objectList.push_back(object);
    }
    return true;
}

extern "C"
bool NvDsInferParseCustomMrcnnTLTV2 (std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
                                   NvDsInferNetworkInfo  const &networkInfo,
                                   NvDsInferParseDetectionParams const &detectionParams,
                                   std::vector<NvDsInferInstanceMaskInfo> &objectList) {
    auto layerFinder = [&outputLayersInfo](const std::string &name)
        -> const NvDsInferLayerInfo *{
        for (auto &layer : outputLayersInfo) {
            if (layer.dataType == FLOAT &&
              (layer.layerName && name == layer.layerName)) {
                return &layer;
            }
        }
        return nullptr;
    };

    const NvDsInferLayerInfo *detectionLayer = layerFinder("generate_detections");
    const NvDsInferLayerInfo *maskLayer = layerFinder("mask_fcn_logits/BiasAdd");

    if (!detectionLayer || !maskLayer) {
        std::cerr << "ERROR: some layers missing or unsupported data types "
                  << "in output tensors" << std::endl;
        return false;
    }

    if(maskLayer->inferDims.numDims != 4U) {
        std::cerr << "Network output number of dims is : " <<
            maskLayer->inferDims.numDims << " expect is 4"<< std::endl;
        return false;
    }

    const unsigned int det_max_instances = maskLayer->inferDims.d[0];
    const unsigned int num_classes = maskLayer->inferDims.d[1];
    if(num_classes != detectionParams.numClassesConfigured) {
        std::cerr << "WARNING: Num classes mismatch. Configured:" <<
            detectionParams.numClassesConfigured << ", detected by network: " <<
            num_classes << std::endl;
    }
    const unsigned int mask_instance_height= maskLayer->inferDims.d[2];
    const unsigned int mask_instance_width = maskLayer->inferDims.d[3];

    auto out_det = reinterpret_cast<MrcnnRawDetection*>( detectionLayer->buffer);
    auto out_mask = reinterpret_cast<float(*)[mask_instance_width *
        mask_instance_height]>(maskLayer->buffer);

    for(auto i = 0U; i < det_max_instances; i++) {
        MrcnnRawDetection &rawDec = out_det[i];

        if(rawDec.score < detectionParams.perClassPreclusterThreshold[0])
            continue;

        NvDsInferInstanceMaskInfo obj;
        obj.left = CLIP(rawDec.x1, 0, networkInfo.width - 1);
        obj.top = CLIP(rawDec.y1, 0, networkInfo.height - 1);
        obj.width = CLIP(rawDec.x2, 0, networkInfo.width - 1) - rawDec.x1;
        obj.height = CLIP(rawDec.y2, 0, networkInfo.height - 1) - rawDec.y1;
        if(obj.width <= 0 || obj.height <= 0)
            continue;
        obj.classId = static_cast<int>(rawDec.class_id);
        obj.detectionConfidence = rawDec.score;

        obj.mask_size = sizeof(float)*mask_instance_width*mask_instance_height;
        obj.mask = new float[mask_instance_width*mask_instance_height];
        obj.mask_width = mask_instance_width;
        obj.mask_height = mask_instance_height;

        float *rawMask = reinterpret_cast<float *>(out_mask + i
                         * detectionParams.numClassesConfigured + obj.classId);
        memcpy (obj.mask, rawMask, sizeof(float)*mask_instance_width*mask_instance_height);

        objectList.push_back(obj);
    }

    return true;

}

extern "C"
bool NvDsInferParseCustomDDETRTAO (std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
                                NvDsInferNetworkInfo const &networkInfo,
                                NvDsInferParseDetectionParams const &detectionParams,
                                std::vector<NvDsInferObjectDetectionInfo> &objectList) {

    // Code from NvDsInferParseCustomTfSSD for layer finding
    auto layerFinder = [&outputLayersInfo](const std::string &name)
        -> const NvDsInferLayerInfo *{
        for (auto &layer : outputLayersInfo) {
            if (layer.dataType == FLOAT &&
              (layer.layerName && name == layer.layerName)) {
                return &layer;
            }
        }
        return nullptr;
    };

    const NvDsInferLayerInfo *boxLayer = layerFinder("pred_boxes"); // 1 x num_queries x 4
    const NvDsInferLayerInfo *classLayer = layerFinder("pred_logits");  // 1 x num_queries x num_classes

    if (!boxLayer || !classLayer) {
        std::cerr << "ERROR: some layers missing or unsupported data types "
                  << "in output tensors" << std::endl;
        return false;
    }

    const int keep_top_k = 200;
    unsigned int numDetections = classLayer->inferDims.d[0];
    unsigned int numClasses = classLayer->inferDims.d[1];
    std::map<float, NvDsInferObjectDetectionInfo> ordered_objects;

    for (unsigned int idx = 0; idx < numDetections; idx += 1) {
        NvDsInferObjectDetectionInfo res;

        unsigned int class_layer_idx = idx * numClasses;

        res.classId = std::max_element(((float*)classLayer->buffer+class_layer_idx), ((float*)classLayer->buffer+class_layer_idx+numClasses)) - ((float*)classLayer->buffer+class_layer_idx);
        res.detectionConfidence = ((float*)classLayer->buffer)[class_layer_idx+res.classId];

        // If model does not have sigmoid layer, perform sigmoid calculation here
        res.detectionConfidence = 1.0/(1.0 + exp(-res.detectionConfidence));

        if(res.classId == 0 || res.detectionConfidence < detectionParams.perClassPreclusterThreshold[res.classId]) {
            continue;
        }
        enum {cx, cy, w, h};
        float rectX1f, rectY1f, rectX2f, rectY2f;

        unsigned int box_layer_idx = idx * 4;

        rectX1f = (((float*)boxLayer->buffer)[box_layer_idx + cx] - (((float*)boxLayer->buffer)[box_layer_idx + w]/2)) * networkInfo.width;
        rectY1f = (((float*)boxLayer->buffer)[box_layer_idx + cy] - (((float*)boxLayer->buffer)[box_layer_idx + h]/2)) * networkInfo.height;
        rectX2f = rectX1f + ((float*)boxLayer->buffer)[box_layer_idx + w] * networkInfo.width;
        rectY2f = rectY1f + ((float*)boxLayer->buffer)[box_layer_idx + h] * networkInfo.height;

        rectX1f = CLIP(rectX1f, 0.0f, networkInfo.width - 1);
        rectX2f = CLIP(rectX2f, 0.0f, networkInfo.width - 1);
        rectY1f = CLIP(rectY1f, 0.0f, networkInfo.height - 1);
        rectY2f = CLIP(rectY2f, 0.0f, networkInfo.height - 1);

        res.left = rectX1f;
        res.top = rectY1f;
        res.width = rectX2f - rectX1f;
        res.height = rectY2f - rectY1f;

        ordered_objects[res.detectionConfidence] = res;
    }

    int jdx = 0;
    for (auto iter=ordered_objects.rbegin(); iter!=ordered_objects.rend() && jdx<keep_top_k; iter++, jdx++) {
        if (iter->second.classId != 0){
        objectList.emplace_back(iter->second);}
    }
    return true;
}


extern "C"
bool NvDsInferParseCustomEfficientDetTAO (std::vector<NvDsInferLayerInfo> const &outputLayersInfo,
                                   NvDsInferNetworkInfo  const &networkInfo,
                                   NvDsInferParseDetectionParams const &detectionParams,
                                   std::vector<NvDsInferObjectDetectionInfo> &objectList) {
    if(outputLayersInfo.size() != 4)
    {
        std::cerr << "Mismatch in the number of output buffers."
                  << "Expected 4 output buffers, detected in the network :"
                  << outputLayersInfo.size() << std::endl;
        return false;
    }

    int* p_keep_count = (int *) outputLayersInfo[0].buffer;

    float* p_bboxes = (float *) outputLayersInfo[1].buffer;
    NvDsInferDims inferDims_p_bboxes = outputLayersInfo[1].inferDims;
    int numElements_p_bboxes=inferDims_p_bboxes.numElements;

    float* p_scores = (float *) outputLayersInfo[2].buffer;
    float* p_classes = (float *) outputLayersInfo[3].buffer;

    const int out_class_size = detectionParams.numClassesConfigured;
    const float threshold = detectionParams.perClassThreshold[0];

    float max_bbox=0;
    for (int i=0; i < numElements_p_bboxes; i++)
    {
        // std::cout <<"p_bboxes: "
              // <<p_bboxes[i] << std::endl;
        if ( max_bbox < p_bboxes[i] )
            max_bbox=p_bboxes[i];
    }
    int normalized = (max_bbox < 2.0);


    if (p_keep_count[0] > 0)
    {
        assert (normalized == 0);
        for (int i = 0; i < p_keep_count[0]; i++) {


            if ( p_scores[i] < threshold) continue;
            assert((int) p_classes[i] < out_class_size);


            // std::cout << "label/conf/ x/y x/y -- "
                      // << (int)p_classes[i] << " " << p_scores[i] << " "
                      // << p_bboxes[4*i] << " " << p_bboxes[4*i+1] << " " << p_bboxes[4*i+2] << " "<< p_bboxes[4*i+3] << " " << std::endl;

            if(p_bboxes[4*i+2] < p_bboxes[4*i] || p_bboxes[4*i+3] < p_bboxes[4*i+1])
                continue;

            NvDsInferObjectDetectionInfo object;
            object.classId = (int) p_classes[i];
            object.detectionConfidence = p_scores[i];


            object.left=p_bboxes[4*i+1];
            object.top=p_bboxes[4*i];
            object.width=( p_bboxes[4*i+3] - object.left);
            object.height= ( p_bboxes[4*i+2] - object.top);

            object.left=CLIP(object.left, 0, networkInfo.width - 1);
            object.top=CLIP(object.top, 0, networkInfo.height - 1);
            object.width=CLIP(object.width, 0, networkInfo.width - 1);
            object.height=CLIP(object.height, 0, networkInfo.height - 1);

            objectList.push_back(object);
        }
    }
    return true;
}


/* Check that the custom function has been defined correctly */
CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseCustomNMSTLT);
CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseCustomBatchedNMSTLT);
CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseCustomDDETRTAO);
CHECK_CUSTOM_PARSE_FUNC_PROTOTYPE(NvDsInferParseCustomEfficientDetTAO);
