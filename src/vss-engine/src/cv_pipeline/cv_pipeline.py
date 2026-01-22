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

import multiprocessing
import os
import queue
import re
import sys
import threading
import time
import uuid
from argparse import ArgumentParser
from pathlib import Path
from threading import Event, Thread
from typing import Callable

import yaml

sys.path.append(os.path.dirname(__file__) + "/..")
from file_splitter import ChunkInfo, FileSplitter  # noqa: E402
from utils import MediaFileInfo, get_json_file_name  # noqa: E402
from via_logger import logger  # noqa: E402
from vlm_pipeline.ngc_model_downloader import (  # noqa: E402
    download_ngc_models_for_cv_pipeline,
    preprocess_3rdparty_models_for_cv_pipeline,
)
from vlm_pipeline.process_base import ViaProcessBase  # noqa: E402

from .cv_metadata_fuser import CVMetadataFuser  # noqa: E402

# Location to download and cache NGC models
NGC_MODEL_CACHE = os.environ.get("NGC_MODEL_CACHE", "") or os.path.expanduser(
    "~/.via/ngc_model_cache/"
)


class RequestInfo:
    def __init__(self) -> None:
        self.request_id = str(uuid.uuid4())
        self.chunk_count = 0
        self.file = ""
        self.aggregation_hint = ""
        self.processed_chunk_list = []
        self.is_summarization = False
        self.is_gsam = False
        self.gsam_output_file = ""
        self.stream_id = ""
        self.query = []
        self.progress = 0
        self.do_aggregation = False
        self.response = None
        self.live_stream_chunk_thread = None
        self.live_stream_splitter: FileSplitter = None
        self.is_live = False
        self.live_stream_chunk_files = []
        self.live_stream_ended = False
        self.num_gpus = 1


class GroundingProcess(ViaProcessBase):
    def __init__(
        self, args, gpu_id, process_id=0, disabled=False, input_queue=None, input_queue_lock=None
    ) -> None:
        super().__init__(
            gpu_id=gpu_id,
            disabled=disabled,
            input_queue=input_queue,
            input_queue_lock=input_queue_lock,
        )
        self._gdino_engine = args.gdino_engine
        self._tracker_config = args.tracker_config
        self._inference_interval = args.inference_interval
        self._gpu_id = int(gpu_id)
        self._process_id = process_id

    def _initialize(self):

        # self._GSAMPipeline = GSAMPipeline

        # from .gsam_model_trt import GroundingDino
        from .gsam_pipeline_trt_ds import cudaSetDevice

        cudaSetDevice(0)
        # self._gdino = GroundingDino(trt_engine=self._gdino_engine, max_text_len=256, batch_size=1)
        # import cupy as cp
        # memory_pool = cp.cuda.MemoryPool(cp.cuda.malloc_managed)
        # Set the memory pool as the default allocator
        # cp.cuda.set_allocator(memory_pool.malloc)
        return True

    def _deinitialize(self):
        self._gdino = None

    def _process(self, chunk: list[ChunkInfo], text_prompts: list, output_file: list, **kwargs):
        if output_file:
            gsam_chunk_output_file = (
                f"{Path(output_file).parent}/{chunk.chunkIdx}_{Path(output_file).name}"
            )
        else:
            gsam_chunk_output_file = ""

        from .gsam_pipeline_trt_ds import GSAMPipeline

        gsam_pipeline = GSAMPipeline(
            #  self._gdino,
            chunk,
            None,
            output_file=gsam_chunk_output_file,
            text_prompts=text_prompts,
            is_rgb=False,
            mask_border_width=3,
            mask_color=[0, 0, 1, 1],
            draw_bbox=False,
            center_text_on_object=True,
            tracker_config=self._tracker_config,
            inference_interval=self._inference_interval,
            request_id=kwargs["request_id"],
            # gpu_id=self._gpu_id,
            batch_size=4,
            buffer_pool_size=20,
        )
        pts_to_frame_num_map, max_object_id = gsam_pipeline.start()

        print(f"grounding pipeline finished for {chunk}")
        gsam_pipeline.stop()
        gsam_pipeline = None

        return {
            "chunk": chunk,
            "pts_to_frame_num_map": pts_to_frame_num_map,
            "max_object_id": max_object_id,
            **kwargs,
        }


