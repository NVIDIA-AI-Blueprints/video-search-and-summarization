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
	import { createEventDispatcher, onMount } from "svelte";
	import { Play, Pause, Maximise, Undo } from "@gradio/icons";
	import VideoPartition from "./VideoPartition.svelte";
	import VideoControls from "./VideoControls.svelte";
	import type { FileData, Client } from "@gradio/client";
	import { prepare_files } from "@gradio/client";
	import { format_time } from "@gradio/utils";
	import type { I18nFormatter } from "@gradio/utils";

	export let root = "";
	export let src: string;
	export let subtitle: string | null = null;
	export let mirror: boolean;
	export let autoplay: boolean;
	export let loop: boolean;
	export let label = "test";
	export let interactive = false;
	export let handle_change: (video: FileData) => void = () => {};
	export let handle_reset_value: () => void = () => {};
	export let upload: Client["upload"];
	export let start_time: number | undefined = undefined;
	export let end_time: number | undefined = undefined;

	const dispatch = createEventDispatcher<{
		play: undefined;
		pause: undefined;
		stop: undefined;
		end: undefined;
		clear: undefined;
	}>();

	let time = 0;
	let duration: number;
	let paused = true;
	let video: HTMLVideoElement;
	let processingVideo = false;

    function handleMove(e: TouchEvent | MouseEvent): void {
		if (!duration) return;

		if (e.type === "click") {
			handle_click(e as MouseEvent);
			return;
		}

		if (e.type !== "touchmove" && !((e as MouseEvent).buttons & 1)) return;

		const clientX =
			e.type === "touchmove"
				? (e as TouchEvent).touches[0].clientX
				: (e as MouseEvent).clientX;
		const { left, right } = (
			e.currentTarget as HTMLProgressElement
		).getBoundingClientRect();
		time = (duration * (clientX - left)) / (right - left);
	}

	async function play_pause(): Promise<void> {
		if (document.fullscreenElement != video) {
			const isPlaying =
				video.currentTime > 0 &&
				!video.paused &&
				!video.ended &&
				video.readyState > video.HAVE_CURRENT_DATA;

			if (!isPlaying) {
				await video.play();
			} else video.pause();
		}
	}

	function handle_click(e: MouseEvent): void {
		const { left, right } = (
			e.currentTarget as HTMLProgressElement
		).getBoundingClientRect();
		time = (duration * (e.clientX - left)) / (right - left);
	}

	function format(seconds: number): string {
		if (isNaN(seconds) || !isFinite(seconds)) return "...";

		const minutes = Math.floor(seconds / 60);
		let _seconds: number | string = Math.floor(seconds % 60);
		if (_seconds < 10) _seconds = `0${_seconds}`;

		return `${minutes}:${_seconds}`;
	}

	function handle_end(): void {
		dispatch("stop");
		dispatch("end");
	}

	const handle_trim_video = async (videoBlob: Blob): Promise<void> => {
		let _video_blob = new File([videoBlob], "video.mp4");
		const val = await prepare_files([_video_blob]);
		let value = ((await upload(val, root))?.filter(Boolean) as FileData[])[0];

		handle_change(value);
	};

	function open_full_screen(): void {
		video.requestFullscreen();
	}

	$: time = time || 0;
	$: duration = duration || 0;
</script>

<div class="wrap">
	<div class="mirror-wrap" class:mirror>
		<VideoPartition
			{src}
			preload="auto"
			{autoplay}
			loop={loop}
            start_time={start_time}
            end_time={end_time}
			is_stream={false}
			on:click={play_pause}
			on:play
			on:pause
			on:ended={handle_end}
			bind:currentTime={time}
			bind:duration
			bind:paused
			bind:node={video}
			data-testid={`${label}-player`}
			{processingVideo}
		>
			<track kind="captions" src={subtitle} default />
		</VideoPartition>
	</div>

	<div class="controls">
		<div class="inner">
			<span class="time">{format(time)} / {format(duration)}</span>
		</div>
	</div>
</div>
{#if interactive}
	<VideoControls
		videoElement={video}
		showRedo
		{handle_trim_video}
		{handle_reset_value}
		bind:processingVideo
	/>
{/if}

<style lang="postcss">
	span {
		text-shadow: 0 0 8px rgba(0, 0, 0, 0.5);
	}

	progress {
		margin-right: var(--size-3);
		border-radius: var(--radius-sm);
		width: var(--size-full);
		height: var(--size-2);
	}

	progress::-webkit-progress-bar {
		border-radius: 2px;
		background-color: rgba(255, 255, 255, 0.2);
		overflow: hidden;
	}

	progress::-webkit-progress-value {
		background-color: rgba(255, 255, 255, 0.9);
	}

	.mirror {
		transform: scaleX(-1);
	}

	.mirror-wrap {
		position: relative;
		height: 100%;
		width: 100%;
	}

	.controls {
		position: absolute;
		bottom: 0;
		opacity: 0;
		transition: 500ms;
		margin: var(--size-2);
		border-radius: var(--radius-md);
		background: var(--color-grey-800);
		padding: var(--size-2) var(--size-100);
		width: calc(100% - 17.1rem * 2);
	}
	.wrap:hover .controls {
		opacity: 1;
	}

	.inner {
		display: flex;
		justify-content: space-between;
		align-items: center;
		padding-right: var(--size-2);
		padding-left: var(--size-200);
		width: var(--size-full);
		height: var(--size-full);
	}

	.icon {
		display: flex;
		justify-content: center;
		cursor: pointer;
		width: var(--size-6);
		color: white;
	}

	.time {
		flex-shrink: 0;
		margin-right: var(--size-3);
		margin-left: var(--size-3);
		color: white;
		font-size: var(--text-sm);
		font-family: var(--font-mono);
	}
	.wrap {
		position: relative;
		background-color: var(--background-fill-secondary);
		height: var(--size-full);
		width: var(--size-full);
		border-radius: var(--radius-xl);
	}
	.wrap :global(video) {
		height: var(--size-full);
		width: var(--size-full);
	}
</style>
