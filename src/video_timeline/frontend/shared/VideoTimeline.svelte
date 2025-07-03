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
	import { onMount, onDestroy } from "svelte";
	import VideoPlayer from "./VideoPlayer.svelte";
	import type { Client } from "@gradio/client";

	export let videoElement: HTMLVideoElement;
	export let markers: { timestamp: number; label: number; video_src: string;
							start_time: number; end_time: number; description: string }[] = [];
	export let loadingTimeline: boolean;
	export let upload: Client["upload"];

	let thumbnails: string[] = [];
	let numberOfThumbnails = 8;
	let intervalId: ReturnType<typeof setInterval> | undefined;
	let videoDuration: number;

	let totalDuration = (markers[markers.length - 1]).end_time; /* Change it to accept totalDuration of original video from API response */

	let selectedMarker: { timestamp: number; label: number; video_src: string;
							start_time: number; end_time: number; description: string} | null = null;

	let hoveredMarker: number | null = null;
	let tooltipPosition = { x: 0, y: 0 };
	let tooltipElement: HTMLDivElement;

	const handleMarkerHover = (event: MouseEvent, index: number) => {
		hoveredMarker = index;

		// Wait for next tick to ensure tooltip is rendered, then position it
		setTimeout(() => {
			if (tooltipElement) {
				const windowWidth = window.innerWidth;
				const tooltipWidth = tooltipElement.offsetWidth;

				// If tooltip would overflow on the right, position it to the left of cursor
				const x = event.clientX + tooltipWidth > windowWidth
					? event.clientX - tooltipWidth - 10
					: event.clientX + 10;
				tooltipPosition = { x, y: event.clientY + 10 };
			}
		}, 0);
	};

	const handleMarkerLeave = () => {
		hoveredMarker = null;
	};

	const handleClickMarker = (marker: { timestamp: number; label: number; video_src: string;
										start_time: number; end_time: number; description: string}): void => {
		console.log("Selected Marker: (should be None) ", selectedMarker);
		console.log("Marker: ", marker);
		selectedMarker = marker;
		console.log("Selected Marker: ", selectedMarker);
		console.log("Total Duration: ", totalDuration);
	};

	const generateThumbnail = (): void => {
		const canvas = document.createElement("canvas");
		const ctx = canvas.getContext("2d");
		if (!ctx) return;

		canvas.width = videoElement.videoWidth;
		canvas.height = videoElement.videoHeight;

		ctx.drawImage(videoElement, 0, 0, canvas.width, canvas.height);

		const thumbnail: string = canvas.toDataURL("image/jpeg", 0.7);
		thumbnails = [...thumbnails, thumbnail];
	};

	onMount(() => {
		const loadMetadata = (): void => {
			videoDuration = videoElement.duration;
			totalDuration = videoDuration;

			const interval = videoDuration / numberOfThumbnails;
			let captures = 0;

			const onSeeked = (): void => {
				generateThumbnail();
				captures++;

				if (captures < numberOfThumbnails) {
					videoElement.currentTime += interval;
				} else {
					videoElement.removeEventListener("seeked", onSeeked);
				}
			};

			videoElement.addEventListener("seeked", onSeeked);
			videoElement.currentTime = 0;
		};

		if (videoElement.readyState >= 1) {
			loadMetadata();
		} else {
			videoElement.addEventListener("loadedmetadata", loadMetadata);
		}
	});

	onDestroy(() => {
		if (intervalId !== undefined) {
			clearInterval(intervalId);
		}
	});

	// Function to handle click on a table row
	const handleTableRowClick = (marker: any) => {
		// Call the handleClickMarker method to open the corresponding video player
		console.log('Marker click: ', marker)
		handleClickMarker(marker);
	};
</script>

