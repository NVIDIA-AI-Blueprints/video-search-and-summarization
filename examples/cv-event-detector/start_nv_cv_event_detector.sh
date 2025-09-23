#! /bin/bash
######################################################################################################
# SPDX-FileCopyrightText: Copyright (c) 2024-2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: LicenseRef-NvidiaProprietary
#
# NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
# property and proprietary rights in and to this material, related
# documentation and any modifications thereto. Any use, reproduction,
# disclosure or distribution of this material and related documentation
# without an express license agreement from NVIDIA CORPORATION or
# its affiliates is strictly prohibited.
######################################################################################################


start_processes() {

    arch=$(uname -m)
    if [ "$INSTALL_PROPRIETARY_CODECS" = true ]; then
        if ! command -v ffmpeg_for_overlay_video >/dev/null 2>&1; then
            echo "Installing additional multimedia packages"
            bash user_additional_install.sh
        fi
    fi
    mkdir -p /tmp/via-logs/

    mkdir -p /opt/nvidia/deepstream/deepstream/samples/models/Tracker/

    mkdir -p ~/.via/ngc_model_cache/cv_models/triton_model_repo/gdino_trt/1/

    if [ ! -f ~/.via/ngc_model_cache/cv_models/resnet50_market1501.etlt ]; then
        wget 'https://api.ngc.nvidia.com/v2/models/nvidia/tao/reidentificationnet/versions/deployable_v1.0/files/resnet50_market1501.etlt' -P ~/.via/ngc_model_cache/cv_models/
    fi

    if [ ! -f ~/.via/ngc_model_cache/cv_models/resnet50_trafficamnet_rtdetr.onnx ]; then
        echo "Downloading and converting TrafficAMNet (RT-DETR) model"
        curl -L 'https://api.ngc.nvidia.com/v2/models/org/nvidia/team/tao/trafficcamnet_transformer_lite/deployable_v1.0/files?redirect=true&path=resnet50_trafficamnet_rtdetr.onnx' -o resnet50_trafficamnet_rtdetr.onnx
        mv resnet50_trafficamnet_rtdetr.onnx ~/.via/ngc_model_cache/cv_models/resnet50_trafficamnet_rtdetr.onnx
    fi

    if [ ! -f ~/.via/ngc_model_cache/cv_models/resnet50_trafficamnet_rtdetr.onnx_b4_gpu0_fp16.engine ]; then
        echo "Converting TrafficAMNet (RT-DETR) model to engine"
        /usr/src/tensorrt/bin/trtexec --onnx='~/.via/ngc_model_cache/cv_models/resnet50_trafficamnet_rtdetr.onnx' --fp16 --minShapes=inputs:1x3x544x960 --optShapes=inputs:1x3x544x960 --maxShapes=inputs:4x3x544x960 --saveEngine='/tmp/resnet50_trafficamnet_rtdetr.onnx_b4_gpu0_fp16.engine'
        mv /tmp/resnet50_trafficamnet_rtdetr.onnx_b4_gpu0_fp16.engine ~/.via/ngc_model_cache/cv_models/resnet50_trafficamnet_rtdetr.onnx_b4_gpu0_fp16.engine
    fi

    mkdir -p /tmp/via/data/models/
    cp ~/.via/ngc_model_cache/cv_models/resnet50_trafficamnet_rtdetr.onnx /tmp/via/data/models/
    cp ~/.via/ngc_model_cache/cv_models/resnet50_trafficamnet_rtdetr.onnx_b4_gpu0_fp16.engine /tmp/via/data/models/
    make -C tao_post_processor

    cp ~/.via/ngc_model_cache/cv_models/resnet50_market1501.etlt /opt/nvidia/deepstream/deepstream/samples/models/Tracker/

    if [ ! -f ~/.via/ngc_model_cache/cv_models/triton_model_repo/gdino_trt/1/model.plan ]; then
        echo "Downloading and converting Gdino model"
        cp /opt/nvidia/TritonGdino/download_convert_model.sh ~/.via/ngc_model_cache/cv_models/
        cp /opt/nvidia/TritonGdino/update_model.py ~/.via/ngc_model_cache/cv_models/
        cd ~/.via/ngc_model_cache/cv_models && bash download_convert_model.sh
        cd -
    fi

    cp -r ~/.via/ngc_model_cache/cv_models/triton_model_repo/gdino_trt/* /opt/nvidia/TritonGdino/triton_model_repo/gdino_trt/
    
    if [ "$USE_GDINO" = true ]; then
        echo "Starting Triton server for Gdino"
        tritonserver --model-repository=/opt/nvidia/TritonGdino/triton_model_repo &
        nohup tritonserver --model-repository=/opt/nvidia/TritonGdino/triton_model_repo --strict-model-config=false --grpc-infer-allocation-pool-size=16 --exit-on-error=true --http-port 8001 >triton.log 2>&1 &
        triton_pid=$!

        disown

        curl --connect-timeout 1 "http://localhost:8000/" | head -n 3 | grep -v "Failed"
        status=$?
        until [ $status -eq 0 ]; do
            echo "Waiting for triton pipeline"
            curl --connect-timeout 1 "http://localhost:8000/" | head -n 3 | grep -v "Failed"
            status=$?
            sleep 1
        done
    fi


    if [[ "$arch" == "aarch64" ]]; then
        pip3 install  /opt/nvidia/deepstream/deepstream/service-maker/python/pyservicemaker-0.0.1-py3-none-linux_*.whl --force-reinstall --no-deps
    else
        pip3 install /opt/nvidia/deepstream/deepstream/service-maker/python/pyservicemaker-0.0.1-cp312-cp312-linux_x86_64.whl --force-reinstall --no-deps
    fi

    #apt install libx264-164 gstreamer1.0-plugins-ugly --reinstall
    python3 main.py
}

PYTHONPATH=$PYTHONPATH:.


start_processes