class CVPipeline:
    def __init__(self, args) -> None:
        self._request_info_map: dict[str, RequestInfo] = {}
        self._args = args
        self.text_prompts = []
        self._processed_chunk_queue_watcher_thread = None
        self._metadatafusion_thread = None

        print("CV pipeline args: ", args)

        gdino_model_path = (
            os.getenv("GDINO_MODEL_PATH", "")
            or "ngc:nvidia/tao/grounding_dino:grounding_dino_swin_tiny_commercial_deployable_v1.0"
        )
        gdino_model_file_path = ""  # should point to the onnx file

        reid_model_path = self.get_reid_model_path(
            args.tracker_config
        )  # can be ngc or user provided path
        reid_model_file_path = ""  # should point to the onnx file

        sam2_model_path = "3rdparty_meta_sam2_large"
        sam2_model_file_path_dict = self.get_sam2_model_files_paths(args.tracker_config)

        # one folder where all cv models are saved
        cv_models_path = f"{NGC_MODEL_CACHE}/cv_pipeline_models"
        if not os.path.exists(cv_models_path):
            os.makedirs(cv_models_path)

        # Get all the onnx models and generate engine files
        # Download models from NGC
        # Workaround for some asyncio issue
        def download_thread_func(ngc_model_path, download_prefix, model_path_):
            try:
                print("Downloading models for cv pipeline in progress")
                model_path = download_ngc_models_for_cv_pipeline(ngc_model_path, download_prefix)
            except Exception as ex:
                model_path_[1] = ex
                return
            model_path_[0] = model_path

        def preprocess_3rdparty_thread_func(
            ngc_model_path, download_prefix, model_path_, custom_cmd
        ):
            print("Preprocessing 3rd party models for cv pipeline in progress")
            try:
                print("Preprocessing 3rd party models for cv pipeline in progress")
                model_path = preprocess_3rdparty_models_for_cv_pipeline(
                    ngc_model_path, download_prefix, custom_cmd
                )
            except Exception as ex:
                model_path_[1] = ex
                return
            model_path_[0] = model_path

        if gdino_model_path.startswith("ngc:"):
            model_path_ = ["", ""]
            download_thread = Thread(
                target=download_thread_func,
                args=(
                    gdino_model_path[4:],
                    NGC_MODEL_CACHE,
                    model_path_,
                ),
            )
            download_thread.start()
            download_thread.join()
            if model_path_[1]:
                raise model_path_[1] from None
            gdino_model_file_path = (
                f"{model_path_[0]}/grounding_dino_swin_tiny_commercial_deployable.onnx"
            )
        else:
            # check if the user provided path contains swin.onnx
            # if yes, copy it to internal location and use that
            if os.path.exists(gdino_model_path) and gdino_model_path.endswith(".onnx"):
                gdino_model_file_path = gdino_model_path

        if reid_model_path.startswith("ngc:"):
            model_path_ = ["", ""]
            download_thread = Thread(
                target=download_thread_func,
                args=(
                    reid_model_path[4:],
                    NGC_MODEL_CACHE,
                    model_path_,
                ),
            )
            download_thread.start()
            download_thread.join()
            if model_path_[1]:
                raise model_path_[1] from None
            reid_model_file_path = f"{model_path_[0]}/resnet50_market1501_aicity156.onnx"
        else:
            reid_model_file_path = reid_model_path

        logger.info(f"CV pipeline reid_model_file_path: {reid_model_file_path}")

        if True:
            model_type = "large"
            custom_cmd = (
                f"cd /opt/nvidia/via/3rdparty/sam2-onnx-tensorrt/; "
                f'[ ! -d "env_sam" ] && python3 -m virtualenv env_sam; '
                f". env_sam/bin/activate; "
                f"pip3 install -e .; "
                f"cd checkpoints; bash download_ckpts.sh; cd ..; "
                f"mkdir -p checkpoints/{model_type}; "
                f"python3 export_sam2_onnx.py --model {model_type}; "
                f"deactivate; "
                f"mv checkpoints/{model_type}/*.onnx {os.path.join(NGC_MODEL_CACHE, sam2_model_path)}"
            )

            model_path_ = ["", ""]
            download_thread = Thread(
                target=preprocess_3rdparty_thread_func,
                args=(sam2_model_path, NGC_MODEL_CACHE, model_path_, custom_cmd),
            )
            download_thread.start()
            download_thread.join()
            if model_path_[1]:
                raise model_path_[1] from None
            for model_name in ["ImageEncoder", "MaskDecoder", "MemoryEncoder", "MemoryAttention"]:
                if (
                    model_name not in sam2_model_file_path_dict
                    or sam2_model_file_path_dict[model_name] is None
                ):
                    sam2_model_file_path_dict[model_name] = (
                        f"{model_path_[0]}/{re.sub(r'(?<!^)(?=[A-Z])', '_', model_name).lower()}.onnx"
                    )

        gdino_engine_name = os.getenv("DETECTOR_ENGINE_NAME", "") or "swin.fp16.engine"
        # gdino_onnx_name = os.getenv("DETECTOR_ONNX_NAME", "") or "swin.onnx"

        # Engine file creation
        # Run each engine file creation in independent thread
        # Function to run a system command
        def run_command(command):

            # Get the engine file name
            match = re.search(r"--saveEngine=([^\s]+)", command)
            engine_file = match.group(1)
            if not os.path.exists(engine_file):
                os.system(command)
            else:
                if engine_file == f"{cv_models_path}/{gdino_engine_name}":
                    os.system(
                        f"cp {cv_models_path}/{gdino_engine_name}"
                        " /opt/nvidia/TritonGdino/triton_model_repo/gdino_trt/1/model.plan"
                    )
                # BN : TBD
                # result = subprocess.run(command, capture_output=True, text=True)
                # if result.returncode:
                #    raise Exception(f"Error in running command {command}")

        threads = []
        update_gdino_model_script = os.path.join(os.path.dirname(__file__), "update_gdino_model.py")
        engine_creation_commands = [
            (
                f"python3 {update_gdino_model_script} {gdino_model_file_path} /tmp/gdino_model.onnx "
                "&& /usr/src/tensorrt/bin/trtexec "
                f"--onnx=/tmp/gdino_model.onnx "
                "--minShapes=inputs:1x3x544x960,input_ids:1x256,attention_mask:1x256,"
                "position_ids:1x256,token_type_ids:1x256,text_token_mask:1x256x256 "
                "--optShapes=inputs:4x3x544x960,input_ids:4x256,attention_mask:4x256,"
                "position_ids:4x256,token_type_ids:4x256,text_token_mask:4x256x256 "
                "--maxShapes=inputs:8x3x544x960,input_ids:8x256,attention_mask:8x256,"
                "position_ids:8x256,token_type_ids:8x256,text_token_mask:8x256x256 "
                f"--fp16 --saveEngine={cv_models_path}/{gdino_engine_name}"
                f" && cp {cv_models_path}/{gdino_engine_name}"
                " /opt/nvidia/TritonGdino/triton_model_repo/gdino_trt/1/model.plan"
            ),
            f"/usr/src/tensorrt/bin/trtexec \
                --onnx={sam2_model_file_path_dict['ImageEncoder']} --fp16 \
                --saveEngine={cv_models_path}/image_encoder.onnx_b1_gpu0_fp16.engine",
            f"/usr/src/tensorrt/bin/trtexec \
                --onnx={sam2_model_file_path_dict['MaskDecoder']} --fp16 \
                --minShapes=point_coords:1x1x2,point_labels:1x1,image_embed:1x256x64x64 \
                --optShapes=point_coords:1x2x2,point_labels:1x2,image_embed:1x256x64x64 \
                --maxShapes=point_coords:1x2x2,point_labels:1x2,image_embed:1x256x64x64 \
                --saveEngine={cv_models_path}/mask_decoder.onnx_b1_gpu0_fp16.engine",
            f"/usr/src/tensorrt/bin/trtexec \
                --onnx={sam2_model_file_path_dict['MemoryAttention']} --fp16 \
                --minShapes=memory_0:1x1x256,memory_1:1x1x64x64x64,memory_pos_embed:1x4100x64 \
                --optShapes=memory_0:1x16x256,memory_1:1x7x64x64x64,memory_pos_embed:1x28736x64 \
                --maxShapes=memory_0:1x16x256,memory_1:1x7x64x64x64,memory_pos_embed:1x28736x64 \
                --builderOptimizationLevel=5 \
                --saveEngine={cv_models_path}/memory_attention.onnx_b1_gpu0_fp16.engine",
            f"/usr/src/tensorrt/bin/trtexec \
                --onnx={sam2_model_file_path_dict['MemoryEncoder']} --fp16 \
                --minShapes=mask_for_mem:1x1x1024x1024,occ_logit:1x1 \
                --optShapes=mask_for_mem:1x1x1024x1024,occ_logit:1x1 \
                --maxShapes=mask_for_mem:1x1x1024x1024,occ_logit:1x1 \
                --saveEngine={cv_models_path}/memory_encoder.onnx_b1_gpu0_fp16.engine",
            f"/usr/src/tensorrt/bin/trtexec \
            --onnx={reid_model_file_path} \
            --saveEngine={cv_models_path}/resnet50_market1501_aicity156.onnx.engine \
            --minShapes=input:1x3x256x128 --optShapes=input:16x3x256x128  \
            --maxShapes=input:100x3x256x128",
        ]

        # for command in engine_creation_commands:
        #    run_command(command)

        for command in engine_creation_commands:
            thread = threading.Thread(target=run_command, args=(command,))
            thread.start()
            threads.append(thread)

        for thread in threads:
            thread.join()

        self._processed_chunk_queue = multiprocessing.get_context("spawn").Queue()

        args.gdino_engine = f"{cv_models_path}/{gdino_engine_name}"
        # some paths to verify the tracker config file
        # custom_tracker_models_dir = os.environ.get("CV_PIPELINE_TRACKER_MODELS_DIR", "")
        default_reid_engine_path = (
            "/tmp/via/data/models/gdino-sam/resnet50_market1501_aicity156.onnx.engine"
        )

        with open(args.tracker_config) as f:
            input_config = yaml.safe_load(f)

        if input_config.get("Segmenter", {}).get("segmenterConfigPath") is not None:
            segmenter_config_path = input_config["Segmenter"]["segmenterConfigPath"]
            with open(segmenter_config_path) as f:
                segmenter_config = yaml.safe_load(f)
            for model_name, onnx_name in [
                ("ImageEncoder", "image_encoder.onnx"),
                ("MaskDecoder", "mask_decoder.onnx"),
                ("MemoryEncoder", "memory_encoder.onnx"),
                ("MemoryAttention", "memory_attention.onnx"),
            ]:
                if segmenter_config.get(model_name) is not None:
                    model_engine_path = segmenter_config[model_name]["modelEngineFile"]
                    default_model_engine_path = (
                        "/tmp/via/data/models/gdino-sam/" + onnx_name + "_b1_gpu0_fp16.engine"
                    )

                    if model_engine_path == default_model_engine_path:
                        segmenter_config[model_name][
                            "modelEngineFile"
                        ] = f"{cv_models_path}/{onnx_name}_b1_gpu0_fp16.engine"
                    else:
                        if not os.path.exists(model_engine_path):
                            logger.warning(
                                "Model engine file does not exist : "
                                + model_engine_path
                                + " Using default engine file"
                            )
                            segmenter_config[model_name][
                                "modelEngineFile"
                            ] = f"{cv_models_path}/{onnx_name}_b1_gpu0_fp16.engine"

            segmenter_config_path = "/tmp/config_tracker_module_Segmenter.yml"
            with open(segmenter_config_path, "w") as f:
                yaml.dump(segmenter_config, f)
            input_config["Segmenter"]["segmenterConfigPath"] = segmenter_config_path

        if input_config.get("ReID", {}).get("modelEngineFile") is not None:
            reid_engine_path = input_config["ReID"]["modelEngineFile"]
            if reid_engine_path == default_reid_engine_path:
                input_config["ReID"][
                    "modelEngineFile"
                ] = f"{cv_models_path}/resnet50_market1501_aicity156.onnx.engine"
            else:
                if not os.path.exists(reid_engine_path):
                    logger.warning(
                        "ReID engine file does not exist : "
                        + reid_engine_path
                        + " Using default engine file"
                    )
                    input_config["ReID"][
                        "modelEngineFile"
                    ] = f"{cv_models_path}/resnet50_market1501_aicity156.onnx.engine"

        with open("/tmp/via_tracker_config.yml", "w") as f:
            yaml.dump(input_config, f)

        args.tracker_config = "/tmp/via_tracker_config.yml"
        if os.environ.get("GDINO_INFERENCE_INTERVAL"):
            args.inference_interval = int(os.environ.get("GDINO_INFERENCE_INTERVAL"))
        else:
            args.inference_interval = 1

        if (
            os.environ.get("NUM_CV_CHUNKS_PER_GPU")
            and int(os.environ.get("NUM_CV_CHUNKS_PER_GPU")) > 0
        ):
            args.num_chunks_per_gpu = int(os.environ.get("NUM_CV_CHUNKS_PER_GPU"))
        else:
            args.num_chunks_per_gpu = 2

        # Save the gdino engine path and tracker config path
        self._tracker_config = args.tracker_config
        self._gdino_engine = args.gdino_engine

        self._grounding_procs = [
            GroundingProcess(args, gpu_id=i // args.num_chunks_per_gpu, process_id=i)
            for i in range(args.num_chunks)
        ]
        for idx, grounding_proc in enumerate(self._grounding_procs):
            grounding_proc.set_output_queue(self._processed_chunk_queue)
            grounding_proc.set_final_output_queue(self._processed_chunk_queue)
            grounding_proc.start()

        self._metadatafuser = CVMetadataFuser()

        for i in range(args.num_chunks):
            if not self._grounding_procs[i].wait_for_initialization():
                self.stop()
                raise Exception(f"Failed to load Grounding model on GPU {i}")

        self._processed_chunk_queue_watcher_stop_event = Event()
        self._processed_chunk_queue_watcher_thread = Thread(
            target=self._watch_processed_chunk_queue
        )
        self._processed_chunk_queue_watcher_thread.start()

        self._metadatafusion_request_queue = queue.Queue()
        self._metadatafusion_thread_stop_event = Event()
        self._metadatafusion_thread = Thread(target=self._metadatafusion_thread_func)
        self._metadatafusion_thread.start()

        print("CVPipeline initialized!!")

    def stop(self, force=False):
        if force:
            for proc in self._grounding_procs:
                proc.terminate()
            return

        for proc in self._grounding_procs:
            proc.stop()

        if self._processed_chunk_queue_watcher_thread:
            self._processed_chunk_queue_watcher_stop_event.set()
            self._processed_chunk_queue_watcher_thread.join()

        if self._metadatafusion_thread:
            self._metadatafusion_thread_stop_event.set()
            self._metadatafusion_thread.join()

    def get_reid_model_path(self, tracker_config):
        reid_model_path_ngc = "ngc:nvidia/tao/reidentificationnet:deployable_v1.2"
        with open(tracker_config) as f:
            input_config = yaml.safe_load(f)
        if input_config.get("ReID", {}).get("onnxFile") is not None:
            reid_model_path = input_config["ReID"]["onnxFile"]
            if os.path.exists(reid_model_path):
                return reid_model_path
        return reid_model_path_ngc

    def get_sam2_model_files_paths(self, tracker_config):
        sam2_model_file_path_dict = {}
        with open(tracker_config) as f:
            input_config = yaml.safe_load(f)
        if input_config.get("Segmenter", {}).get("segmenterConfigPath") is not None:
            if os.path.exists(input_config["Segmenter"]["segmenterConfigPath"]):
                segmenter_config_path = input_config["Segmenter"]["segmenterConfigPath"]
                with open(segmenter_config_path) as f:
                    segmenter_config = yaml.safe_load(f)
                for model_name in [
                    "ImageEncoder",
                    "MaskDecoder",
                    "MemoryEncoder",
                    "MemoryAttention",
                ]:
                    if segmenter_config.get(model_name, {}).get("onnxFile") is not None:
                        if os.path.exists(segmenter_config[model_name]["onnxFile"]):
                            sam2_model_file_path_dict[model_name] = segmenter_config[model_name][
                                "onnxFile"
                            ]
        return sam2_model_file_path_dict

    def _watch_processed_chunk_queue(self):
        while not self._processed_chunk_queue_watcher_stop_event.is_set():
            try:
                item = self._processed_chunk_queue.get(timeout=1)
            except queue.Empty:
                continue
            request_id = item["request_id"]
            # chunk = item["chunk"]
            req_info = self._request_info_map[request_id]
            req_info.processed_chunk_list.append(item)

            req_info.progress = 90 * len(req_info.processed_chunk_list) / req_info.chunk_count

            if len(req_info.processed_chunk_list) == req_info.chunk_count:
                self._metadatafusion_request_queue.put(req_info)

    def _metadatafusion_thread_func(self):
        while not self._metadatafusion_thread_stop_event.is_set():
            try:
                req_info: RequestInfo = self._metadatafusion_request_queue.get(timeout=1)
            except queue.Empty:
                continue
            cur_time = time.time()
            gsam_pipeline_duration = cur_time - req_info.start_time
            metadata_fusion_start_time = cur_time
            req_info.response = self._perform_metadatafusion(req_info.request_id)
            metadata_fusion_duration = time.time() - metadata_fusion_start_time
            print("CV pipeline performance statistics")
            print(f"gsam_pipeline_duration = {gsam_pipeline_duration}")
            print(f"metadata_fusion_duration = {metadata_fusion_duration}")
            cv_metadata_json_file = get_json_file_name(req_info.request_id, "fused")
            if not os.path.exists(cv_metadata_json_file):
                print(f"Warning : cv_metadata_json_file {cv_metadata_json_file} does not exist.")
                cv_metadata_json_file = ""
            self._on_process_cv_pipeline_done(cv_metadata_json_file)
            req_info.progress = 100

    def _perform_metadatafusion(self, request_id, aggregate=False):
        req_info = self._request_info_map[request_id]

        if req_info.processed_chunk_list:
            req_info.processed_chunk_list.sort(key=lambda item: item["chunk"].chunkIdx)

        if len(req_info.processed_chunk_list) == 0:
            # sess_info.stop_request_processing(req_info)
            return ""

        # get max_chunk_length & chunk_start_frame_numbers
        pts_to_frame_num_list = []
        max_object_id = -1
        for item in req_info.processed_chunk_list:
            pts_to_frame_num_map = item["pts_to_frame_num_map"]
            max_object_id = max(max_object_id, item["max_object_id"])
            pts_to_frame_num_list.append(pts_to_frame_num_map)
        (max_chunk_length, chunk_start_frame_nums) = CVMetadataFuser.get_chunk_info_for_fusion(
            pts_to_frame_num_list
        )
        # BN : TBD : for long videos : chunk 0 processes first few frames twice
        # Hence chunk 0 has entries of first few frames twice
        # Hence this is a hack. Increase max_chunk_length by 10 to accommodate for extra entries
        # Needs to be fixed
        max_chunk_length += 50
        print(max_chunk_length)
        print(chunk_start_frame_nums)
        print(max_object_id)
        self._metadatafuser.fuse_chunks(
            fusion_config=self._args.fusion_config,
            request_id=request_id,
            max_chunk_length=max_chunk_length,
            chunk_start_frame_nums=chunk_start_frame_nums,
            max_object_id=max_object_id,
        )

        req_info.response = req_info.processed_chunk_list
        return req_info.processed_chunk_list

    @staticmethod
    def get_gdino_engine():
        # BN : TBD : Hardcode this for now. Needs to be made more generic
        gdino_engine_name = os.getenv("DETECTOR_ENGINE_NAME", "") or "swin.fp16.engine"
        cv_models_path = f"{NGC_MODEL_CACHE}/cv_pipeline_models"
        return f"{cv_models_path}/{gdino_engine_name}"

    @staticmethod
    def get_tracker_config():
        # BN : TBD : Hardcode this for now. Needs to be made more generic
        return "/tmp/via_tracker_config.yml"

    @staticmethod
    def get_inference_interval():
        return os.getenv("GDINO_INFERENCE_INTERVAL", "") or 1

    def process_cv_pipeline(
        self,
        file,
        on_process_cv_pipeline_done: Callable[[str], None],
        text_prompt: [],
        output_file: None,
        camera_id: str = "",
    ):
        caption = text_prompt
        caption = caption.lower()
        caption = caption.strip()
        if not caption.endswith("."):
            caption = caption + "."
        cat_list = caption.split(" . ")
        cat_list[-1] = cat_list[-1].replace(" .", "")
        self.text_prompts = cat_list
        self.output_file = output_file
        self._on_process_cv_pipeline_done = on_process_cv_pipeline_done
        # BN : TBD : Issue : if overlap between chunks = 0, some frames get dropped
        # hence, as a workaround, use an overlap of 1 sec.
        chunk_overlap_duration = 1
        file_duration = MediaFileInfo.get_info(file).video_duration_nsec / 1e9
        # to make sure number of chunks < self._args.num_chunks, multiply by 1.1
        chunk_size = 1.1 * (file_duration / self._args.num_chunks + chunk_overlap_duration)
        print(f"CV pipeline : chunk_size = {chunk_size}")
        # set the chunk_size to 30 seconds if it's less
        if chunk_size < 30:
            chunk_size = 30
            print("CV pipeline : setting chunk size to 30 seconds")

        # BN : TBD : should we create a new request info or use from upstream?
        req_info = RequestInfo()
        req_info.file = file
        req_info.is_gsam = True
        req_info.gsam_output_file = self.output_file
        req_info.stream_id = str(uuid.uuid4())
        req_info.camera_id = camera_id
        req_info.start_time = time.time()
        self._request_info_map[req_info.request_id] = req_info

        self.chunk_count = 0

        def _on_new_chunk(chunk: ChunkInfo):
            if chunk is None:
                return
            print(f"Processing chunk {chunk}")
            if chunk.chunkIdx >= self._args.num_chunks:
                for proc in self._grounding_procs:
                    proc.terminate()
                raise Exception(f"{chunk.chunkIdx} should be within 0 - {self._args.num_chunks-1}")
            chunk.streamId = req_info.stream_id
            self._grounding_procs[chunk.chunkIdx % self._args.num_chunks].enqueue_chunk(
                chunk,
                chunkIdx=chunk.chunkIdx,
                request_id=req_info.request_id,
                text_prompts=self.text_prompts,
                output_file=self.output_file,
            )
            req_info.chunk_count += 1
            self.chunk_count += 1

        FileSplitter(
            file,
            FileSplitter.SplitMode.SEEK,
            chunk_size,
            lambda chunk: _on_new_chunk(chunk),
            sliding_window_overlap_sec=chunk_overlap_duration,
        ).split()

        return True, req_info.stream_id, req_info.request_id

    def wait(self, request_id):
        req_info = self._request_info_map[request_id]
        while req_info.progress < 100:
            time.sleep(0.1)

    @staticmethod
    def populate_argument_parser(parser: ArgumentParser):
        parser.add_argument(
            "--num-chunks",
            default=1,
            type=int,
            help="Max number of chunks for a video",
        )
        parser.add_argument(
            "--text-prompt",
            default="person . forklift . robot . fire . spill .",
            type=str,
            help="Text prompt for GD",
        )
        parser.add_argument(
            "--output-file",
            type=str,
            default="",
            help="File to write visualized cv metadata output to",
        )
        parser.add_argument(
            "--gdino-engine",
            type=str,
            default="",
            help="Engine file for grounding dino",
        )
        parser.add_argument(
            "--tracker-config",
            type=str,
            default="config/config_tracker_MaskTracker.yml",
            help="Tracker config file",
        )
        parser.add_argument(
            "--fusion-config",
            type=str,
            default="config/MOT_EVAL_config_fusion.yml",
            help="Tracker config file",
        )
        parser.add_argument(
            "--inference-interval",
            default=0,
            type=int,
            help="Inference interval",
        )
        parser.add_argument("file", type=str, help="File to run the CV pipeline on")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="CV Pipeline")
    CVPipeline.populate_argument_parser(parser)
    args = parser.parse_args()

    try:
        pipeline = CVPipeline(args)
    except Exception as ex:
        logger.error("Could not load CV Pipeline - " + str(ex))
        sys.exit(-1)

    def _on_cv_pipeline_done(json_fused_file, in_file):
        print("Finished processing")
        print(f"Input file = {in_file}")
        print(f"output file = {json_fused_file}")

    status, details, req_id = pipeline.process_cv_pipeline(
        args.file,
        lambda json_fused_file, in_file=args.file: _on_cv_pipeline_done(json_fused_file, in_file),
        text_prompt=args.text_prompt,
        output_file=args.output_file,
    )
    if not status:
        print(f"Failed to process file - {args.file}")
        pipeline.stop()
        sys.exit(-1)

    print(f"waiting for req_id {req_id}")
    pipeline.wait(req_id)
    # _, response = pipeline.get_response(req_id)
    # if response:
    #   print_response(response)
    pipeline.stop()