<div class="container">
	{#if loadingTimeline}
		<div class="load-wrap">
			<span aria-label="loading timeline" class="loader" />
		</div>
	{:else}
		<div id="timeline" class="thumbnail-wrapper">
			{#each thumbnails as thumbnail, i (i)}
				<img id="thumbnail-image" src={thumbnail} alt={`frame-${i}`} draggable="false" />
			{/each}
			{#each markers as marker, i (i)}
				<div
					class="marker"
					style="left: {(markers[i].start_time / totalDuration) * 100}%"
					on:click={() => handleClickMarker(markers[i])}
					on:mouseenter={(e) => handleMarkerHover(e, i)}
					on:mouseleave={handleMarkerLeave}
					on:keydown={(e) => e.key === 'Enter' && handleClickMarker(markers[i])}
					tabindex="0"
					role="button"
					aria-label="Play marker {i + 1}: {markers[i].label}"
				>
					<svg viewBox="0 0 45 220" fill="none" xmlns="http://www.w3.org/2000/svg">
						<g filter="url(#filter0_d_200_5644)">
						<path d="M41.25 194.042C41.25 204.119 32.5022 212.667 21.625 212.667C10.7478 212.667 3 204.119 3 194.042C3 183.965 10.7478 175.417 21.625 175.417C32.5022 175.417 41.25 183.965 41.25 194.042Z" fill="white"/>
						<path fill-rule="evenodd" clip-rule="evenodd" d="M21.625 211.667C32.0499 211.667 40.25 203.567 40.25 194.042C40.25 184.517 32.0499 176.417 21.625 176.417C11.2001 176.417 3 184.517 3 194.042C3 203.567 11.2001 211.667 21.625 211.667ZM21.625 212.667C32.5022 212.667 41.25 204.119 41.25 194.042C41.25 183.965 32.5022 175.417 21.625 175.417C10.7478 175.417 3 183.965 3 194.042C3 204.119 10.7478 212.667 21.625 212.667Z" fill="#707070"/>
						<path fill-rule="evenodd" clip-rule="evenodd" d="M17.9167 3.24193e-07L25.3333 0L25.3333 175.417L17.9167 175.417L17.9167 3.24193e-07ZM18.9167 174.417L18.9167 1L24.3333 1L24.3333 174.417L18.9167 174.417Z" fill="#707070"/>
						<path fill-rule="evenodd" clip-rule="evenodd" d="M18.9167 175L18.9167 1L24.3333 1L24.3333 175L18.9167 175Z" fill="white"/>
						<path fill-rule="evenodd" clip-rule="evenodd" d="M24.3367 174.67C23.459 174.506 22.5537 174.42 21.6283 174.42C20.703 174.42 19.7977 174.506 18.92 174.67V175.689C19.7953 175.513 20.701 175.42 21.6283 175.42C22.5557 175.42 23.4614 175.513 24.3367 175.689V174.67Z" fill="white"/>
						<text x="50%" y="89%" dominant-baseline="middle" text-anchor="middle" fill="green" font-size="24px" font-weight="bold">{i + 1}</text>
						</g>
						<defs>
							<filter id="filter0_d_200_5644" x="0" y="0" width="45" height="220.667" filterUnits="userSpaceOnUse" color-interpolation-filters="sRGB">
							<feFlood flood-opacity="0" result="BackgroundImageFix"/>
							<feColorMatrix in="SourceAlpha" type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 127 0" result="hardAlpha"/>
							<feOffset dy="4"/>
							<feGaussianBlur stdDeviation="2"/>
							<feComposite in2="hardAlpha" operator="out"/>
							<feColorMatrix type="matrix" values="0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0.25 0"/>
							<feBlend mode="normal" in2="BackgroundImageFix" result="effect1_dropShadow_200_5644"/>
							<feBlend mode="normal" in="SourceGraphic" in2="effect1_dropShadow_200_5644" result="shape"/>
							</filter>
						</defs>
					</svg>
				</div>
			{/each}
		</div>
	{/if}
</div>
{#if selectedMarker}
	<!-- Display VideoPlayer component in modal when a marker is clicked -->
	<div
		class="modal-overlay"
		on:click={() => selectedMarker = null}
		on:keydown={(e) => e.key === 'Escape' && (selectedMarker = null)}
		role="button"
		tabindex="0"
	>
		<div class="modal-content">
			<div class="player-container">
				<VideoPlayer
					src={selectedMarker.video_src}
					subtitle=None
					start_time={selectedMarker.timestamp}
					end_time={(selectedMarker.timestamp + (selectedMarker.end_time - selectedMarker.start_time))}
					autoplay={true}
					loop={true}
					{upload}
					on:play
					on:pause
					on:stop
					on:end
					mirror={false}
					label=None
					interactive={false}
				/>
				<!-- <button on:click={() => selectedMarker = null}>Close</button> -->
			</div>
		</div>
	</div>
{/if}
{#if markers.length > 0}
  <!-- Table to display marker information -->
  <div class="table-container">
	<div class="table-wrapper">
		<table>
		<thead>
			<tr>
			<th>Play</th>
			<th>Marker No.</th>
			<th>Label</th>
			<th>Start Time</th>
			<th>End Time</th>
			<th>Description</th>
			</tr>
		</thead>
		<tbody>
			{#each markers as marker, i}
			<tr class="row" on:click={() => handleTableRowClick(marker)}>
				<td>
					<svg viewBox="0 0 24 24" fill="green" width="24" height="24">
						<path d="M3 22v-20l18 10-18 10z"/>
					</svg>
				</td>
				<td>{i + 1}</td>
				<td>{marker.label}</td>
				<td>{marker.start_time}</td>
				<td>{marker.end_time}</td>
				<td>{marker.description}</td>
			</tr>
			{/each}
		</tbody>
		</table>
	</div>
  </div>
{/if}

<!-- Custom Tooltip -->
{#if hoveredMarker !== null}
	<div
		bind:this={tooltipElement}
		class="tooltip"
		style="left: {tooltipPosition.x}px; top: {tooltipPosition.y}px;"
	>
		{markers[hoveredMarker].label}
	</div>
{/if}

<style>
	.load-wrap {
		display: flex;
		justify-content: center;
		align-items: center;
		height: 100%;
	}
	.loader {
		display: flex;
		position: relative;
		background-color: var(--border-color-accent-subdued);
		animation: shadowPulse 2s linear infinite;
		box-shadow:
			-24px 0 var(--border-color-accent-subdued),
			24px 0 var(--border-color-accent-subdued);
		margin: var(--spacing-md);
		border-radius: 50%;
		width: 10px;
		height: 10px;
		scale: 0.5;
	}

	.tooltip {
		position: fixed;
		background-color: rgba(0, 0, 0, 0.8);
		color: white;
		padding: 4px 8px;
		border-radius: 4px;
		font-size: 12px;
		pointer-events: none;
		z-index: 1001;
		white-space: nowrap;
	}

	@keyframes shadowPulse {
		33% {
			box-shadow:
				-24px 0 var(--border-color-accent-subdued),
				24px 0 #fff;
			background: #fff;
		}
		66% {
			box-shadow:
				-24px 0 #fff,
				24px 0 #fff;
			background: var(--border-color-accent-subdued);
		}
		100% {
			box-shadow:
				-24px 0 #fff,
				24px 0 var(--border-color-accent-subdued);
			background: #fff;
		}
	}

	.container {
		display: flex;
		flex-direction: column;
		align-items: center;
		justify-content: center;
		/* margin: var(--spacing-lg) var(--spacing-lg) 0 var(--spacing-lg); */
		margin: 2% 2% 2% 2%;
	}

	#timeline {
		display: flex;
		height: var(--size-10);
		flex: 1;
		position: relative;
	}

	img {
		flex: 1 1 auto;
		min-width: 0;
		object-fit: cover;
		height: var(--size-25);
		/* border: 1px solid var(--block-border-color); */
		border: 0;
		user-select: none;
		z-index: 1;
	}

	.marker {
		position: absolute;
		top: 0;
		transform: translateX(-40%); /* to shift the middle-y line of marker towards left border of thumbnail*/
		display: flex;
		flex-direction: column;
		align-items: center;
		z-index: 2;
		height: 125%;
	}

	.marker svg {
		height: 100%;
	}

	.marker:hover {
		cursor: pointer;
	}

	.modal-overlay {
		position: fixed;
		top: 0;
		left: 0;
		width: 100%;
		height: 100%;
		background-color: rgba(0, 0, 0, 0.5); /* Semi-transparent black background */
		display: flex;
		justify-content: center;
		align-items: center;
		z-index: 1000; /* Ensure the overlay appears above other content */
	}

	.modal-content {
		height: 50%;
		width: 50%;
		background-color: white;
		padding: 7px;
		border-radius: 10px;
		box-shadow: 0 0 10px rgba(0, 0, 0, 0.3); /* Drop shadow for the modal */
	}

	.player-container {
		width: 100%;
		height: 100%;
		border-radius: 10px;
	}

	.table-container {
		margin-top: 5%;
		padding-left: 5%;
		padding-right: 5%;
		overflow-y: auto;
		max-height: 160px;
		position: relative;
	}

	.table-wrapper {
        max-height: 121px;
        overflow-y: auto;
    }

	table {
		width: 100%;
		border-collapse: collapse;
	}

	th, td {
		border: 1px solid #ddd;
		padding: 3px;
		text-align: left;
	}

	th {
		background-color: #05200d00;
	}

	tr.row {
		height: 20px;
	}

	tr.row:hover {
		background-color: #59b65c;
		cursor: pointer;
	}
</style>
