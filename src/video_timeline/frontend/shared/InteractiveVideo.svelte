<!--
 * SPDX-FileCopyrightText: Copyright (c) 2025 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
 * SPDX-License-Identifier: LicenseRef-NvidiaProprietary
 *
 * NVIDIA CORPORATION, its affiliates and licensors retain all intellectual
 * property and proprietary rights in and to this material, related
 * documentation and any modifications thereto. Any use, reproduction,
 * disclosure or distribution of this material and related documentation
 * without an express license agreement from NVIDIA CORPORATION or
 * its affiliates is strictly prohibited.
-->

<script lang="ts">
	import { createEventDispatcher } from "svelte";
	import { Upload, ModifyUpload } from "@gradio/upload";
	import type { FileData, Client } from "@gradio/client";
	import { BlockLabel } from "@gradio/atoms";
	import { Webcam } from "@gradio/image";
	import { Video } from "@gradio/icons";
	import type { WebcamOptions } from "./utils";
	import { prettyBytes, playable } from "./utils";
	import Player from "./Player.svelte";
	import type { I18nFormatter } from "@gradio/utils";
	import { SelectSource } from "@gradio/atoms";

	export let value: FileData | null = null;
	export let subtitle: FileData | null = null;
	export let timestamps;
	export let marker_labels;
	export let start_times;
	export let end_times;
	export let descriptions;
	export let sources:
		| ["webcam"]
		| ["upload"]
		| ["webcam", "upload"]
		| ["upload", "webcam"] = ["webcam", "upload"];
	export let label: string | undefined = undefined;
	export let show_download_button = false;
	export let show_label = true;
	export let webcam_options: WebcamOptions;
	export let include_audio: boolean;
	export let autoplay: boolean;
	export let root: string;
	export let i18n: I18nFormatter;
	export let active_source: "webcam" | "upload" = "webcam";
	export let handle_reset_value: () => void = () => {};
	export let max_file_size: number | null = null;
	export let upload: Client["upload"];
	export let stream_handler: Client["stream"];
	export let loop: boolean;
	export let uploading = false;

	let has_change_history = false;

	const dispatch = createEventDispatcher<{
		change: FileData | null;
		clear?: never;
		play?: never;
		pause?: never;
		end?: never;
		drag: boolean;
		error: string;
		upload: FileData;
		start_recording?: never;
		stop_recording?: never;
	}>();

	function handle_load({ detail }: CustomEvent<FileData | null>): void {
		value = detail;
		dispatch("change", detail);
		dispatch("upload", detail!);
	}

	function handle_clear(): void {
		value = null;
		dispatch("change", null);
		dispatch("clear");
	}

	function handle_change(video: FileData): void {
		has_change_history = true;
		dispatch("change", video);
	}

	function handle_capture({
		detail
	}: CustomEvent<FileData | any | null>): void {
		dispatch("change", detail);
	}

	let dragging = false;
	$: dispatch("drag", dragging);
</script>

<BlockLabel {show_label} Icon={Video} label={label || "Video"} />
<div data-testid="video" class="video-container">
	{#if value === null || value.url === undefined}
		<div class="upload-container">
			{#if active_source === "upload"}
				<Upload
					bind:dragging
					bind:uploading
					filetype="video/x-m4v,video/*"
					on:load={handle_load}
					{max_file_size}
					on:error={({ detail }) => dispatch("error", detail)}
					{root}
					{upload}
					{stream_handler}
					aria_label={i18n("video.drop_to_upload")}
				>
					<slot />
				</Upload>
			{:else if active_source === "webcam"}
				<Webcam
					{root}
					mirror_webcam={webcam_options.mirror}
					webcam_constraints={webcam_options.constraints}
					{include_audio}
					mode="video"
					on:error
					on:capture={handle_capture}
					on:start_recording
					on:stop_recording
					{i18n}
					{upload}
					stream_every={1}
				/>
			{/if}
		</div>
	{:else if playable()}
		{#key value?.url}
			<Player
				{upload}
				{root}
				interactive
				{autoplay}
				src={value.url}
				subtitle={subtitle?.url}
				timestamps={timestamps}
				marker_labels={marker_labels}
				start_times={start_times}
				end_times={end_times}
				descriptions={descriptions}
				is_stream={false}
				on:play
				on:pause
				on:stop
				on:end
				mirror={webcam_options.mirror && active_source === "webcam"}
				{label}
				{handle_change}
				{handle_reset_value}
				{loop}
				{value}
				{i18n}
				{show_download_button}
				{handle_clear}
				{has_change_history}
			/>
		{/key}
	{:else if value.size}
		<div class="file-name">{value.orig_name || value.url}</div>
		<div class="file-size">
			{prettyBytes(value.size)}
		</div>
	{/if}

	<SelectSource {sources} bind:active_source {handle_clear} />
</div>

<style>
	.file-name {
		padding: var(--size-6);
		font-size: var(--text-xxl);
		word-break: break-all;
	}

	.file-size {
		padding: var(--size-2);
		font-size: var(--text-xl);
	}

	.upload-container {
		height: 100%;
		width: 100%;
	}

	.video-container {
		display: flex;
		height: 100%;
		flex-direction: column;
		justify-content: center;
		align-items: center;
	}
</style>
