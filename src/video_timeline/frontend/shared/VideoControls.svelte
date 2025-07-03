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
	import { Undo, Trim, Clear } from "@gradio/icons";
	import VideoTimeline from "./VideoTimeline.svelte";
	import { trimVideo } from "./utils";
	import { FFmpeg } from "@ffmpeg/ffmpeg";
	import loadFfmpeg from "./utils";
	import { onMount } from "svelte";
	import { format_time } from "@gradio/utils";
	import { IconButton } from "@gradio/atoms";
	import { ModifyUpload } from "@gradio/upload";
	import type { FileData } from "@gradio/client";

	export let videoElement: HTMLVideoElement;

	export let showRedo = false;
	export let interactive = true;
	export let mode = "";
	export let handle_reset_value: () => void;
	export let handle_trim_video: (videoBlob: Blob) => void;
	export let processingVideo = false;

	let ffmpeg: FFmpeg;

	onMount(async () => {
		ffmpeg = await loadFfmpeg();
	});

	$: if (mode === "edit" && trimmedDuration === null && videoElement)
		trimmedDuration = videoElement.duration;

	let trimmedDuration: number | null = null;
	let dragStart = 0;
	let dragEnd = 0;

	let loadingTimeline = false;

	const toggleTrimmingMode = (): void => {
		if (mode === "edit") {
			mode = "";
			trimmedDuration = videoElement.duration;
		} else {
			mode = "edit";
		}
	};
</script>

<div class="container" class:hidden={mode !== "edit"}>
	{#if mode === "edit"}
		<div class="timeline-wrapper">
			<VideoTimeline
				{videoElement}
				bind:loadingTimeline
				markers={[]}
			/>
		</div>
	{/if}

	<div class="controls" data-testid="waveform-controls">
		{#if mode === "edit" && trimmedDuration !== null}
			<time
				aria-label="duration of selected region in seconds"
				class:hidden={loadingTimeline}>{format_time(trimmedDuration)}</time
			>
		{:else}
			<div />
		{/if}

		<div class="settings-wrapper">
			{#if showRedo && mode === ""}
				<button
					class="action icon"
					disabled={processingVideo}
					aria-label="Reset video to initial value"
					on:click={() => {
						handle_reset_value();
						mode = "";
					}}
				>
					<Undo />
				</button>
			{/if}

			{#if interactive}
				{#if mode === ""}
					<button
						disabled={processingVideo}
						class="action icon"
						aria-label="Trim video to selection"
						on:click={toggleTrimmingMode}
					>
						<Trim />
					</button>
				{:else}
					<button
						class:hidden={loadingTimeline}
						class="text-button"
						on:click={() => {
							mode = "";
							processingVideo = true;
							trimVideo(ffmpeg, dragStart, dragEnd, videoElement)
								.then((videoBlob) => {
									handle_trim_video(videoBlob);
								})
								.then(() => {
									processingVideo = false;
								});
						}}>Trim</button
					>
					<button
						class="text-button"
						class:hidden={loadingTimeline}
						on:click={toggleTrimmingMode}>Cancel</button
					>
				{/if}
			{/if}
		</div>
	</div>
</div>

<!-- <ModifyUpload
	{i18n}
	on:clear={() => handle_clear()}
	download={show_download_button ? value?.url : null}
>
	{#if showRedo && mode === ""}
		<IconButton
			Icon={Undo}
			label="Reset video to initial value"
			disabled={processingVideo || !has_change_history}
			on:click={() => {
				handle_reset_value();
				mode = "";
			}}
		/>
	{/if}

	{#if interactive && mode === ""}
		<IconButton
			Icon={Trim}
			label="Trim video to selection"
			disabled={processingVideo}
			on:click={toggleTrimmingMode}
		/>
	{/if}
</ModifyUpload> -->

<style>
	.container {
		width: 100%;
	}
	time {
		color: var(--color-accent);
		font-weight: bold;
		padding-left: var(--spacing-xs);
	}

	.timeline-wrapper {
		display: flex;
		align-items: center;
		justify-content: center;
		width: 100%;
		height: 100%;
	}

	.settings-wrapper {
		display: flex;
		justify-self: self-end;
	}

	.text-button {
		border: 1px solid var(--neutral-400);
		border-radius: var(--radius-sm);
		font-weight: 300;
		font-size: var(--size-3);
		text-align: center;
		color: var(--neutral-400);
		height: var(--size-5);
		font-weight: bold;
		padding: 0 5px;
		margin-left: 5px;
	}

	.hidden {
		display: none;
	}

	.text-button:hover,
	.text-button:focus {
		color: var(--color-accent);
		border-color: var(--color-accent);
	}

	.controls {
		display: grid;
		grid-template-columns: 1fr 1fr;
		margin: var(--spacing-lg);
		overflow: hidden;
		text-align: left;
	}

	@media (max-width: 320px) {
		.controls {
			display: flex;
			flex-wrap: wrap;
		}

		.controls * {
			margin: var(--spacing-sm);
		}

		.controls .text-button {
			margin-left: 0;
		}
	}

	.action {
		width: var(--size-5);
		width: var(--size-5);
		color: var(--neutral-400);
		margin-left: var(--spacing-md);
	}

	.action:disabled {
		cursor: not-allowed;
		color: var(--border-color-accent-subdued);
	}

	.action:disabled:hover {
		color: var(--border-color-accent-subdued);
	}

	.icon:hover,
	.icon:focus {
		color: var(--color-accent);
	}

	.container {
		display: flex;
		flex-direction: column;
	}
</style>
