// SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0
//
// Licensed under the Apache License, Version 2.0 (the "License");
// you may not use this file except in compliance with the License.
// You may obtain a copy of the License at
//
// http://www.apache.org/licenses/LICENSE-2.0
//
// Unless required by applicable law or agreed to in writing, software
// distributed under the License is distributed on an "AS IS" BASIS,
// WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
// See the License for the specific language governing permissions and
// limitations under the License.

/**
 * Pipeline Helper Functions for VIA/LVS Jenkins CI
 *
 * This file contains shared helper functions and constants used across the CI pipeline.
 * Load this file in pipeline stages using:
 *   def helpers = load("${env.WORKSPACE}/services/video-summarization/ci/pipeline-helpers.groovy")
 */

import groovy.transform.Field
import com.cloudbees.groovy.cps.NonCPS

// ============================================================================
// Configuration Constants
// ============================================================================

@Field String IMAGE_NAME = "nvcr.io/nv-metropolis-dev/vss-summarization/vss-video-summarization"
@Field String LVS_ROOT = "services/video-summarization"
@Field String NGC_IMAGE_ORG = "nv-metropolis-dev"
@Field String NGC_IMAGE_TEAM = "vss-summarization"
@Field String TEST_RESULTS_NGC_TEAM = "vss-summarization"
@Field String SHARED_RTVI_COMPOSE_PROJECT = "lvs-ci-shared-rtvi"
@Field String SHARED_RTVI_PORT = "8420"
// Setting number of frames to match the max frames per prompt supported by the VLM deployed by devops team
@Field int RTVI_VLM_FRAMES_PER_CHUNK = 5

// Stash names for artifact transfer between stages
@Field String COVERAGE_STASH_NAME = "coverage-xml-report"

// ============================================================================
// Monorepo path helpers (service lives under services/video-summarization/)
// ============================================================================

String lvsPath(String relPath = '') {
    def rel = relPath?.trim()
    if (!rel) {
        return LVS_ROOT
    }
    return "${LVS_ROOT}/${rel}"
}

String lvsWorkspace() {
    return "${env.WORKSPACE}/${LVS_ROOT}"
}

String helpersGroovyPath() {
    return "${env.WORKSPACE}/${lvsPath('ci/pipeline-helpers.groovy')}"
}

def inLvsDir(Closure body) {
    dir(LVS_ROOT) {
        body()
    }
}

String serviceWorkspacePath(String relPath = '') {
    def base = fileExists('docker/Dockerfile') ? env.WORKSPACE : lvsWorkspace()
    def rel = relPath?.trim()
    if (!rel) {
        return base
    }
    return "${base}/${rel}"
}

String resolveHelpersGroovyPath() {
    return fileExists('docker/Dockerfile')
        ? "${env.WORKSPACE}/ci/pipeline-helpers.groovy"
        : helpersGroovyPath()
}

// ============================================================================
// Helper Functions
// ============================================================================

String getEffectiveTagName() {
    return (env.EFFECTIVE_TAG_NAME ?: env.TAG_NAME ?: '').trim()
}

boolean isTagBuild() {
    return getEffectiveTagName() != ''
}

boolean shouldRunBuild() {
    return isTagBuild() || params.RUN_BUILD == 'true'
}

boolean shouldRunUnitTests() {
    return isTagBuild() || params.RUN_UNIT_TESTS == 'true'
}

boolean shouldRunFunctionalTests() {
    return isTagBuild() || params.RUN_FUNCTIONAL_TESTS == 'true'
}

boolean shouldRunIntegrationTests() {
    return isTagBuild() || params.RUN_INTEGRATION_TESTS == 'true'
}

boolean shouldUseComposeImageOnly() {
    return params.INTEGRATION_USE_COMPOSE_IMAGE == 'true' && !isTagBuild()
}

/**
 * Computes the LVS image version from git information.
 * Same naming rule is used for push (current commit) and for pulling the "previous" image (e.g. HEAD^).
 * Format: [git_tag-]short_commit[-arch]
 *
 * @param arch Optional architecture suffix (e.g., "amd64", "arm64-sbsa", "arm64-igpu"). Defaults to empty string.
 * @param ref Optional git ref (e.g. "HEAD^"). When null/empty, uses current HEAD (with MR merge-commit handling). When set, version is computed for that ref: we run "git rev-parse --short=7 ref" and "git describe --tags --exact-match ref" so the result is exactly the version string that would have been used when the pipeline pushed an image for that commit. So the same method drives both "tag we push for this commit" and "tag we pull for the parent commit" when reusing.
 * @return String image version (e.g., "v1.2.3-abc1234-amd64", "abc1234-arm64-sbsa")
 *
 * Why ref='HEAD^' finds the previous LVS image: When CI runs at commit ghi9012, HEAD^ is the parent commit (e.g. def5678). The pipeline that ran for def5678 pushed an image with tag = computeImageVersion(arch) at that run = version for def5678. So computeImageVersion(arch, 'HEAD^') at ghi9012 returns the version for def5678, i.e. the tag already in the registry from the previous run.
 *
 * Concrete example (branch user/feature1 from main at abc0000, LVS src-only changes; we do NOT push re-used images):
 * 1) User adds commits abc1234, def5678; raises MR. CI runs for def5678. Build LVS, push IMAGE_NAME:def5678-amd64.
 * 2) User pushes ghi9012 (LVS src only). runLvsImageReuse() finds ancestor HEAD~1=def5678: image exists, changes def5678..ghi9012 app-only → pull def5678-amd64, tag as ghi9012-amd64 locally, overlay app, run tests; no push.
 * 3) User pushes jkl3456 (LVS src only). runLvsImageReuse() tries HEAD~1=ghi9012: no image in registry (we never pushed). Tries HEAD~2=def5678: image exists, changes def5678..jkl3456 app-only → pull def5678-amd64, tag as jkl3456-amd64 locally, overlay app, run tests; no push. So we resolve to the latest commit that has an image (def5678) without pushing re-used images.
 * For release builds (TAG_NAME set) we never reuse — we always build LVS with current commit so the pushed image tag matches the release.
 */
def computeImageVersion(String arch = '', String ref = null) {
    // Ensure git is installed
    def gitInstalled = sh(script: 'which git', returnStatus: true) == 0
    if (!gitInstalled) {
        echo "Git not found, installing..."
        // Detect package manager and install git
        def hasApt = sh(script: 'which apt-get', returnStatus: true) == 0
        def hasApk = sh(script: 'which apk', returnStatus: true) == 0

        if (hasApt) {
            sh 'apt-get update && apt-get install -y git'
        } else if (hasApk) {
            sh 'apk update && apk add --no-cache git git-lfs'
        } else {
            error("Cannot install git: no supported package manager found (apt-get or apk)")
        }
        echo "Git installed successfully"
    }

    // Configure safe.directory to avoid "dubious ownership" errors in containers
    // This is needed because the workspace may be owned by a different user than the container user
    sh 'git config --global --add safe.directory $(pwd) || true'

    def gitTag
    def gitCommit

    if (ref != null && ref.toString().trim() != '') {
        // Explicit ref (e.g. HEAD^): same naming rule as HEAD, but for that ref. Used for "previous LVS image" tag.
        gitTag = sh(
            script: "git describe --tags --exact-match ${ref} 2>/dev/null || echo ''",
            returnStdout: true
        ).trim()
        gitCommit = sh(
            script: "git rev-parse --short=7 ${ref}",
            returnStdout: true
        ).trim()
    } else {
        // Current HEAD (with release/MR handling)
        // Use TAG_NAME (set by Jenkins when a tag triggers the build) as the authoritative
        // source for the git tag. This is correct even when multiple tags point to the same
        // commit (git describe --tags --exact-match picks arbitrarily among them).
        gitTag = (env.TAG_NAME ?: '').trim()
        def isMergeCommit = sh(
            script: 'git rev-parse --verify HEAD^2 >/dev/null 2>&1 && echo "true" || echo "false"',
            returnStdout: true
        ).trim()
        if (isMergeCommit == 'true') {
            gitCommit = sh(script: 'git rev-parse --short=7 HEAD^1', returnStdout: true).trim()
            echo "MR pipeline detected: using MR head commit (HEAD^1) for versioning"
        } else {
            gitCommit = sh(script: 'git rev-parse --short=7 HEAD', returnStdout: true).trim()
        }
    }

    // Build version string using hyphens
    def versionParts = []

    if (gitTag && gitTag != '') {
        versionParts.add(gitTag)
    }

    versionParts.add(gitCommit)

    if (arch && arch.trim() != '') {
        versionParts.add(arch)
    }

    def version = versionParts.join('-')
    echo "Computed image version: ${version} (tag=${gitTag ?: 'none'}, commit=${gitCommit}, arch=${arch ?: 'none'}, ref=${ref ?: 'HEAD'})"
    return version
}

/**
 * Constructs a full image tag (IMAGE_NAME:version) for a given architecture.
 * This is a convenience function that combines IMAGE_NAME with computeImageVersion().
 *
 * NOTE: This function is used ONLY for LVS/VIA application images, NOT for base images.
 * Base image tags are calculated separately by ci/scripts/get_base_docker_img.sh.
 *
 * @param arch Optional architecture suffix (e.g., "amd64", "arm64-sbsa", "arm64-igpu"). Defaults to empty string.
 * @return String full image tag (e.g., "nvcr.io/org/repo:v1.2.3-abc1234-amd64")
 */
def getImageTag(String arch = '') {
    def version = computeImageVersion(arch)
    return "${IMAGE_NAME}:${version}"
}

/**
 * Splits a Docker image reference into repository and tag parts.
 *
 * @param imageTag Full image tag, e.g. nvcr.io/org/team/image:abc1234-amd64
 * @return Map with repository and tag keys
 */
def splitImageTag(String imageTag) {
    def trimmed = imageTag?.trim()
    if (!trimmed) {
        error('Image tag is required')
    }
    if (!(trimmed ==~ /^[A-Za-z0-9._:\/-]+$/)) {
        error("Image reference contains unsafe characters: ${trimmed}")
    }
    if (trimmed.contains('@')) {
        error("Digest image references are not supported for standalone arch derivation: ${trimmed}")
    }

    def lastSlash = trimmed.lastIndexOf('/')
    def tagSeparator = trimmed.lastIndexOf(':')
    if (tagSeparator <= lastSlash || tagSeparator == trimmed.length() - 1) {
        error("Image reference must include an explicit tag: ${trimmed}")
    }

    return [
        repository: trimmed.substring(0, tagSeparator),
        tag: trimmed.substring(tagSeparator + 1),
    ]
}

/**
 * Returns the version tag component from a full Docker image tag.
 *
 * @param imageTag Full image tag, e.g. nvcr.io/org/team/image:abc1234-amd64
 * @return String tag/version component, e.g. abc1234-amd64
 */
def getImageVersionFromTag(String imageTag) {
    return splitImageTag(imageTag).tag
}

/**
 * Derives an arch-specific LVS image tag from a full image ref or bare version.
 * If imageRefOrVersion is nvcr.io/.../image:3672fd1-amd64, then targetArch
 * arm64-sbsa resolves to nvcr.io/.../image:3672fd1-arm64-sbsa.
 * If imageRefOrVersion is a bare version, IMAGE_NAME is used when returnFullRef is true.
 *
 * @param imageRefOrVersion Full image ref or bare version, e.g. nvcr.io/.../image:3672fd1-amd64 or 3672fd1-amd64
 * @param targetArch Target architecture suffix, e.g. amd64, arm64-sbsa, or arm64-igpu
 * @param options Optional map: returnFullRef (default true), repository (default IMAGE_NAME for bare versions)
 * @return Full image ref or bare version for the requested architecture
 */
def resolveImageTagForArch(String imageRefOrVersion, String targetArch, Map options = [:]) {
    def arch = targetArch?.trim()
    if (!(arch in ['amd64', 'arm64-sbsa', 'arm64-igpu'])) {
        error("Unsupported image architecture '${targetArch}'")
    }

    def input = imageRefOrVersion?.trim()
    if (!input) {
        error('Image tag/version is required')
    }
    if (!(input ==~ /^[A-Za-z0-9._:\/-]+$/)) {
        error("Image reference contains unsafe characters: ${input}")
    }
    if (input.contains('@')) {
        error("Digest image references are not supported for arch derivation: ${input}")
    }

    def lastSlash = input.lastIndexOf('/')
    def tagSeparator = input.lastIndexOf(':')
    def hasFullRef = tagSeparator > lastSlash && tagSeparator < input.length() - 1
    if (!hasFullRef && input.contains('/')) {
        error("Image reference must include an explicit tag: ${input}")
    }

    def repository = (options.repository ?: IMAGE_NAME).toString()
    def version = input
    if (hasFullRef) {
        def parsed = splitImageTag(input)
        repository = parsed.repository
        version = parsed.tag
    }

    def archSuffixPattern = /-(amd64|arm64-sbsa|arm64-igpu|arm64)$/
    def hasArchSuffix = version ==~ /.*${archSuffixPattern}/
    if (arch == 'amd64' && !hasArchSuffix) {
        def returnFullRef = options.containsKey('returnFullRef') ? options.returnFullRef.toString().toBoolean() : true
        def resolved = returnFullRef ? "${repository}:${version}" : version
        echo "Using provided amd64 image tag as-is: ${resolved}"
        return resolved
    }

    def baseVersion = version.replaceFirst(archSuffixPattern, '')
    def resolvedVersion = "${baseVersion}-${arch}"
    def returnFullRef = options.containsKey('returnFullRef') ? options.returnFullRef.toString().toBoolean() : true
    def resolved = returnFullRef ? "${repository}:${resolvedVersion}" : resolvedVersion
    echo "Resolved ${arch} image tag: ${resolved}"
    return resolved
}

/**
 * Returns true if the current HEAD is a merge commit (has two parents), e.g. after an MR is merged.
 * Used to automatically skip LVS reuse on post-merge builds so the merged state gets a fresh image.
 */
def isMergeCommit() {
    def exitCode = sh(script: 'git rev-parse --verify HEAD^2 >/dev/null 2>&1', returnStatus: true)
    return (exitCode == 0)
}

/**
 * Returns the full LVS image path (name:version) that would have been built/pushed for the given ref.
 * Uses the same computeImageVersion(arch, ref) as the push path, so the path matches what the pipeline pushed for that commit.
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @param ref Git ref (e.g. 'HEAD^' for parent commit). That image is expected to exist when the previous pipeline run on this branch completed.
 * @return String full image path (e.g. IMAGE_NAME:def5678-amd64)
 */
def getLvsImageFullTagForRef(String arch, String ref = 'HEAD^') {
    def version = computeImageVersion(arch, ref)
    return "${IMAGE_NAME}:${version}"
}

/**
 * Returns the full LVS image path for the given ref if that image exists in the registry (manifest inspect, no pull); otherwise null.
 * Callers can use the returned path without calling getLvsImageFullTagForRef again.
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @param ref Git ref (e.g. 'HEAD~1')
 * @param ngcApiKey NGC API key for authentication
 * @return String full image path (e.g. IMAGE_NAME:def5678-amd64) if exists in registry, null otherwise
 */
def getLvsImagePathInRegistryForRef(String arch, String ref, String ngcApiKey) {
    loginToNvcr(ngcApiKey)
    def imagePath = getLvsImageFullTagForRef(arch, ref)
    def exitCode = sh(
        script: "docker manifest inspect ${imagePath}",
        returnStatus: true
    )
    def exists = (exitCode == 0)
    echo "getLvsImagePathInRegistryForRef(arch=${arch}, ref=${ref}): ${exists ? imagePath : 'null'}"
    return exists ? imagePath : null
}

/**
 * Finds the closest ref (HEAD~0, HEAD~1, HEAD~2, ...) that has an LVS image in the registry and for which
 * changes from that ref to HEAD are app-only. Returns the full image path so callers can pull without
 * resolving again. HEAD~0 (current commit) is tried first so a CI re-run on the same commit can reuse
 * the image from a previous run. E.g. jkl3456 can resolve to def5678's image (skip ghi9012 which was never pushed).
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @param ngcApiKey NGC API key for authentication
 * @param maxAncestors Max number of refs to try from HEAD~0 (default 29)
 * @return String full LVS image path (e.g. IMAGE_NAME:def5678-amd64), or null if none found
 */
def findReusableLvsImage(String arch, String ngcApiKey, int maxAncestors = 29) {
    for (int n = 0; n < maxAncestors; n++) {
        def ref = "HEAD~${n}"
        def refExists = sh(
            script: "git rev-parse --verify ${ref}",
            returnStatus: true
        )
        if (refExists != 0) {
            echo "findReusableLvsImage: ${ref} is not a valid ref, stopping"
            return null
        }
        def imagePath = getLvsImagePathInRegistryForRef(arch, ref, ngcApiKey)
        if (imagePath == null) {
            echo "findReusableLvsImage: no image in registry for ${ref}, trying next ancestor"
            continue
        }
        if (!canReuseLvsImage(ref)) {
            echo "findReusableLvsImage: changes from ${ref} to HEAD are not app-only, trying next ancestor"
            continue
        }
        echo "findReusableLvsImage: found reusable image ${imagePath} (ref=${ref})"
        return imagePath
    }
    echo "findReusableLvsImage: no reusable ancestor in the last ${maxAncestors} commits"
    return null
}

/**
 * Returns true if there is a reusable LVS image (ancestor with image in registry and app-only changes to HEAD).
 * When true, sets env.LVS_REUSE_IMAGE to the full image path so runLvsImageReuse() can use it without resolving again.
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @param ngcApiKey NGC API key for authentication
 * @return boolean true if a reusable image was found and env.LVS_REUSE_IMAGE was set
 */
def hasReusableLvsImage(String arch, String ngcApiKey) {
    def imagePath = findReusableLvsImage(arch, ngcApiKey)
    if (imagePath != null) {
        env.LVS_REUSE_IMAGE = imagePath
        echo "hasReusableLvsImage: set env.LVS_REUSE_IMAGE=${imagePath}"
        return true
    }
    return false
}

/**
 * Checks whether changes since ref are only under app-only paths (e.g. src/).
 * When true, CI can reuse the previous LVS image and overlay current app code.
 *
 * @param ref Git ref to compare against HEAD (default: HEAD^). Uses ref..HEAD for the diff.
 * @return boolean true if only app-only paths changed
 */
def canReuseLvsImage(String ref = 'HEAD^') {
    def scriptPath = lvsPath('ci/scripts/lvs_app_only_changes.py')
    if (!fileExists(scriptPath)) {
        echo "lvs_app_only_changes.py not found at ${scriptPath}, assuming LVS rebuild required"
        return false
    }
    def result = sh(
        script: "python3 ${scriptPath} --ref '${ref}' --quiet 2>/dev/null | tail -1",
        returnStdout: true
    ).trim()
    def canReuse = (result == 'true')
    echo "canReuseLvsImage(ref=${ref}): ${canReuse}"
    return canReuse
}

/**
 * Returns true when any tracked file under services/video-summarization changed
 * between ref and HEAD. Changes elsewhere in the monorepo are ignored.
 */
def hasLvsServiceChanges(String ref = 'HEAD^') {
    def scriptPath = lvsPath('ci/scripts/lvs_app_only_changes.py')
    if (!fileExists(scriptPath)) {
        echo "lvs_app_only_changes.py not found at ${scriptPath}, assuming service changes exist"
        return true
    }
    def result = sh(
        script: "python3 ${scriptPath} --mode any --ref '${ref}' --quiet 2>/dev/null | tail -1",
        returnStdout: true
    ).trim()
    def hasChanges = (result == 'true')
    echo "hasLvsServiceChanges(ref=${ref}): ${hasChanges}"
    return hasChanges
}

/**
 * Sets env.LVS_PIPELINE_ACTIVE from service-path changes vs ref, unless forceActive.
 * Used to skip the monorepo pipeline when a commit only touches paths outside
 * services/video-summarization.
 *
 * @param opts.ref Git ref to compare against HEAD (default: HEAD^)
 * @param opts.forceActive When true, always activate (tag builds, standalone test mode)
 * @return boolean true when downstream stages should run
 */
def evaluateLvsPipelineActive(Map opts = [:]) {
    if (opts.forceActive == true) {
        env.LVS_PIPELINE_ACTIVE = 'true'
        echo 'LVS pipeline: forced active (tag build or standalone test mode)'
        return true
    }
    def ref = (opts.ref ?: 'HEAD^').toString()
    def active = hasLvsServiceChanges(ref)
    env.LVS_PIPELINE_ACTIVE = active ? 'true' : 'false'
    if (!active) {
        currentBuild.description = 'Skipped: no changes under services/video-summarization'
        echo "No video-summarization changes since ${ref}; downstream stages will be skipped."
    } else {
        echo "Video-summarization changes detected since ${ref}; running full pipeline."
    }
    return active
}

/**
 * Returns the NGCR base image name/tag for the given architecture.
 * Computed by ci/scripts/get_base_docker_img.sh (CI only; local builds use via-engine-base).
 * BUILD_PLATFORM and ARM_PLATFORM are passed explicitly for consistency across environments.
 * This name is used for check existence (manifest inspect), pull, tag, and push.
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @return String base image name (e.g. nvcr.io/.../via-engine-base:abc1234-amd64)
 */
def getBaseImageName(String arch) {
    def archInfo = parseArchitecture(arch)
    def baseArch = archInfo.arch
    def armPlatform = archInfo.platform ?: 'sbsa'
    def envVars = "BUILD_PLATFORM=${baseArch}"
    if (baseArch == 'arm64' && armPlatform) {
        envVars += " ARM_PLATFORM=${armPlatform}"
    }
    def name = sh(
        script: "${envVars} bash ${lvsPath('ci/scripts/get_base_docker_img.sh')}",
        returnStdout: true
    ).trim()
    return name
}

/**
 * Prepares a staging directory with current app code (same layout as /opt/nvidia/via).
 * Used only in the LVS reuse path: runLvsImageReuse() copies this into the reused container and commits,
 * so the image has current app code and no host mount is needed.
 *
 * @return String absolute path to the overlay directory
 */
def prepareAppOverlayDir() {
    def overlayDir
    inLvsDir {
        overlayDir = sh(
            script: 'mktemp -d',
            returnStdout: true
        ).trim()
        sh """
            bash docker/copy_sources.sh src/ ${overlayDir}
            bash docker/copy_configs.sh config/ ${overlayDir}
        """
    }
    // In reuse path this dir is copied into the container and committed (see runLvsImageReuse). Image keeps its start_via.sh, VERSION, config/.
    echo "App overlay prepared at: ${overlayDir}"
    return overlayDir
}

/**
 * Detects the host architecture and maps it to LVS/VIA arch tags.
 *
 * @return String arch tag (e.g., "amd64", "arm64-sbsa", "arm64-igpu") or empty string if unknown
 */
def getHostArchTag() {
    def uname = sh(script: "uname -m", returnStdout: true).trim()
    def archTag = ''

    switch (uname) {
        case 'x86_64':
            archTag = 'amd64'
            break
        case 'aarch64':
        case 'arm64':
            archTag = 'arm64-sbsa'
            break
        default:
            archTag = ''
            break
    }

    // Allow override for ARM platform variants
    if (archTag.startsWith('arm64') && env.ARM_PLATFORM) {
        def armPlatform = env.ARM_PLATFORM.trim().toLowerCase()
        if (armPlatform == 'igpu') {
            archTag = 'arm64-igpu'
        } else if (armPlatform == 'sbsa') {
            archTag = 'arm64-sbsa'
        }
    }

    echo "Detected host arch: ${uname} -> ${archTag ?: 'none'} (ARM_PLATFORM=${env.ARM_PLATFORM ?: 'unset'})"
    return archTag
}

/**
 * Resolves the built image tag for the host when RUN_BUILD is enabled.
 *
 * @param runBuild Whether to use the built image tag
 * @return String image tag or null to use docker-compose.yml default
 */
def resolveBuiltImageTag(boolean runBuild) {
    if (!runBuild) {
        echo "RUN_BUILD=false, using hardcoded image from docker-compose.yml"
        return null
    }

    def hostArch = getHostArchTag()
    def imageTag = getImageTag(hostArch)
    echo "Will replace docker-compose.yml image with built image: ${imageTag}"
    return imageTag
}

/**
 * Gets the LVS image to test - either from user parameter or computes latest.
 *
 * @param userProvidedImage Optional user-provided image URL. If provided and not 'auto', returns it directly.
 * @return String full image tag to test
 */
def getLatestImage(String userProvidedImage = '') {
    // If user provided a specific image (not 'auto' or empty), use it
    if (userProvidedImage &&
        userProvidedImage.trim() != '' &&
        userProvidedImage.trim().toLowerCase() != 'auto') {
        echo "Using user-provided image: ${userProvidedImage}"
        return userProvidedImage.trim()
    }

    // Otherwise, compute the latest image tag, eg 1) assume from current git commit or 2) NGC registry latest
    // 1. from current git commit, this might return non-existent image
    def imageTag = getImageTag()
    echo "Latest computed image tag from git: ${imageTag}"
    return imageTag

    // 2. TODO: from NGC registry latest, this might return a valid image
}

/**
 * Parses the combined architecture string into base arch and platform suffix.
 * Examples: "amd64" -> [arch: "amd64", platform: ""]
 *           "arm64-sbsa" -> [arch: "arm64", platform: "sbsa"]
 *           "arm64-igpu" -> [arch: "arm64", platform: "igpu"]
 *
 * @param combinedArch Combined architecture string (e.g., "amd64", "arm64-sbsa", "arm64-igpu")
 * @return Map with 'arch' and 'platform' keys
 */
def parseArchitecture(String combinedArch) {
    if (combinedArch == null || combinedArch.trim().isEmpty()) {
        error("parseArchitecture: combinedArch parameter cannot be null or empty")
    }

    def parts = combinedArch.split('-')
    def baseArch = parts[0]
    def platform = parts.size() > 1 ? parts[1..-1].join('-') : ''

    echo "Parsed architecture: '${combinedArch}' -> arch='${baseArch}', platform='${platform}'"

    return [arch: baseArch, platform: platform]
}

/**
 * Detects the available package manager on the system.
 *
 * @return String - 'apt' for apt-get systems, 'apk' for Alpine systems
 * @throws Exception if no supported package manager is found
 */
def getPackageManager() {
    def hasApt = sh(script: 'which apt-get', returnStatus: true) == 0
    def hasApk = sh(script: 'which apk', returnStatus: true) == 0

    if (hasApt) {
        return 'apt'
    } else if (hasApk) {
        return 'apk'
    } else {
        error("getPackageManager: no supported package manager found (apt-get or apk)")
    }
}

/**
 * Configures git and optionally installs git-lfs.
 * Works with both apt-based and apk-based systems.
 * Sets git safe.directory and core.abbrev. When installLfs is true (default),
 * downloads a pinned git-lfs version from GitHub releases to /usr/local/bin and
 * registers LFS hooks via "git lfs install". The pinned version bypasses the
 * system package manager (e.g. Ubuntu jammy ships 3.0.2 which ignores
 * GIT_LFS_SKIP_SMUDGE during checkout-index; fixed in 3.1.0).
 * See: https://github.com/git-lfs/git-lfs/issues/4858
 *
 * Pass installLfs: false for checkouts that do not need LFS objects (e.g. bare
 * metal perf nodes) to skip git-lfs entirely and avoid any LFS filter activity
 * during checkout.
 *
 * @param installLfs Whether to install git-lfs and register LFS hooks (default: true)
 * @return void
 */
def configureGit(boolean installLfs = true) {
    def pkgMgr = getPackageManager()

    // Install git if not already present
    def gitExists = sh(script: 'which git', returnStatus: true) == 0
    if (!gitExists) {
        echo "git not found, installing..."
        if (pkgMgr == 'apt') {
            sh '(sudo apt-get update && sudo apt-get install -y git) || (apt-get update && apt-get install -y git)'
        } else {
            sh 'apk update && apk add --no-cache git'
        }
    } else {
        echo "git already installed"
    }

    // Ensure curl exists before downloading pinned git-lfs.
    if (installLfs) {
        def curlExists = sh(script: 'which curl', returnStatus: true) == 0
        if (!curlExists) {
            echo "curl not found, installing..."
            if (pkgMgr == 'apt') {
                sh '(sudo apt-get update && sudo apt-get install -y curl ca-certificates) || (apt-get update && apt-get install -y curl ca-certificates)'
            } else {
                sh 'apk update && apk add --no-cache curl ca-certificates'
            }
        } else {
            echo "curl already installed"
        }
    }

    // Always install the pinned git-lfs version from GitHub releases when LFS is needed.
    // apt/apk repos (e.g. Ubuntu jammy ships 3.0.2) cannot be relied upon to provide a
    // version that fixes GIT_LFS_SKIP_SMUDGE being ignored during checkout-index (fixed
    // in 3.1.0, see github.com/git-lfs/git-lfs/issues/4858), so we bypass the package
    // manager entirely and install a known-good binary to /usr/local/bin.
    if (installLfs) {
        sh '''
            GIT_LFS_VERSION="3.7.1"
            ARCH=$(uname -m)
            case "${ARCH}" in
                x86_64)  LFS_ARCH="amd64" ;;
                aarch64) LFS_ARCH="arm64" ;;
                *)       echo "Unsupported arch for git-lfs install: ${ARCH}"; exit 1 ;;
            esac
            echo "Installing git-lfs ${GIT_LFS_VERSION} (${LFS_ARCH})..."
            TMPDIR=$(mktemp -d)
            curl -fsSL "https://github.com/git-lfs/git-lfs/releases/download/v${GIT_LFS_VERSION}/git-lfs-linux-${LFS_ARCH}-v${GIT_LFS_VERSION}.tar.gz" \
                | tar -xz -C "${TMPDIR}"
            GIT_LFS_BIN=$(find "${TMPDIR}" -type f -name 'git-lfs' | head -1)
            install -m 755 "${GIT_LFS_BIN}" /usr/local/bin/git-lfs 2>/dev/null || \
                sudo install -m 755 "${GIT_LFS_BIN}" /usr/local/bin/git-lfs
            rm -rf "${TMPDIR}"
            echo "git-lfs installed: $(git lfs version)"
        '''
    }

    // Configure git
    sh '''
        git config --global --add safe.directory $(pwd) || true
        git config --global core.abbrev 7 || true
    '''

    if (installLfs) {
        sh '''
            # Jenkins sets core.hooksPath to NUL (Windows) or /dev/null (Linux) during checkout
            # to disable Git hooks. However, this conflicts with git-lfs which needs to install
            # its own hooks for LFS functionality. We must unset core.hooksPath before running
            # git lfs install to allow it to create hook files in the proper location.
            # Without this, git-lfs can fail with "mkdir /dev/null: not a directory" error.
            # See: https://github.com/git-lfs/git-lfs/pull/5177
            git config --unset core.hooksPath || true
            git lfs install || true
        '''
    }
}

/**
 * After a normal gitCheckout(), resolve the git ref embedded in a standalone
 * image tag and checkout to that exact commit.
 *
 * Supported tag formats (anything after the last ':'):
 *   v1.2.3-abc1234-amd64   → checkout SHA abc1234
 *   v1.2.3-abc1234         → checkout SHA abc1234
 *   abc1234-amd64          → checkout SHA abc1234
 *   v1.2.3-amd64           → checkout tag  v1.2.3  (no SHA present)
 *   v1.2.3                 → checkout tag  v1.2.3
 *
 * @param imageTag Full image tag, e.g. nvcr.io/.../image:v1.2.3-abc1234-amd64
 */
def checkoutForStandaloneTest(String imageTag) {
    // Extract the version string after the last ':'
    def tagVersion = imageTag.tokenize(':').last()

    // Strip known arch suffixes
    tagVersion = tagVersion.replaceAll(/-(amd64|arm64-sbsa|arm64-igpu)$/, '')

    // Split by both '-' and '.' to find SHA in any position (e.g. rc3.6b8de38)
    def allTokens = tagVersion.tokenize('-.')
    def commitSha = null
    def versionTag = null

    // SHA: last atomic token that is 7–40 lowercase hex characters
    for (def token : allTokens.reverse()) {
        if (token ==~ /^[0-9a-f]{7,40}$/) {
            commitSha = token
            break
        }
    }

    // Version tag: everything before the SHA (strip trailing delimiter).
    // Supports both 'v1.2.3-rc1' and '3.1.0-rc3' (with or without 'v' prefix).
    if (commitSha) {
        def shaIdx = tagVersion.lastIndexOf(commitSha)
        if (shaIdx > 0) {
            versionTag = tagVersion[0..(shaIdx - 2)]
            // Remove any trailing delimiters from version tag
            versionTag = versionTag.replaceAll(/[-\.]+$/, '')
        }
    } else {
        versionTag = tagVersion
    }

    // Validate: must start with optional 'v' then digit.digit (semver-like)
    if (versionTag && !(versionTag ==~ /^v?\d+\.\d+.*/)) {
        versionTag = null
    }

    def ref = commitSha ?: versionTag
    if (!ref) {
        error("Could not extract a git ref from image tag '${imageTag}'. " +
              "Parsed: tagVersion='${tagVersion}', allTokens=${allTokens}, commitSha=${commitSha}, versionTag=${versionTag}. " +
              "Expected format: registry/image:v1.2.3-abc1234-amd64 or similar.")
    }

    // Format the ref: prefix version tags so GitSCM resolves them unambiguously
    def gitRef = versionTag && !commitSha ? "refs/tags/${ref}" : ref
    echo "Image tag '${imageTag}' → resolved git ref: ${gitRef}"

    configureGit()
    withEnv([
        'GIT_LFS_SKIP_SMUDGE=1',
        'GIT_CONFIG_COUNT=4',
        'GIT_CONFIG_KEY_0=filter.lfs.process',
        'GIT_CONFIG_VALUE_0=git-lfs filter-process --skip',
        'GIT_CONFIG_KEY_1=filter.lfs.smudge',
        'GIT_CONFIG_VALUE_1=git-lfs smudge --skip -- %f',
        'GIT_CONFIG_KEY_2=filter.lfs.required',
        'GIT_CONFIG_VALUE_2=false',
        'GIT_CONFIG_KEY_3=filter.lfs.clean',
        'GIT_CONFIG_VALUE_3=git-lfs clean -- %f'
    ]) {
        if (commitSha) {
            // gitCheckout() has already fetched all branch refs (non-shallow), so the commit
            // is already present in the workspace. A plain 'git checkout <sha>' avoids the
            // GitSCM plugin trying to resolve the SHA as a branch name (origin/<sha>), which
            // fails with "Couldn't find any revision to build".
            sh "git checkout ${commitSha}"
        } else {
            // Version-tag checkout: use GitSCM so Jenkins credentials are applied during fetch.
            def remoteCfg = scm.userRemoteConfigs.collect { cfg ->
                def m = [url: cfg.url]
                if (cfg.credentialsId) { m.credentialsId = cfg.credentialsId }
                return m
            }
            checkout([
                $class: 'GitSCM',
                branches: [[name: gitRef]],
                doGenerateSubmoduleConfigurations: false,
                extensions: [
                    [$class: 'CloneOption', noTags: false, shallow: false, timeout: 30]
                ],
                userRemoteConfigs: remoteCfg
            ])
        }
    }
    sh "git log -1 --oneline"
}

/**
 * Performs a full git checkout and displays environment information.
 * Checks out the branch that this Jenkinsfile is running from (automatically determined by SCM).
 * Ensures git-lfs is installed and git is properly configured before checkout.
 *
 * Use this for build stages and any stage that needs LFS objects or git tags
 * (e.g. computeImageVersion relies on tag history).
 *
 * @return void
 */
def gitCheckout() {
    configureGit()
    echo "Checking out branch from SCM"
    checkout scm
    if ((env.GIT_LFS_SKIP_SMUDGE ?: '') == '1') {
        echo "GIT_LFS_SKIP_SMUDGE=1 detected; pulling LFS objects after checkout"
        sh 'git lfs pull'
    }
    sh "test -d '${LVS_ROOT}'"
    sh 'ls -lrt; pwd'
}

/**
 * Performs a shallow git checkout for bare metal nodes where network connectivity
 * can be slow or intermittent.
 *
 * Uses depth=1 and noTags to minimise data transfer. LFS hooks are intentionally
 * not installed (configureGit(false)) because git-lfs 3.0.2 ignores
 * GIT_LFS_SKIP_SMUDGE during checkout-index and will attempt to download LFS
 * objects, causing the checkout to hang when the LFS server connection stalls.
 * NOT suitable for stages that need LFS objects or git tags (e.g.
 * computeImageVersion relies on tag history).
 *
 * @return void
 */
def gitCheckoutShallow() {
    configureGit(false)
    // Remove any LFS hooks left by a previous run on this persistent workspace.
    // git-lfs 3.0.2 ignores GIT_LFS_SKIP_SMUDGE during checkout-index, so even
    // with that env var set the filter would attempt to download LFS objects and
    // hang when the connection to the LFS server stalls.
    //
    // git lfs uninstall removes hooks from core.hooksPath. Jenkins sets
    // core.hooksPath=/dev/null during checkout, so uninstall tries to remove
    // /dev/null/pre-push (not a directory → silently fails), leaving .git/hooks/
    // intact. Unset core.hooksPath first so uninstall targets the right directory,
    // then also delete the hook files directly as a belt-and-suspenders measure.
    sh '''
        git config --unset core.hooksPath || true
        git lfs uninstall || true
        rm -f .git/hooks/pre-push .git/hooks/post-checkout .git/hooks/post-merge .git/hooks/post-commit || true
    '''
    // Avoid fetching every branch head on bare-metal workers; this often stalls on
    // slower/unstable links and turns into 30-minute checkout timeouts.
    def requestedBranch = (scm.branches && scm.branches[0]?.name) ? scm.branches[0].name : (env.BRANCH_NAME ?: '')
    requestedBranch = requestedBranch
        .replaceFirst('^\\*/', '')
        .replaceFirst('^origin/', '')
    def branchRefspec = requestedBranch ? "+refs/heads/${requestedBranch}:refs/remotes/origin/${requestedBranch}" : "+refs/heads/*:refs/remotes/origin/*"
    def remoteCfg = scm.userRemoteConfigs.collect { cfg ->
        def m = [url: cfg.url]
        if (cfg.credentialsId) { m.credentialsId = cfg.credentialsId }
        m.refspec = branchRefspec
        return m
    }
    echo "Checking out branch from SCM (shallow): branch=${requestedBranch ?: 'unknown'} refspec=${branchRefspec}"
    withEnv([
        'GIT_LFS_SKIP_SMUDGE=1',
        // Emit detailed Git tracing to diagnose intermittent checkout hangs/failures.
        'GIT_TRACE=2',
        'GIT_TRACE_SETUP=1',
        'GIT_TRACE_REDACT=1',
        'GIT_TRACE_CURL=1',
        'GIT_TRACE_CURL_NO_DATA=1',
        'GIT_HTTP_VERSION=HTTP/1.1',
        "GIT_TRACE2_EVENT=/tmp/git-trace2-event-${env.BUILD_TAG ?: 'jenkins'}.log",
        "GIT_TRACE2_PERF=/tmp/git-trace2-perf-${env.BUILD_TAG ?: 'jenkins'}.log",
        // Force LFS filters into skip mode for this checkout invocation.
        // This protects RTXPRO workers that still have older git-lfs behavior.
        'GIT_CONFIG_COUNT=4',
        'GIT_CONFIG_KEY_0=filter.lfs.process',
        'GIT_CONFIG_VALUE_0=git-lfs filter-process --skip',
        'GIT_CONFIG_KEY_1=filter.lfs.smudge',
        'GIT_CONFIG_VALUE_1=git-lfs smudge --skip -- %f',
        'GIT_CONFIG_KEY_2=filter.lfs.required',
        'GIT_CONFIG_VALUE_2=false',
        'GIT_CONFIG_KEY_3=filter.lfs.clean',
        'GIT_CONFIG_VALUE_3=git-lfs clean -- %f'
    ]) {
        int attempt = 0
        retry(3) {
            attempt += 1
            // RTXPRO workers use a persistent workspace; if a previous run leaves a
            // partially-populated .git directory, checkout can hang for 30 minutes and
            // get SIGTERM'd. Start each retry from a clean workspace.
            echo "Shallow checkout attempt ${attempt}/3: cleaning workspace before checkout"
            deleteDir()
            try {
                checkout([
                    $class: 'GitSCM',
                    branches: scm.branches,
                    extensions: [
                        [$class: 'CloneOption', shallow: true, depth: 1, noTags: true, timeout: 30, honorRefspec: true],
                        // Perf on BM only needs pipeline/compose/benchmark sources. Sparse checkout
                        // keeps working-tree population small and avoids long checkout stalls.
                        [$class: 'SparseCheckoutPaths', sparseCheckoutPaths: [
                            [$class: 'SparseCheckoutPath', path: "${LVS_ROOT}/"]
                        ]],
                        // CheckoutOption covers the "git checkout -f" step which has a separate
                        // default 10-minute timeout from CloneOption. On H100 nodes under disk
                        // load (NIM model loading, Docker I/O), working tree population can
                        // exceed 10 minutes and get SIGTERM'd.
                        [$class: 'CheckoutOption', timeout: 30]
                    ],
                    userRemoteConfigs: remoteCfg
                ])
            } catch (Exception checkoutErr) {
                // Surface trace2 output directly in console so failures are diagnosable
                // without node-local filesystem access.
                sh '''
                    set +e
                    for f in /tmp/git-trace2-event-*.log /tmp/git-trace2-perf-*.log; do
                        [ -f "$f" ] || continue
                        echo "=== TRACE TAIL: $f ==="
                        tail -n 200 "$f" || true
                    done
                '''
                throw checkoutErr
            }
        }
    }
    sh 'ls -lrt; pwd'
}

/**
 * Runs an NGC CLI auth/connectivity preflight using the same key that Docker
 * registry login will use. If the agent image does not include the NGC CLI,
 * the preflight is skipped and Docker login remains the source of truth.
 *
 * @param ngcApiKey NGC API key exported to NGC_CLI_API_KEY for the CLI
 * @param org NGC org to configure for the CLI
 * @param team NGC team to configure for the CLI
 */
def runNgcCliPreflight(String ngcApiKey, String org = NGC_IMAGE_ORG, String team = NGC_IMAGE_TEAM) {
    if (!ngcApiKey?.trim()) {
        error("NGC CLI preflight requires a non-empty NGC API key")
    }

    def markerScope = env.getProperty('NODE_NAME') ?: env.getProperty('POD_LABEL') ?: 'agent'
    // Keep the marker key-independent so this helper stays within Jenkins'
    // Groovy sandbox; every call still performs the authoritative Docker login.
    def marker = "NGC_CLI_PREFLIGHT_${markerScope}_${org}_${team}".replaceAll(/[^A-Za-z0-9_]/, '_').toUpperCase()
    if (env.getProperty(marker) == 'skipped') {
        echo "NGC CLI preflight already skipped for ${org}/${team}; ngc command was unavailable."
        return
    }

    def status
    withEnv([
        "NGC_CLI_API_KEY=${ngcApiKey}",
        "NGC_CLI_ORG=${org}",
        "NGC_CLI_TEAM=${team}"
    ]) {
        try {
            timeout(time: 2, unit: 'MINUTES') {
                status = sh(
                    script: '''
                        set +x
                        set -e
                        if ! command -v ngc >/dev/null 2>&1; then
                            echo "NGC CLI preflight skipped: ngc command is not available in this agent container."
                            exit 42
                        fi

                        tmp_home="$(mktemp -d)"
                        trap 'rm -rf "${tmp_home}"' EXIT
                        export HOME="${tmp_home}"

                        echo "Running NGC CLI preflight for ${NGC_CLI_ORG}/${NGC_CLI_TEAM}"
                        echo "NGC preflight command: ngc --version"
                        ngc --version || true
                        echo "NGC preflight command: ngc config set --auth-option api-key --org ${NGC_CLI_ORG} --team ${NGC_CLI_TEAM} --format_type ascii"
                        ngc config set --auth-option api-key --org "${NGC_CLI_ORG}" --team "${NGC_CLI_TEAM}" --format_type ascii
                        echo "NGC preflight command: ngc diag server"
                        ngc diag server
                    ''',
                    returnStatus: true
                )
            }
        } catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException err) {
            if (isLikelyTimeoutInterruption(err)) {
                error("NGC CLI preflight timed out for ${org}/${team}")
            }
            throw err
        }
    }

    if (status == 42) {
        env.setProperty(marker, 'skipped')
        return
    }
    if (status != 0) {
        error("NGC CLI preflight failed for ${org}/${team} with exit code ${status}")
    }

    echo "NGC CLI preflight passed for ${org}/${team}"
}

/**
 * Logs in to the NGC container registry (nvcr.io).
 * Runs an NGC CLI preflight when available, logs out to clear any stale
 * credentials, then authenticates using the NGC API key.
 *
 * The key is passed via withEnv so it never appears in the rendered sh script body
 *
 * @param ngcApiKey NGC API key used as the password for $oauthtoken authentication
 */
def loginToNvcr(String ngcApiKey) {
    runNgcCliPreflight(ngcApiKey)
    withEnv(["NGC_API_KEY_FOR_LOGIN=${ngcApiKey}"]) {
        sh '''
            docker logout nvcr.io || true
            echo "$NGC_API_KEY_FOR_LOGIN" | docker login nvcr.io -u '$oauthtoken' --password-stdin
        '''
    }
}

/**
 * Waits for the Docker daemon to become available.
 *
 * @return void
 * @throws Exception if Docker daemon is not available within timeout period
 */
def waitForDockerd() {
    try {
        timeout(time: 5, unit: 'MINUTES') {
           sh """
            while ! docker info > /dev/null 2>&1; do
                echo "Waiting for docker daemon at localhost:2375..."
                sleep 5
            done
            echo "Docker Daemon is ready!"
        """
        }
    } catch(Throwable daemonException) {
        error "${daemonException} \nDocker daemon is not available"
    }
}

/**
 * Fetches NGC and API credentials from HashiCorp Vault and puts them into the designated environment variables.
 * Runs in the container named jenkins-shared-lib-base, which must be present in the wrapped pod YAML context.
 *
 * @return void
 * @throws Exception if Vault credentials cannot be fetched
 */
def fetchCredsFromVault() {
    withCredentials([string(credentialsId: 'met-vss-prod-jwt-issuer', variable: 'JOB_JWT')]) {
        container('jenkins-shared-lib-base') {
            sh "vault --version"
            echo "Fetching vault credentials"
            getVaultCredentials(
                secrets: [
                    [secretPath: 'cloudbees/met-vss-cicd/kv2/creds', secretData: 'NGC_API_KEY', envVar: 'NGC_API_KEY'],
                    [secretPath: 'cloudbees/met-vss-cicd/kv2/creds', secretData: 'HF_TOKEN', envVar: 'HF_TOKEN'],
                    [secretPath: 'cloudbees/met-vss-cicd/kv2/creds', secretData: 'OPENAI_API_KEY', envVar: 'OPENAI_API_KEY'],
                    [secretPath: 'cloudbees/met-vss-cicd/kv2/creds', secretData: 'NVIDIA_API_KEY', envVar: 'NVIDIA_API_KEY'],
                    [secretPath: 'blossom/metropolis-mdx-cicd/kv2/nv-one-click/common', secretData: 'SSH_PRIVATE_KEY', envVar: 'SSH_PRIVATE_KEY'],
                    [secretPath: 'blossom/metropolis-mdx-cicd/kv2/nv-one-click/common', secretData: 'SSH_PUBLIC_KEY', envVar: 'SSH_PUBLIC_KEY']
                ],
                jwtToken: JOB_JWT,
                vaultAddress: "https://prod.vault.nvidia.com",
                vaultNamespace: "swtegra-metropolis-apps",
                role: "cloudbees-met-vss-cicd-role",
                mountPath: "auth/jwt/cloudbees/met-vss-cicd/"
            )
        }
    }
}

/**
 * Fetches SSA credentials from HashiCorp Vault for security scanning.
 * Uses metropolis-lab namespace which has access to the SSA secrets.
 */
def fetchSSACredsFromVault() {
    withCredentials([string(credentialsId: 'met-vss-prod-jwt-issuer', variable: 'JOB_JWT')]) {
        container('jenkins-shared-lib-base') {
            sh "vault --version"
            echo "Fetching SSA credentials from vault"
            getVaultCredentials(
                secrets: [
                    [secretPath: 'nvidia/services/ssa/clients/nvssa-prd-eVx9BDYG4DiQ6rhhyAerZz2YpZveLueD3SrT64-Jvx0/kv/secret', secretData: 'id', envVar: 'SSA_ID'],
                    [secretPath: 'nvidia/services/ssa/clients/nvssa-prd-eVx9BDYG4DiQ6rhhyAerZz2YpZveLueD3SrT64-Jvx0/kv/secret', secretData: 'secret', envVar: 'SSA_SECRET']
                ],
                jwtToken: JOB_JWT,
                vaultAddress: "https://prod.vault.nvidia.com",
                vaultNamespace: "metropolis-lab",
                role: "cloudbees-met-vss-cicd-role",
                mountPath: "auth/jwt/cloudbees/met-vss-cicd/",
                logLevel: 'DEBUG'
            )
        }
    }
}

/**
 * Get the node IP and user from the lockable node and set up SSH access.
 *
 * @param lockableNodeLabel Label for the lockable node to reserve (must not be null or empty)
 * @param sshPublicKey SSH public key to add to authorized_keys (must not be null or empty, should be a valid SSH key format)
 * @return Map with keys: nodeName, lockJobUrl, nodeHost, nodeUser
 *         (nodeHost is SSH-preflight-selected for OCI-style node names)
 * @throws Exception if node reservation fails or SSH key setup fails
 */
def getNodeIp(String lockableNodeLabel, String sshPublicKey) {
    if (lockableNodeLabel == null || lockableNodeLabel.trim().isEmpty()) {
        error("getNodeIp: lockableNodeLabel parameter cannot be null or empty")
    }
    if (sshPublicKey == null || sshPublicKey.trim().isEmpty()) {
        error("getNodeIp: sshPublicKey parameter cannot be null or empty")
    }
    // Basic validation: SSH public keys typically start with ssh-rsa, ssh-ed25519, ecdsa-sha2, etc.
    if (!sshPublicKey.trim().matches(/^(ssh-(rsa|dss|ed25519)|ecdsa-sha2-|sk-ecdsa-sha2-|sk-ssh-ed25519@openssh\.com).*/)) {
        error("getNodeIp: sshPublicKey does not appear to be a valid SSH public key format")
    }

    echo "Reserving ${lockableNodeLabel} bare metal node..."
    long lockStartMs = System.currentTimeMillis()
    echo "[LOCK_TIMING] getNode start_epoch_ms=${lockStartMs} label=${lockableNodeLabel}"
    def lockInfo = getNode("${lockableNodeLabel}")

    // Prefer values returned by getNode() (branch-local). Fall back to env vars for
    // compatibility with older shared-library versions that don't return a map.
    def reservedNode = null
    def lockJobUrl = null
    if (lockInfo instanceof Map) {
        reservedNode = lockInfo.nodeName ?: lockInfo.node_name
        lockJobUrl = lockInfo.lockJobUrl ?: lockInfo.lock_job_url
    } else if (lockInfo != null) {
        reservedNode = lockInfo.toString().trim()
    }
    if (!reservedNode?.trim()) {
        reservedNode = env.jen_node
    }
    if (!lockJobUrl?.trim()) {
        lockJobUrl = env.lock_job_url
    }
    // Public/routable IP from lock metadata (same as env.node_ip from getNode). On OCI BM agents,
    // on-node "ip route get 1.1.1.1" may yield a private source IP; select the SSH target below.
    def nodeIpFromLock = null
    if (lockInfo instanceof Map) {
        nodeIpFromLock = (lockInfo.nodeIp ?: lockInfo.node_ip)?.toString()?.trim()
    }
    if (!nodeIpFromLock) {
        nodeIpFromLock = env.node_ip?.toString()?.trim()
    }
    // Detect before node() — reservedNode is known; OCI BM SSH target is selected after node IP discovery.
    boolean ociStyleNode = reservedNode.contains('-OCI') || reservedNode.toUpperCase().endsWith('OCI')

    long lockEndMs = System.currentTimeMillis()
    long lockWaitMs = lockEndMs - lockStartMs
    double lockWaitMinutes = lockWaitMs / 60000.0
    String lockWaitMinutesFormatted = String.format(java.util.Locale.US, "%.2f", lockWaitMinutes)
    String lockWaitMetric = "lock_wait_minutes=${lockWaitMinutesFormatted}"
    env.BM_LOCK_WAIT_MINUTES = lockWaitMinutesFormatted
    try {
        def existingBuildDescription = currentBuild.description?.trim()
        currentBuild.description = existingBuildDescription ? "${existingBuildDescription} | ${lockWaitMetric}" : lockWaitMetric
        echo "[LOCK_TIMING] published_build_description_metric=${lockWaitMetric}"
    } catch (Exception e) {
        echo "Warning: Failed to publish lock wait metric to build description: ${e.getMessage()}"
    }
    echo "[LOCK_TIMING] getNode end_epoch_ms=${lockEndMs} label=${lockableNodeLabel}"
    echo "[LOCK_TIMING] getNode wait_seconds=${(lockWaitMs / 1000.0)} label=${lockableNodeLabel}"
    echo "[LOCK_TIMING] getNode wait_minutes=${lockWaitMinutesFormatted} label=${lockableNodeLabel}"

    if (reservedNode == null || reservedNode.trim().isEmpty()) {
        error("getNodeIp: Failed to reserve node - env.jen_node is not set")
    }

    echo "${lockableNodeLabel} node reserved: ${reservedNode}"

    def nodeIp = null
    def nodeUser = null

    // Get node IP and username
    node(reservedNode) {
        echo "Getting node IP and user information..."
        sh '''
        echo "Disk usage on reserved bare metal node:"
        df -h
        '''
        // On-agent IP not necessarily the routable IP for OCI-style nodes
        nodeIp = sh(script: "ip route get 1.1.1.1 | awk '{print \$7}' | head -1", returnStdout: true).trim()
        nodeUser = sh(script: "whoami", returnStdout: true).trim()

        if (nodeIp == null || nodeIp.isEmpty()) {
            error("getNodeIp: Failed to determine node IP address")
        }
        if (nodeUser == null || nodeUser.isEmpty()) {
            error("getNodeIp: Failed to determine node user")
        }

        echo "Node IP: ${nodeIp}"
        echo "Node User: ${nodeUser}"

        // Copy the CI SSH public key to the lockable node
        // Escape the SSH key to prevent shell injection
        def escapedSshKey = sshPublicKey.replace("'", "'\\''")
        sh """
        whoami
        echo "whoami: \$(whoami)"
        if [ ! -d ~/.ssh ]; then
            mkdir -p ~/.ssh
            touch ~/.ssh/authorized_keys
        elif [ ! -f ~/.ssh/authorized_keys ]; then
            touch ~/.ssh/authorized_keys
        fi

        # Check if the key is already in the authorized_keys file
        if ! grep -qF '${escapedSshKey}' ~/.ssh/authorized_keys; then
            echo '${escapedSshKey}' >> ~/.ssh/authorized_keys
        fi
        """
    }

    if (ociStyleNode && nodeIpFromLock) {
        def nodeIpFromAgent = nodeIp
        echo "OCI-style node: SSH target IP from lock metadata ${nodeIpFromLock} (on-agent route was ${nodeIpFromAgent ?: 'empty'})"
        if (isSshReachableFromOrchestrator(nodeIpFromLock, nodeUser, "${lockableNodeLabel} lock metadata IP", 20, 3)) {
            nodeIp = nodeIpFromLock
        } else if (nodeIpFromAgent && nodeIpFromAgent != nodeIpFromLock &&
                   isSshReachableFromOrchestrator(nodeIpFromAgent, nodeUser, "${lockableNodeLabel} on-agent route IP", 20, 2)) {
            echo "OCI-style node: using on-agent route IP ${nodeIpFromAgent} for SSH because lock metadata IP ${nodeIpFromLock} was unreachable"
            nodeIp = nodeIpFromAgent
        } else {
            error("getNodeIp: OCI-style node ${reservedNode} is not SSH-reachable from orchestrator via lock metadata IP '${nodeIpFromLock}' or on-agent route IP '${nodeIpFromAgent ?: 'empty'}'")
        }
    }
    if (nodeIp == null || nodeIp.isEmpty()) {
        error("getNodeIp: Failed to determine node IP address")
    }
    echo "Node IP (SSH target): ${nodeIp}"

    // Return the reserved node name so callers can use a local variable instead of
    // reading shared env fields in parallel branches.
    return [nodeName: reservedNode, lockJobUrl: lockJobUrl, nodeHost: nodeIp, nodeUser: nodeUser]
}

/**
 * Checks whether the Jenkins orchestrator can SSH to a reserved bare metal host.
 *
 * This is separate from Jenkins agent connectivity: OCI-style bare metal nodes
 * can be ONLINE from Jenkins remoting while still not reachable from the SSH path
 * required for workspace and media sync.
 */
def isSshReachableFromOrchestrator(String host, String user, String contextLabel, int connectTimeoutSeconds = 20, int maxAttempts = 3, int backoffSeconds = 5) {
    if (!host?.trim()) {
        return false
    }
    if (!user?.trim()) {
        error("isSshReachableFromOrchestrator: user is required")
    }
    if (!env.NVOC_SSH_PRIVATE_KEY_FILE?.trim()) {
        error("isSshReachableFromOrchestrator: NVOC_SSH_PRIVATE_KEY_FILE is not set")
    }

    int attempts = maxAttempts > 0 ? maxAttempts : 1
    int backoff = backoffSeconds > 0 ? backoffSeconds : 0
    boolean reachable = false

    withEnv([
        "SSH_PREFLIGHT_HOST=${host}",
        "SSH_PREFLIGHT_USER=${user}",
        "SSH_PREFLIGHT_KEY=${env.NVOC_SSH_PRIVATE_KEY_FILE}",
        "SSH_PREFLIGHT_TIMEOUT=${connectTimeoutSeconds.toString()}"
    ]) {
        for (int attempt = 1; attempt <= attempts; attempt++) {
            echo "[SSH_PREFLIGHT] ${contextLabel}: attempt ${attempt}/${attempts} testing ${user}@${host}:22 from orchestrator"
            def status = sh(
                script: '''#!/usr/bin/env bash
set +x
set -euo pipefail
command -v ssh >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y --no-install-recommends openssh-client; }
ssh -i "${SSH_PREFLIGHT_KEY}" \
  -o StrictHostKeyChecking=no \
  -o UserKnownHostsFile=/dev/null \
  -o ConnectTimeout="${SSH_PREFLIGHT_TIMEOUT}" \
  -o BatchMode=yes \
  -o LogLevel=ERROR \
  "${SSH_PREFLIGHT_USER}@${SSH_PREFLIGHT_HOST}" \
  'echo ssh-preflight-ok' >/dev/null
''',
                returnStatus: true
            )

            if (status == 0) {
                echo "[SSH_PREFLIGHT] ${contextLabel}: SSH reachable on attempt ${attempt}/${attempts}"
                reachable = true
                break
            }
            echo "[SSH_PREFLIGHT] ${contextLabel}: SSH unreachable on attempt ${attempt}/${attempts} (exit ${status})"
            if (attempt < attempts && backoff > 0) {
                int delaySeconds = backoff * attempt
                echo "[SSH_PREFLIGHT] ${contextLabel}: retrying after ${delaySeconds}s"
                sleep(time: delaySeconds, unit: 'SECONDS')
            }
        }
    }
    return reachable
}

/**
 * Runs security scan on a single architecture image.
 */
def runSecurityScanForArch(String imageName, String arch, String nspectId) {
    def imageToScan = "${imageName}:${computeImageVersion(arch)}"
    def tarFile = "image-${arch}.tar"

    wrap([$class: 'MaskPasswordsBuildWrapper', varPasswordPairs: [
        [var: 'NGC_API_KEY', password: env.NGC_API_KEY],
        [var: 'SSA_ID', password: env.SSA_ID],
        [var: 'SSA_SECRET', password: env.SSA_SECRET]
    ]]) {
        container('docker') {
            loginToNvcr(env.NGC_API_KEY)
            sh """
                docker pull ${imageToScan}
                docker save -o ${tarFile} ${imageToScan}
                chmod 0644 ${tarFile}
            """
        }
        container('pulse-cli') {
            runPulseScan(
                SSA_ID: "${env.SSA_ID}",
                SSA_SECRET: "${env.SSA_SECRET}",
                NSPECT_ID: "${nspectId}",
                IMAGE: imageToScan,
                REGISTRY_PASSWORD: "${env.NGC_API_KEY}"
            )
        }
        container('jenkins-shared-lib-base') {
            runContainerFileScan(imageToScan, tarFile)
        }
    }
}

/**
 * Resolves the host path used for NIM model cache mounts.
 * Can be overridden with NIM_CACHE_DIR; defaults to ${HOME}/nim-cache.
 */
def getNimCacheDir() {
    return (env.NIM_CACHE_DIR?.trim()) ? env.NIM_CACHE_DIR.trim() : "${env.HOME}/nim-cache"
}

/**
 * Splits a compose file path into its directory and filename components.
 * @param composeFilePath Full path to the compose file (e.g. "/workspace/compose/h100-foo.yaml")
 * @return Map with keys 'dir' and 'file'
 */
def parseComposePath(String composeFilePath) {
    return [
        dir:  composeFilePath.replaceAll(/\/[^\/]+$/, ''),
        file: composeFilePath.replaceAll(/^.*\//, '')
    ]
}

/**
 * Parses a Docker Compose-style .env file into a Map<String,String>.
 * Returns an empty map if the file does not exist or cannot be read.
 * Lines beginning with '#' and blank lines are ignored. Surrounding single or
 * double quotes around the value are stripped. No interpolation is performed.
 *
 * @param envFilePath Absolute or workspace-relative path to the .env file
 * @return Map of variable name -> raw string value (never null)
 */
def parseEnvFile(String envFilePath) {
    def envMap = [:]
    if (!envFilePath?.trim() || !fileExists(envFilePath)) {
        return envMap
    }
    try {
        def content = readFile(envFilePath)
        for (rawLine in content.split('\n')) {
            def line = rawLine.trim()
            if (!line || line.startsWith('#')) continue
            def idx = line.indexOf('=')
            if (idx <= 0) continue
            def key = line.substring(0, idx).trim()
            def value = line.substring(idx + 1).trim()
            if (value.length() >= 2) {
                def first = value.substring(0, 1)
                def last = value.substring(value.length() - 1)
                if ((first == '"' && last == '"') || (first == "'" && last == "'")) {
                    value = value.substring(1, value.length() - 1)
                }
            }
            envMap[key] = value
        }
    } catch (Exception e) {
        echo "parseEnvFile: could not read ${envFilePath}: ${e.message}"
    }
    return envMap
}

/**
 * Resolves Docker Compose-style variable interpolation in a single string value.
 * Supports ${VAR}, ${VAR:-default}, ${VAR-default}, ${VAR:?msg}, ${VAR?msg}.
 * Unrecognised/missing values resolve to '' (we do not error on :? / ? — the
 * caller validates the resolved string and produces a friendlier message).
 *
 * Implementation note: Jenkins CPS does not reliably bind the per-group
 * arguments of a closure passed to String.replaceAll(Pattern, Closure)
 * (the closure is sometimes invoked with no arguments / wrong arity,
 * which yields an empty replacement). Use an explicit Matcher loop to
 * avoid the CPS issue.
 *
 * @param value Raw string possibly containing ${...} placeholders
 * @param envMap Substitution dictionary (e.g. from parseEnvFile)
 * @return Fully-substituted string, or null if value is null
 */
@NonCPS
def resolveComposeInterpolation(String value, Map envMap) {
    if (value == null) return null
    def pattern = ~/\$\{([A-Za-z_][A-Za-z0-9_]*)(:-|:\?|-|\?)?([^}]*)\}/
    def m = pattern.matcher(value)
    def sb = new StringBuffer()
    while (m.find()) {
        def name = m.group(1)
        def op = m.group(2)
        def defaultVal = m.group(3)
        def envVal = envMap?.get(name)
        boolean envSet = envVal != null
        boolean envNonEmpty = envSet && envVal.toString().length() > 0
        String replacement
        if (op == null || op == '') {
            replacement = envSet ? envVal.toString() : ''
        } else if (op == ':-') {
            replacement = envNonEmpty ? envVal.toString() : (defaultVal ?: '')
        } else if (op == '-') {
            replacement = envSet ? envVal.toString() : (defaultVal ?: '')
        } else {
            // ':?' and '?' would normally fail in Compose if unset/empty;
            // we simply substitute what we have and let the caller validate.
            replacement = envSet ? envVal.toString() : ''
        }
        m.appendReplacement(sb, java.util.regex.Matcher.quoteReplacement(replacement))
    }
    m.appendTail(sb)
    return sb.toString()
}

/**
 * Reads relevant LVS service env vars from a docker-compose file with full
 * Compose-style interpolation against the sibling .env file.
 *
 * Concretely: resolves ${VAR} / ${VAR:-default} placeholders so that a YAML
 * value like `LVS_DATABASE_BACKEND: ${LVS_DATABASE_BACKEND:-elasticsearch_db}`
 * becomes `elasticsearch_db`, and a value like
 * `LVS_LLM_BASE_URL: http://${LVS_LLM_HOST}:${LVS_LLM_PORT}/v1` becomes a
 * fully-qualified URL when LVS_LLM_HOST / LVS_LLM_PORT are defined in
 * `<composeDir>/.env`.
 *
 * @param composeFilePath Path to the compose file (workspace-relative or absolute), e.g. "compose/BlueprintBuilderGenerated/docker-compose.yml"
 * @return Map with keys lvsDatabaseBackend, esPort, llmBaseUrl, llmModelName (strings; '' when not found or on error)
 */
def getLvsDatabaseEnvFromCompose(String composeFilePath) {
    def result = [ lvsDatabaseBackend: '', esPort: '', llmBaseUrl: '', llmModelName: '' ]
    if (!composeFilePath?.trim()) {
        return result
    }
    try {
        def composed = parseComposePath(composeFilePath)
        def envMap = parseEnvFile("${composed.dir}/.env")
        def compose = readYaml file: composeFilePath
        def lvsEnv = compose?.services?.lvs?.environment
        if (lvsEnv != null) {
            def resolve = { raw ->
                if (raw == null) return ''
                def s = resolveComposeInterpolation(raw.toString(), envMap)
                return s?.replaceAll(/^['"]|['"]$/, '')?.trim() ?: ''
            }
            result.lvsDatabaseBackend = resolve(lvsEnv.LVS_DATABASE_BACKEND)
            result.esPort             = resolve(lvsEnv.ES_PORT)
            result.llmBaseUrl         = resolve(lvsEnv.LVS_LLM_BASE_URL)
            result.llmModelName       = resolve(lvsEnv.LVS_LLM_MODEL_NAME)
        }
    } catch (Exception e) {
        echo "getLvsDatabaseEnvFromCompose: could not read ${composeFilePath}: ${e.message}"
    }
    return result
}

/**
 * Returns true if a string still contains an unresolved Compose-style
 * placeholder (e.g. "http://${LVS_LLM_HOST}:9233/v1") or has empty host/port
 * segments after substitution (e.g. "http://:/v1", "http://host:/v1").
 */
def hasUnresolvedComposePlaceholder(String value) {
    if (value == null) return true
    if (value.contains('${')) return true
    // Matches "//:" (empty host) and ":/" immediately followed by anything that
    // isn't a digit (empty port). Catches the http://:/v1 and http://host:/v1
    // shapes produced when only one of host/port resolves.
    if (value =~ /\/\/:/) return true
    if (value =~ /:\/[^\/0-9]/) return true
    if (value.endsWith(':/')) return true
    return false
}

def buildComposeProfileFlags(String composeProfiles = null) {
    if (composeProfiles == null || composeProfiles.trim().isEmpty()) {
        return ''
    }
    return composeProfiles
        .split(',')
        .collect { it.trim() }
        .findAll { it }
        .collect { "--profile ${it}" }
        .join(' ')
}

/**
 * Builds the docker compose environment for Jenkins withEnv.
 * Keep values out of command strings so Blue Ocean/xtrace cannot print them.
 */
def buildDockerComposeEnvironment(
    String ngcApiKey = null,
    String nvidiaApiKey = null,
    String openaiApiKey = null,
    String hfToken = null,
    String artifactoryUser = null,
    String artifactoryToken = null
) {
    def resolvedNgcApiKey = ngcApiKey != null ? ngcApiKey : (env.NGC_API_KEY_LVS ?: env.NGC_API_KEY ?: '')
    def nimCacheDir = getNimCacheDir()
    def framesPerChunk = env.VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK ?: RTVI_VLM_FRAMES_PER_CHUNK.toString()

    def composeEnv = [
        "NGC_API_KEY=${resolvedNgcApiKey}",
        "LOCAL_NIM_CACHE=${nimCacheDir}",
        "VLM_DEFAULT_NUM_FRAMES_PER_SECOND_OR_FIXED_FRAMES_CHUNK=${framesPerChunk}",
    ]

    if (nvidiaApiKey != null) {
        composeEnv << "NVIDIA_API_KEY=${nvidiaApiKey}"
    }
    if (openaiApiKey != null) {
        composeEnv << "OPENAI_API_KEY=${openaiApiKey}"
        composeEnv << "VIA_VLM_API_KEY=${openaiApiKey}"
    }
    if (hfToken != null) {
        composeEnv << "HF_TOKEN=${hfToken}"
    }
    if (artifactoryUser != null) {
        composeEnv << "ARTIFACTORY_USER=${artifactoryUser}"
    }
    if (artifactoryToken != null) {
        composeEnv << "ARTIFACTORY_TOKEN=${artifactoryToken}"
        // RTVI-VLM downloads Artifactory URLs directly. Pass the bearer token
        // in the format expected by its asset downloader.
        composeEnv << "ASSET_DOWNLOAD_AUTH_TOKENS=artifactory.nvidia.com=Bearer ${artifactoryToken}"
    }
    if (env.SHARED_RTVI_VLM_URL) {
        composeEnv << "RTVI_VLM_URL=${env.SHARED_RTVI_VLM_URL}"
    }
    if (env.COMPOSE_PROFILES) {
        composeEnv << "COMPOSE_PROFILES=${env.COMPOSE_PROFILES}"
    }

    return composeEnv
}

def dockerComposeEnvNames(List<String> composeEnv) {
    return composeEnv
        .collect { it.substring(0, it.indexOf('=')) }
        .unique()
}

/**
 * Runs a body with compose environment values scoped to the enclosed shell steps.
 */
def withDockerComposeEnvironment(
    String ngcApiKey = null,
    String nvidiaApiKey = null,
    String openaiApiKey = null,
    String hfToken = null,
    String artifactoryUser = null,
    String artifactoryToken = null,
    Closure body
) {
    withEnv(buildDockerComposeEnvironment(
        ngcApiKey,
        nvidiaApiKey,
        openaiApiKey,
        hfToken,
        artifactoryUser,
        artifactoryToken
    )) {
        body()
    }
}

/**
 * Builds a docker compose command. Secret values are supplied by
 * withDockerComposeEnvironment; sudo receives only env var names to preserve.
 */
def buildDockerComposeCommand(
    boolean useSudo = true,
    String ngcApiKey = null,
    String nvidiaApiKey = null,
    String openaiApiKey = null,
    String hfToken = null,
    String composeFile = null,
    String artifactoryUser = null,
    String artifactoryToken = null,
    String composeProfiles = null
) {
    def composeEnvNames = dockerComposeEnvNames(buildDockerComposeEnvironment(
        ngcApiKey,
        nvidiaApiKey,
        openaiApiKey,
        hfToken,
        artifactoryUser,
        artifactoryToken
    ))
    def sudoCmd = useSudo ? "sudo --preserve-env=${composeEnvNames.join(',')} " : ''

    def fileFlag = composeFile ? " -f ${composeFile}" : ''
    def profileFlags = buildComposeProfileFlags(composeProfiles)
    def profileFlag = profileFlags ? " ${profileFlags}" : ''
    return "${sudoCmd}docker compose${fileFlag}${profileFlag}"
}

def withComposeProject(String composeCmd, String projectName) {
    return composeCmd.replaceFirst('docker compose', "docker compose -p ${projectName}")
}

def resolveSharedRtviNodeIp() {
    def override = (env.SHARED_RTVI_NODE_IP ?: '').trim()
    if (override) {
        echo "[SHARED_RTVI] Using SHARED_RTVI_NODE_IP override: ${override}"
        return override
    }

    def nodeIp = sh(
        script: '''
            set -eu
            ip -4 route get 8.8.8.8 2>/dev/null | awk '{for (i=1; i<=NF; i++) if ($i == "src") {print $(i+1); exit}}'
        ''',
        returnStdout: true
    ).trim()
    if (!nodeIp) {
        nodeIp = sh(
            script: "hostname -I | awk '{print \$1}'",
            returnStdout: true
        ).trim()
    }
    if (!nodeIp) {
        error("[SHARED_RTVI] Could not determine Jenkins node IP. Set SHARED_RTVI_NODE_IP explicitly.")
    }
    echo "[SHARED_RTVI] Resolved Jenkins node IP: ${nodeIp}"
    return nodeIp
}

def getSharedRtviVlmUrl() {
    if (!env.SHARED_RTVI_VLM_URL) {
        env.SHARED_RTVI_VLM_URL = "http://${resolveSharedRtviNodeIp()}:${SHARED_RTVI_PORT}"
    }
    return env.SHARED_RTVI_VLM_URL
}

def clearSharedRtviAssets(boolean useSudo = true) {
    def sudoCmd = useSudo ? 'sudo ' : ''
    sh """
    set +e
    echo "[SHARED_RTVI] Clearing /tmp/assets in shared RTVI-VLM container..."
    if ${sudoCmd}docker ps --format '{{.Names}}' | grep -qx 'rtvi-vlm'; then
        ${sudoCmd}docker exec rtvi-vlm bash -lc 'rm -rf /tmp/assets/* /tmp/assets/.[!.]* /tmp/assets/..?* 2>/dev/null || true'
        ${sudoCmd}docker exec rtvi-vlm df -h /tmp/assets || true
    else
        echo "[SHARED_RTVI] rtvi-vlm container is not running; nothing to clear"
    fi
    """
}

def startSharedRtviVlm(boolean useSudo = true, Map envCredentials, String composeFilePath) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("startSharedRtviVlm: composeFilePath must be provided")
    }

    def composed = parseComposePath(composeFilePath)
    def baseComposeCmd = buildDockerComposeCommand(
        useSudo,
        envCredentials.ngcApiKey,
        envCredentials.nvidiaApiKey,
        envCredentials.openaiApiKey,
        envCredentials.hfToken,
        composed.file,
        envCredentials.artifactoryUser,
        envCredentials.artifactoryToken,
        'rtvi'
    )
    def sharedComposeCmd = withComposeProject(baseComposeCmd, SHARED_RTVI_COMPOSE_PROJECT)
    def sharedRtviUrl = getSharedRtviVlmUrl()
    def sharedRtviHealthUrl = "${sharedRtviUrl.replaceAll('/+$', '')}/v1/health/ready"
    def sudoCmd = useSudo ? 'sudo ' : ''

    withDockerComposeEnvironment(
        envCredentials.ngcApiKey,
        envCredentials.nvidiaApiKey,
        envCredentials.openaiApiKey,
        envCredentials.hfToken,
        envCredentials.artifactoryUser,
        envCredentials.artifactoryToken
    ) {
        sh """
        set -eu
        cd ${composed.dir}
        echo "[SHARED_RTVI] Starting/reusing shared rtvi-vlm at ${sharedRtviUrl}"
        echo "[SHARED_RTVI] Compose project: ${SHARED_RTVI_COMPOSE_PROJECT}"
        export RTVI_VLM_PORT=${SHARED_RTVI_PORT}
        running="\$(${sudoCmd}docker inspect -f '{{ .State.Running }}' rtvi-vlm 2>/dev/null || true)"
        project="\$(${sudoCmd}docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' rtvi-vlm 2>/dev/null || true)"
        if [ "\$running" = "true" ] && [ "\$project" = "${SHARED_RTVI_COMPOSE_PROJECT}" ]; then
            echo "[SHARED_RTVI] Reusing already-running rtvi-vlm container"
        else
            ${sharedComposeCmd} pull rtvi-vlm || true
            ${sharedComposeCmd} up -d rtvi-vlm
            ${sharedComposeCmd} up --wait --wait-timeout 1500 --no-recreate rtvi-vlm
        fi
        ${sharedComposeCmd} ps rtvi-vlm

        echo "[SHARED_RTVI] Validating health from a short-lived container..."
        ${sudoCmd}docker run --rm --network host python:3.12-slim python3 - <<'PYEOF'
import sys
import time
import urllib.request

url = "${sharedRtviHealthUrl}"
last_error = None
for _ in range(60):
    try:
        with urllib.request.urlopen(url, timeout=5) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            print(f"[SHARED_RTVI] health status={resp.status} body={body[:200]}")
            if resp.status == 200:
                sys.exit(0)
    except Exception as exc:
        last_error = exc
    time.sleep(5)
print(f"[SHARED_RTVI] health check failed for {url}: {last_error}", file=sys.stderr)
sys.exit(1)
PYEOF
        """
    }
    clearSharedRtviAssets(useSudo)
    return sharedRtviUrl
}

def stopSharedRtviVlm(boolean useSudo = true, String composeFilePath = null) {
    def sudoCmd = useSudo ? 'sudo ' : ''
    if (composeFilePath != null && composeFilePath.trim()) {
        def composed = parseComposePath(composeFilePath)
        def baseComposeCmd = buildDockerComposeCommand(useSudo, null, null, null, null, composed.file, null, null, 'rtvi')
        def sharedComposeCmd = withComposeProject(baseComposeCmd, SHARED_RTVI_COMPOSE_PROJECT)
        withDockerComposeEnvironment(null, null, null, null, null, null) {
            sh """
            set +e
            cd ${composed.dir}
            echo "[SHARED_RTVI] Stopping shared RTVI-VLM compose project ${SHARED_RTVI_COMPOSE_PROJECT}"
            ${sharedComposeCmd} down -v --remove-orphans || true
            """
        }
    } else {
        sh """
        set +e
        echo "[SHARED_RTVI] Stopping shared RTVI-VLM compose project ${SHARED_RTVI_COMPOSE_PROJECT}"
        ${sudoCmd}docker compose -p ${SHARED_RTVI_COMPOSE_PROJECT} down -v --remove-orphans || true
        ${sudoCmd}docker rm -f rtvi-vlm || true
        """
    }
    env.SHARED_RTVI_VLM_URL = ''
}

/**
 * Pulls Docker Compose images with progress tracking.
 * This operation has its own timeout (separate from deployment timeout).
 *
 * @param ngcApiKey NGC API key for container registry access
 * @param nvidiaApiKey NVIDIA API key for NIM services
 * @param openaiApiKey OpenAI API key for LLM services
 * @param useSudo Whether to use sudo for docker commands (default: true for bare metal, false for DinD)
 * @param composeFilePath Path to the docker compose file (e.g. "compose/h100-foo.yaml")
 * @param hfToken Hugging Face token for model access (optional)
 * @param builtImageTag When non-null, the lvs service image was built locally and must not be pulled from registry
 * @param composeProfiles Optional comma-separated compose profiles to enable (e.g. 'rtvi')
 */
def pullDockerComposeImages(
    String ngcApiKey,
    String nvidiaApiKey,
    String openaiApiKey,
    boolean useSudo = true,
    String composeFilePath,
    String hfToken = null,
    String builtImageTag = null,
    String artifactoryUser = null,
    String artifactoryToken = null,
    String composeProfiles = null
) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("pullDockerComposeImages: composeFilePath must be provided")
    }

    def composed = parseComposePath(composeFilePath)
    def composeCmd = buildDockerComposeCommand(
        useSudo,
        ngcApiKey,
        nvidiaApiKey,
        openaiApiKey,
        hfToken,
        composed.file,
        artifactoryUser,
        artifactoryToken,
        composeProfiles
    )

    withDockerComposeEnvironment(ngcApiKey, nvidiaApiKey, openaiApiKey, hfToken, artifactoryUser, artifactoryToken) {
        if (builtImageTag != null) {
            // lvs image was built locally — pulling it from registry would fail since it hasn't been pushed yet.
            // Pull only the dependency services (everything except lvs).
            sh """
            cd ${composed.dir}

            echo "[PULL] Built image detected (${builtImageTag}), skipping pull for lvs service..."
            echo "[PULL] Pre-pulling dependency images (excluding lvs)..."
            PULL_START=\$(date +%s)
            SERVICES=\$(${composeCmd} config --services 2>/dev/null | grep -v '^lvs\$' | tr '\\n' ' ')
            echo "[PULL] Pulling services: \$SERVICES"
            ${composeCmd} pull \$SERVICES
            PULL_END=\$(date +%s)
            echo "[PULL] Compose image pull completed in \$((PULL_END - PULL_START))s"
            """
        } else {
            sh """
            cd ${composed.dir}

            echo "[PULL] Pre-pulling compose images (including dependencies)..."
            PULL_START=\$(date +%s)
            ${composeCmd} pull --include-deps
            PULL_END=\$(date +%s)
            echo "[PULL] Compose image pull completed in \$((PULL_END - PULL_START))s"
            """
        }
    }
}

/**
 * Starts Docker Compose services and waits for them to become healthy.
 * Uses docker compose --wait to leverage health checks defined in docker-compose.yml.
 * NOTE: Images must be pulled before calling this function (use pullDockerComposeImages).
 *
 * @param ngcApiKey NGC API key for container registry access
 * @param nvidiaApiKey NVIDIA API key for NIM services
 * @param openaiApiKey OpenAI API key for LLM services
 * @param useSudo Whether to use sudo for docker commands (default: true for bare metal, false for DinD)
 * @param composeFilePath Path to the docker compose file (e.g. "compose/h100-foo.yaml")
 * @param hfToken Hugging Face token for model access (optional)
 * @param composeProfiles Optional comma-separated compose profiles to enable (e.g. 'rtvi')
 * Timeout is resolved from DEPLOYMENT_TIMEOUT_MINUTES via resolveDeploymentTimeoutMinutes().
 * @return boolean true if deployment succeeded, false otherwise
 */
def runDockerComposeDeployment(
    String ngcApiKey,
    String nvidiaApiKey,
    String openaiApiKey,
    boolean useSudo = true,
    String composeFilePath,
    String hfToken = null,
    String artifactoryUser = null,
    String artifactoryToken = null,
    String composeProfiles = null
) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("runDockerComposeDeployment: composeFilePath must be provided")
    }

    def composed = parseComposePath(composeFilePath)
    def sudoCmd = useSudo ? 'sudo ' : ''
    def composeCmd = buildDockerComposeCommand(
        useSudo,
        ngcApiKey,
        nvidiaApiKey,
        openaiApiKey,
        hfToken,
        composed.file,
        artifactoryUser,
        artifactoryToken,
        composeProfiles
    )
    def timeoutMinutes = resolveDeploymentTimeoutMinutes()
    def waitTimeoutSeconds = timeoutMinutes * 60

    withDockerComposeEnvironment(ngcApiKey, nvidiaApiKey, openaiApiKey, hfToken, artifactoryUser, artifactoryToken) {
        sh """
        cd ${composed.dir}

        echo "[DEPLOYMENT] Starting Docker Compose services (with ${timeoutMinutes}-minute timeout)..."

        # Start services in detached mode
        echo "Starting docker compose services..."
        ${composeCmd} up -d

        # Stream lvs logs in the background so startup output is visible while we wait for health checks.
        # This makes it possible to diagnose slow-startup issues (e.g. disk pressure on the H100 node)
        # without needing to wait for a timeout before seeing any lvs output.
        ${composeCmd} logs -f lvs downloader &
        LVS_LOG_PID=\$!
        echo "[DEPLOYMENT] Streaming lvs+downloader logs in background (PID: \$LVS_LOG_PID)"

        # Wait for all services to report healthy via Docker healthchecks.
        # --no-recreate prevents compose from restarting one-shot services (e.g. downloader)
        # that already exited successfully from the preceding 'up -d' call.
        ${composeCmd} up --wait --wait-timeout ${waitTimeoutSeconds} --no-recreate
        WAIT_RC=\$?

        if [ \$WAIT_RC -eq 0 ]; then
            echo "✓ Services are reporting healthy!"
            ${composeCmd} ps
        fi

        exit \$WAIT_RC
        """
    }
    return true
}

/**
 * Builds a shell script that runs a test command inside a one-off Docker container.
 * Use this for tests that need a configurable image and arguments.
 *
 * @param useSudo Whether to use sudo for docker commands
 * @param dockerRunArgs List of arguments for `docker run` (e.g. --network, -v, image name). Passed as: docker run --rm &lt;dockerRunArgs&gt; &lt;testCommand&gt;
 * @param testCommand The command to run in the container (e.g. 'bash -c "pip install ... && pytest ..."'). Caller is responsible for quoting.
 * @return The full shell script string. Pass to {@code sh runTestsInDocker(...)} to execute, or embed in a larger script.
 */
def runTestsInDocker(boolean useSudo, List<String> dockerRunArgs, String testCommand) {
    def sudoCmd = useSudo ? 'sudo ' : ''
    def argsBlock = dockerRunArgs.join(' \\\n        ')
    return """
    ${sudoCmd}docker run --rm \\
        ${argsBlock} \\
        ${testCommand}
    """.stripIndent()
}


/**
 * Verifies that the Jenkins Artifactory credential can read the media file used
 * by functional summarization tests before LVS/RTVI are involved.
 */
def runFunctionalArtifactoryDownloadPreflight(boolean useSudo) {
    def sudoCmd = useSudo ? 'sudo --preserve-env=ARTIFACTORY_TOKEN ' : ''
    def rc = sh(
        script: """
        set +x
        rm -f artifactory-media-preflight.log
        ${sudoCmd}docker run --rm -i --network host \
            -e ARTIFACTORY_TOKEN \
            python:3.12-slim \
            python3 - <<'PY' > artifactory-media-preflight.log 2>&1
import os
import sys
import urllib.error
import urllib.request

url = "https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4"
token = os.environ.get("ARTIFACTORY_TOKEN", "")

print(f"[ARTIFACTORY_PREFLIGHT] URL: {url}")
print(f"[ARTIFACTORY_PREFLIGHT] Token present: {'yes' if token else 'no'}")
if not token:
    print("[ARTIFACTORY_PREFLIGHT] ERROR: ARTIFACTORY_TOKEN is not set")
    sys.exit(2)

request = urllib.request.Request(
    url,
    headers={
        "Authorization": f"Bearer {token}",
        "Range": "bytes=0-0",
        "User-Agent": "lvs-ci-artifactory-preflight",
    },
)

try:
    with urllib.request.urlopen(request, timeout=30) as response:
        data = response.read(1)
        status = response.getcode()
        print(f"[ARTIFACTORY_PREFLIGHT] HTTP status: {status}")
        print(f"[ARTIFACTORY_PREFLIGHT] Content-Length: {response.headers.get('Content-Length', 'unknown')}")
        print(f"[ARTIFACTORY_PREFLIGHT] Content-Range: {response.headers.get('Content-Range', 'unknown')}")
        print(f"[ARTIFACTORY_PREFLIGHT] Downloaded bytes: {len(data)}")
        if status not in (200, 206) or not data:
            print("[ARTIFACTORY_PREFLIGHT] ERROR: media download probe did not return bytes")
            sys.exit(1)
        print("[ARTIFACTORY_PREFLIGHT] Media download auth check passed")
except urllib.error.HTTPError as exc:
    print(f"[ARTIFACTORY_PREFLIGHT] HTTP status: {exc.code}")
    print(f"[ARTIFACTORY_PREFLIGHT] HTTP reason: {exc.reason}")
    for header in ("WWW-Authenticate", "X-Artifactory-Id", "X-JFrog-Version", "Server"):
        value = exc.headers.get(header)
        if value:
            print(f"[ARTIFACTORY_PREFLIGHT] {header}: {value}")
    body = exc.read(300).decode("utf-8", "replace").strip()
    if body:
        print(f"[ARTIFACTORY_PREFLIGHT] Response body: {body}")
    sys.exit(1)
except Exception as exc:
    print(f"[ARTIFACTORY_PREFLIGHT] ERROR: {type(exc).__name__}: {exc}")
    sys.exit(1)
PY
        rc=\$?
        cat artifactory-media-preflight.log
        exit \$rc
        """.stripIndent(),
        returnStatus: true
    )
    archiveArtifacts artifacts: 'artifactory-media-preflight.log', allowEmptyArchive: true
    if (rc != 0) {
        error("[ARTIFACTORY_PREFLIGHT] Failed to download functional test media from Artifactory. See artifactory-media-preflight.log for HTTP status and response details.")
    }
}

/**
 * Captures a minimal LVS API diagnostic before the full functional suite runs.
 * This stays at the LVS HTTP boundary: /models plus one /summarize request.
 */
def runFunctionalLvsApiDiagnostic(boolean useSudo) {
    def sudoCmd = useSudo ? 'sudo ' : ''
    def rc = sh(
        script: """
        set +x
        rm -f lvs-api-diagnostic.log
        ${sudoCmd}docker run --rm --network host \
            python:3.12-slim \
            python3 - <<'PY' > lvs-api-diagnostic.log 2>&1
import json
import sys
import time
import urllib.error
import urllib.request

BASE_URL = "http://localhost:38111"
MEDIA_URL = (
    "https://artifactory.nvidia.com/artifactory/"
    "sw-ds-generic-bld-local/via-engine/media/bp_preview/its_264.mp4"
)


def request_json(method, path, payload=None, timeout=30):
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        BASE_URL + path,
        data=data,
        method=method,
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read().decode("utf-8", "replace")
            return response.getcode(), body
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace")
        return exc.code, body


def print_body(prefix, body, limit=4000):
    text = body if len(body) <= limit else body[:limit] + "...<truncated>"
    print(f"{prefix}: {text}")


print(f"[LVS_API_DIAG] Base URL: {BASE_URL}")

try:
    status, body = request_json("GET", "/models", timeout=30)
    print(f"[LVS_API_DIAG] GET /models status: {status}")
    print_body("[LVS_API_DIAG] GET /models body", body)
    models_payload = json.loads(body) if body else {}
    models = models_payload.get("data") or []
    model_id = models[0].get("id") if models else ""
    print(f"[LVS_API_DIAG] Selected model from /models: {model_id or '<none>'}")

    summarize_payload = {
        "url": MEDIA_URL,
        "model": model_id,
        "events": ["accident", "emergency vehicle"],
        "scenario": "traffic monitoring",
        "chunk_duration": 10,
        "num_frames_per_second_or_fixed_frames_chunk": 5,
        "use_fps_for_chunking": False,
        "max_tokens": 1024,
    }
    print("[LVS_API_DIAG] POST /summarize payload summary: " + json.dumps({
        "model": summarize_payload["model"],
        "chunk_duration": summarize_payload["chunk_duration"],
        "num_frames_per_second_or_fixed_frames_chunk": summarize_payload[
            "num_frames_per_second_or_fixed_frames_chunk"
        ],
        "url": MEDIA_URL,
    }, sort_keys=True))

    start = time.time()
    status, body = request_json("POST", "/summarize", payload=summarize_payload, timeout=180)
    elapsed = time.time() - start
    print(f"[LVS_API_DIAG] POST /summarize status: {status}")
    print(f"[LVS_API_DIAG] POST /summarize elapsed_sec: {elapsed:.2f}")
    print_body("[LVS_API_DIAG] POST /summarize body", body)

    try:
        data = json.loads(body) if body else {}
    except json.JSONDecodeError as exc:
        print(f"[LVS_API_DIAG] WARNING: summarize response is not JSON: {exc}")
        sys.exit(0)

    choices = data.get("choices") or []
    usage = data.get("usage") or {}
    media_info = data.get("media_info") or {}
    total_chunks = usage.get("total_chunks_processed")
    print(f"[LVS_API_DIAG] response_model: {data.get('model')}")
    print(f"[LVS_API_DIAG] response_video_id: {data.get('video_id')}")
    print(f"[LVS_API_DIAG] choices_count: {len(choices)}")
    print(f"[LVS_API_DIAG] total_chunks_processed: {total_chunks}")
    print(f"[LVS_API_DIAG] media_info: {json.dumps(media_info, sort_keys=True)}")
    if status == 200 and (not choices or total_chunks == 0):
        print("[LVS_API_DIAG] WARNING: /summarize returned HTTP 200 without usable chunks/choices")
except Exception as exc:
    print(f"[LVS_API_DIAG] ERROR: {type(exc).__name__}: {exc}")
    sys.exit(1)
PY
        rc=\$?
        cat lvs-api-diagnostic.log
        exit \$rc
        """.stripIndent(),
        returnStatus: true
    )
    archiveArtifacts artifacts: 'lvs-api-diagnostic.log', allowEmptyArchive: true
    if (rc != 0) {
        echo("[LVS_API_DIAG] Diagnostic command failed; continuing to functional tests so pytest output is still collected.")
    }
}

/**
 * Builds a shell script that runs functional API tests against a running VIA service.
 * Tests run in a lightweight Python container and validate API endpoint behavior.
 * Coverage is collected and reports generated in /workspace.
 *
 * @param useSudo Whether to use sudo for docker commands
 * @return Shell script string to execute (pass to {@code sh} or concatenate with other test commands)
 */
def runFunctionalTest(boolean useSudo) {
    def dockerRunArgs = [
        '--network host',
        "-v ${serviceWorkspacePath()}:/workspace",
        '-w /workspace',
        '-v /tmp/via-logs:/tmp/via-logs',
        'python:3.12-slim'
    ]
    // NOTE: coverage instrumentation is intentionally omitted here.
    // Functional tests are black-box HTTP tests (python:3.12-slim calling http://localhost:38111).
    // coverage.py only tracks the test-runner process, not the VIA server running in a separate
    // container, so it would always report 0%. Coverage is collected by the integration tests
    // which run inside the LVS image and directly import src/ modules.
    def testCommand = 'bash -c "pip install requests pytest pytest-timeout sseclient-py tabulate tqdm pyyaml && ' +
        'pytest tests/functional/ -m test_in_ci --base-url http://localhost:38111 ' +
            '--timeout=300 --junit-xml=/workspace/pytest-report.api-tests.xml -v --override-ini=filterwarnings= --confcutdir=/workspace/tests/functional && ' +
        'python3 /workspace/ci/utils/convert_junit_to_csv.py /workspace/pytest-report.api-tests.xml /workspace/functional-test-results.csv"'
    return runTestsInDocker(useSudo, dockerRunArgs, testCommand)
}

/**
 * Runs integration test suites, each in its own Docker container for process isolation.
 * Running in separate containers prevents Prometheus CollectorRegistry collisions
 * that occur when two ViaTestServer instances register the same metrics in one process.
 *
 * Suite 1 — RTVI integration tests (test_rtvi_integration.py):
 *   Self-contained with a mock RTVI server; no GPU or external RTVI VLM needed.
 *
 * Suite 2 — CA RAG integration tests (test_ca_rag_integration.py):
 *   Starts ViaTestServer which connects to the real RTVI VLM. Requires
 *   RTVI_VLM_URL so LVS can reach the running RTVI instance instead of
 *   timing out against the default http://localhost:8000.
 *
 * Each suite produces separate coverage data, JUnit XML, and CSV reports.
 * Both suites always run; the exit code reflects any failure.
 *
 * @param useSudo Whether to use sudo for docker commands
 * @param envCredentials Map of API keys and config
 * @param dockerImage Full docker image tag to run tests in
 * @param lvsDatabaseBackend Database backend type (e.g. 'elasticsearch_db')
 * @param esHost Elasticsearch host
 * @param esPort Elasticsearch port
 * @param llmBaseUrl LLM base URL for CA-RAG context manager (e.g. 'http://host:port/v1')
 * @param llmModelName LLM model name passed to CA-RAG context manager
 * @param rtviVlmUrl RTVI VLM base URL passed to ViaTestServer in the CA RAG suite
 * @return Shell script string running two docker containers with accumulated exit code
 */
def runIntegrationTest(boolean useSudo, Map envCredentials, String dockerImage, String lvsDatabaseBackend, String esHost, String esPort, String llmBaseUrl = '', String llmModelName = '', String rtviVlmUrl = '') {
    // Secret env vars use bare `-e KEY` (docker inherits the value from the
    // parent shell at runtime). The caller (runServiceTestSuite) wraps the
    // surrounding sh in withEnv so the parent shell has them set. This keeps
    // the rendered script string — which Blue Ocean shows as the step header —
    // free of plaintext secrets.
    def baseDockerRunArgs = [
        '--user root',
        '--entrypoint bash',
        '--network host',
        '--gpus all',
        '--runtime=nvidia',
        '--add-host=host.docker.internal:127.0.0.1',
        "-v ${serviceWorkspacePath()}:/workspace",
        '-w /workspace',
        '-v /tmp/via-logs:/tmp/via-logs',
        '-e OPENAI_API_KEY',
        '-e NGC_API_KEY',
        '-e NVIDIA_API_KEY',
        '-e VIA_VLM_API_KEY',
        "-e LVS_LLM_MODEL_NAME=${llmModelName}",
        "-e LVS_DATABASE_BACKEND=${lvsDatabaseBackend}",
        "-e ES_HOST=${esHost}",
        "-e ES_PORT=${esPort}",
        '-e HF_TOKEN',
        "-e PYTHONPATH=/workspace:/opt/tritonserver/backends/dali/wheel/dali:/workspace/src",
        "-e PIP_INDEX_URL=https://urm.nvidia.com/artifactory/api/pypi/nv-shared-pypi/simple",
        "-e DISABLE_GUARDRAILS=true",
        "-e LVS_LLM_BASE_URL=${llmBaseUrl}",
        '-e ARTIFACTORY_USER',
        '-e ARTIFACTORY_TOKEN',
    ]

    // ── Suite 1: RTVI integration tests ──────────────────────────────────────
    def rtviDockerRunArgs = baseDockerRunArgs + ["${dockerImage}"]
    def rtviTestCommand = '-c "pip install pytest-timeout coverage sseclient-py -q && ' +
        'python3 -c \'import os, sys; [os.makedirs(p, exist_ok=True) for p in sys.argv[1:]]\' /workspace/coverage_reports /workspace/htmlcov-rtvi-integ && ' +
        'USE_RTVI_VLM=true coverage run --data-file=/workspace/.coverage.rtvi-integ ' +
            '-m pytest tests/integration/test_rtvi_integration.py ' +
            '--timeout=600 -vv --tb=short -ra ' +
            '--junit-xml=/workspace/test_rtvi_integration-report.xml && ' +
        'coverage combine --data-file=/workspace/.coverage.rtvi-integ || true && ' +
        'coverage xml --data-file=/workspace/.coverage.rtvi-integ -o /workspace/coverage_reports/coverage-rtvi-integ.xml && ' +
        'coverage html --data-file=/workspace/.coverage.rtvi-integ -d /workspace/htmlcov-rtvi-integ && ' +
        'coverage report --data-file=/workspace/.coverage.rtvi-integ > /workspace/coverage_reports/coverage-rtvi-integ-summary.txt && ' +
        'python3 /workspace/ci/utils/convert_junit_to_csv.py /workspace/test_rtvi_integration-report.xml /workspace/rtvi-integration-test-results.csv"'

    // ── Suite 2: CA RAG integration tests ────────────────────────────────────
    // Fresh container avoids the Prometheus CollectorRegistry collision with suite 1.
    // RTVI_VLM_URL is required so ViaTestServer connects to the real RTVI VLM
    // instead of timing out against the default http://localhost:8000.
    def caRagDockerRunArgs = baseDockerRunArgs.collect()
    if (rtviVlmUrl) {
        caRagDockerRunArgs.add("-e RTVI_VLM_URL=${rtviVlmUrl}")
    }
    caRagDockerRunArgs.add("${dockerImage}")
    def caRagTestCommand = '-c "pip install pytest-timeout coverage sseclient-py -q && ' +
        'python3 -c \'import os, sys; [os.makedirs(p, exist_ok=True) for p in sys.argv[1:]]\' /workspace/coverage_reports /workspace/htmlcov-integ && ' +
        'coverage run --data-file=/workspace/.coverage.integ ' +
            '-m pytest tests/integration/test_ca_rag_integration.py ' +
            '--timeout=600 -vv --tb=short -ra ' +
            '--junit-xml=/workspace/test_ca_rag_integration-report.xml && ' +
        'coverage combine --data-file=/workspace/.coverage.integ || true && ' +
        'coverage xml --data-file=/workspace/.coverage.integ -o /workspace/coverage_reports/coverage-integ.xml && ' +
        'coverage html --data-file=/workspace/.coverage.integ -d /workspace/htmlcov-integ && ' +
        'coverage report --data-file=/workspace/.coverage.integ > /workspace/coverage_reports/coverage-integ-summary.txt && ' +
        'python3 /workspace/ci/utils/convert_junit_to_csv.py /workspace/test_ca_rag_integration-report.xml /workspace/integration-test-results.csv"'

    // Run both containers; accumulate RC so both always execute even if suite 1 fails.
    def rtviScript  = runTestsInDocker(useSudo, rtviDockerRunArgs,  rtviTestCommand)
    def caRagScript = runTestsInDocker(useSudo, caRagDockerRunArgs, caRagTestCommand)
    return """
INTEGRATION_RC=0
${rtviScript.trim()} || INTEGRATION_RC=1
${caRagScript.trim()} || INTEGRATION_RC=1
exit \$INTEGRATION_RC
"""
}

/**
 * Streaming RTVI -> Kafka -> Logstash -> Elasticsearch end-to-end test.
 *
 * Lifecycle (idempotent, with restore-on-exit):
 *   1. Bring down the existing BlueprintBuilderGenerated compose stack that
 *      previous tests left running.
 *   2. Patch `configmaps/config.yaml` to set `kafka_enabled: true` under
 *      `functions.summarization_online.params`. The original is backed up
 *      to `configmaps/config.yaml.kafka-e2e.bak` and restored at the end of
 *      the function (success or failure).
 *   3. Bring the stack back up with `COMPOSE_PROFILES=rtvi,kafka`,
 *      `USE_RTVI_VLM=true`, `KAFKA_ENABLED=true` so the new services
 *      (kafka, logstash) and the rtvi-vlm sidecar all start. The visionllm
 *      index template is registered by lvs at startup
 *      (ElasticsearchDBTool._ensure_index_template) — no separate bootstrap
 *      container is required.
 *   4. Wait for healthy services, then run the pytest opt-in inside a
 *      docker container with --network host so it can reach
 *      localhost:38111 (lvs) and localhost:9200 (elasticsearch).
 *   5. Always-block: bring the stack down again and restore the config file.
 *
 * Skipped at the call-site when `params.RUN_KAFKA_E2E != 'true'` (or
 * env.KAFKA_E2E_TEST != '1') so the integration stage is not slowed down
 * by default.
 *
 * @param useSudo         pass through to compose helpers
 * @param envCredentials  same shape used by runIntegrationTest
 * @param dockerImage     Full LVS image tag for the pytest container
 * @param composeFilePath Absolute path to the BlueprintBuilderGenerated
 *                        compose file. Defaults to the standard CI location.
 */
def runKafkaLogstashE2ETest(boolean useSudo, Map envCredentials, String dockerImage,
                            String composeFilePath = null) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        composeFilePath = "${serviceWorkspacePath()}/compose/BlueprintBuilderGenerated/docker-compose.yml"
    }
    def composed = parseComposePath(composeFilePath)
    def composeDir = composed.dir
    def composeFile = composed.file
    def composeCmd = buildDockerComposeCommand(
        useSudo,
        envCredentials.ngcApiKey,
        envCredentials.nvidiaApiKey,
        envCredentials.openaiApiKey,
        envCredentials.hfToken,
        composeFile,
        envCredentials.artifactoryUser,
        envCredentials.artifactoryToken
    )
    def configFile = "${composeDir}/configmaps/config.yaml"
    def configBak  = "${composeDir}/configmaps/config.yaml.kafka-e2e.bak"

    // Read RTVI connection config from the sibling .env so the pytest container
    // targets the same RTVI instance the compose stack's LVS uses.
    //
    // RTVI_VLM_URL (explicit full URL) takes precedence over RTVI_VLM_PORT when
    // both are set — matching how the test module resolves RTVI_BASE_URL:
    //   RTVI_BASE_URL = os.environ.get("RTVI_VLM_URL", f"http://localhost:{RTVI_VLM_PORT}")
    // When .env sets RTVI_VLM_URL to an external host,
    // LVS inside the stack talks to that host; the pytest container must too.
    def envMap = parseEnvFile("${composeDir}/.env")
    def rtviVlmUrl  = (envMap?.get('RTVI_VLM_URL')  ?: '')
    def rtviVlmPort = (envMap?.get('RTVI_VLM_PORT') ?: '8420')

    // Step 1+2+3: take the running stack down, patch config.yaml, replace
    // the hardcoded LVS image with the pipeline-built tag, and bring the
    // stack back up with the kafka profile.
    def composeBak = "${composeDir}/${composeFile}.kafka-e2e.bak"
    withDockerComposeEnvironment(
        envCredentials.ngcApiKey,
        envCredentials.nvidiaApiKey,
        envCredentials.openaiApiKey,
        envCredentials.hfToken,
        envCredentials.artifactoryUser,
        envCredentials.artifactoryToken
    ) {
        sh """
        set -eu
        cd ${composeDir}

    echo "[KAFKA-E2E] Stopping the existing BlueprintBuilderGenerated stack…"
    ${composeCmd} --profile rtvi --profile kafka down -v --remove-orphans || true

    echo "[KAFKA-E2E] Replacing LVS image in ${composeFile} with: ${dockerImage}"
    cp -f "${composeFile}" "${composeBak}"
    sed -i 's|image: nvcr.io/.*/vss-video-summarization:.*|image: ${dockerImage}|g' ${composeFile}
    echo "[KAFKA-E2E] Image replacement done. Verifying:"
    grep 'image:' ${composeFile} | head -5

    echo "[KAFKA-E2E] Patching ${configFile} -> kafka_enabled: true under summarization_online.params"
    cp -f "${configFile}" "${configBak}"
    python3 - <<'PYEOF'
import yaml, sys
path = "${configFile}"
with open(path, "r") as fh:
    data = yaml.safe_load(fh)
fns = (data or {}).get("functions") or {}
sumo = fns.get("summarization_online")
if sumo is None:
    print(f"ERROR: functions.summarization_online not found in {path}", file=sys.stderr)
    sys.exit(1)
sumo.setdefault("params", {})["kafka_enabled"] = True
with open(path, "w") as fh:
    yaml.safe_dump(data, fh, sort_keys=False)
print(f"[KAFKA-E2E] kafka_enabled=true written to {path}")
PYEOF

    echo "[KAFKA-E2E] Bringing the stack back up with rtvi+kafka profiles…"
    export USE_RTVI_VLM=true
    export KAFKA_ENABLED=true
    export COMPOSE_PROFILES=rtvi,kafka
    export KAFKA_BOOTSTRAP_SERVERS=kafka:9092
    export KAFKA_TOPIC=mdx-vlm-captions
    export KAFKA_STRUCTURED_SUMMARY_TOPIC=mdx-structured-events-summary
    export LVS_DATABASE_BACKEND=elasticsearch_db
    export LVS_EMB_ENABLE=false
    export LVS_EMB_DIMENSIONS=1024

    ${composeCmd} --profile rtvi --profile kafka build logstash
    ${composeCmd} --profile rtvi --profile kafka up -d

    echo "[KAFKA-E2E] Waiting for services to become healthy (timeout 25 min)…"
    ${composeCmd} --profile rtvi --profile kafka up --wait --wait-timeout 1500 --no-recreate
        ${composeCmd} --profile rtvi --profile kafka ps
        """
    }

    def dockerRunArgs = [
        '--user root',
        '--entrypoint bash',
        '--network host',
        '--gpus all',
        '--runtime=nvidia',
        '--add-host=host.docker.internal:127.0.0.1',
        "-v ${serviceWorkspacePath()}:/workspace",
        '-w /workspace',
        '-v /tmp/via-logs:/tmp/via-logs',
        '-e KAFKA_E2E_TEST=1',
        '-e LVS_BACKEND_PORT=38111',
        "-e RTVI_VLM_PORT=${rtviVlmPort}",
        '-e ES_HOST=localhost',
        '-e ES_PORT=9200',
        // Keep CI media inputs on Artifactory; do not require the media profile.
        '-e KAFKA_E2E_FILE_URL=https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/lmm/streams/warehouse_gopro_1m_720.mp4',
        // Secrets inherited from the parent shell at docker-run time (set via
        // withEnv in the caller) — keeps the rendered script literal free of
        // plaintext secrets in the Blue Ocean step header.
        '-e ARTIFACTORY_USER',
        '-e ARTIFACTORY_TOKEN',
        '-e PYTHONPATH=/workspace:/workspace/src',
        '-e PIP_INDEX_URL=https://urm.nvidia.com/artifactory/api/pypi/nv-shared-pypi/simple',
        "${dockerImage}",
    ]
    // Forward RTVI_VLM_URL only when set in .env; the test falls back to
    // http://localhost:${RTVI_VLM_PORT} when the var is absent, which is
    // correct for pure in-stack container setups.  When .env points LVS at an
    // external RTVI, the pytest
    // container must target that same host — not localhost — so that stream/add
    // and health-check calls land on the same RTVI instance LVS is using.
    if (rtviVlmUrl) {
        dockerRunArgs.add(dockerRunArgs.size() - 1, "-e RTVI_VLM_URL=${rtviVlmUrl}")
    }
    def testCommand = '-c "pip install pytest-timeout coverage requests -q && ' +
        'python3 -c \'import os, sys; [os.makedirs(p, exist_ok=True) for p in sys.argv[1:]]\' /workspace/coverage_reports /workspace/htmlcov-kafka-e2e && ' +
        'RC=0; ' +
        'KAFKA_E2E_TEST=1 coverage run --data-file=/workspace/.coverage.kafka-e2e ' +
        '  -m pytest tests/integration/test_kafka_logstash_e2e.py ' +
        '  --timeout=1800 -vv --tb=short -ra ' +
        '  --junit-xml=/workspace/test_kafka_logstash_e2e-report.xml && ' +
        'coverage combine --data-file=/workspace/.coverage.kafka-e2e || true && ' +
        'coverage xml --data-file=/workspace/.coverage.kafka-e2e -o /workspace/coverage_reports/coverage-kafka-e2e.xml && ' +
        'coverage html --data-file=/workspace/.coverage.kafka-e2e -d /workspace/htmlcov-kafka-e2e && ' +
        'coverage report --data-file=/workspace/.coverage.kafka-e2e > /workspace/coverage_reports/coverage-kafka-e2e-summary.txt && ' +
        'python3 /workspace/ci/utils/convert_junit_to_csv.py /workspace/test_kafka_logstash_e2e-report.xml /workspace/kafka-e2e-test-results.csv ' +
        '|| RC=1; exit \\$RC"'

    def testRC = 0
    try {
        testRC = runTestsInDocker(useSudo, dockerRunArgs, testCommand)
    } finally {
        // Step 5: always tear down the kafka-test stack and restore both
        // config.yaml and docker-compose.yml — leaving mutated files or a
        // running stack would break the next stage.
        withDockerComposeEnvironment(
            envCredentials.ngcApiKey,
            envCredentials.nvidiaApiKey,
            envCredentials.openaiApiKey,
            envCredentials.hfToken,
            envCredentials.artifactoryUser,
            envCredentials.artifactoryToken
        ) {
            sh """
            set +e
            cd ${composeDir}
            echo "[KAFKA-E2E] Tearing down the kafka-e2e stack…"
            ${composeCmd} --profile rtvi --profile kafka down -v --remove-orphans || true
            if [ -f "${configBak}" ]; then
                echo "[KAFKA-E2E] Restoring ${configFile} from backup"
                mv -f "${configBak}" "${configFile}"
            fi
            if [ -f "${composeBak}" ]; then
                echo "[KAFKA-E2E] Restoring ${composeFile} from backup"
                mv -f "${composeBak}" "${composeFile}"
            fi
            """
        }
    }
    return testRC
}

/**
 * RTVI + LVS file-path end-to-end sanity test.
 *
 * Python pytest equivalent of ``run_sanity.sh``. Runs against the shared
 * RTVI-VLM via a fresh BlueprintBuilderGenerated LVS stack (no Kafka, no
 * ES shard throttling). Video is fetched by LVS directly from Artifactory
 * using the bearer token already scoped by withDockerComposeEnvironment —
 * no media-server profile needed.
 *
 * Steps:
 *   1. Tear down any pre-existing stack.
 *   2. Replace the LVS image in docker-compose.yml with ``dockerImage``.
 *   3. Bring the stack up with RTVI_VLM_URL=<shared>, KAFKA_ENABLED=false,
 *      LVS_DATABASE_BACKEND=elasticsearch_db.
 *   4. Run pytest in a fresh container with RTVI_E2E_TEST=1.
 *   5. Always-block: tear down the stack and restore docker-compose.yml
 *      from backup so subsequent stages see the repo-committed file.
 *
 * Runs unconditionally on amd64; only ``params.SKIP_RTVI_E2E == 'true'``
 * skips it (emergency escape hatch). Must execute BEFORE runEsShardLimitTest
 * so the ES shard regression always follows a known-good sanity pass.
 *
 * @param useSudo         pass through to compose helpers
 * @param envCredentials  same shape used by runIntegrationTest
 * @param dockerImage     Full LVS image tag for the pytest container
 * @param composeFilePath Absolute path to the BlueprintBuilderGenerated
 *                        compose file. Defaults to the standard CI location.
 */
def runRtviE2ETest(boolean useSudo, Map envCredentials, String dockerImage,
                   String composeFilePath = null) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        composeFilePath = "${serviceWorkspacePath()}/compose/BlueprintBuilderGenerated/docker-compose.yml"
    }
    def composed = parseComposePath(composeFilePath)
    def composeDir = composed.dir
    def composeFile = composed.file
    def sharedRtviUrl = env.SHARED_RTVI_VLM_URL ?: ''
    if (!sharedRtviUrl) {
        error("[RTVI-E2E] SHARED_RTVI_VLM_URL is required so the test can reach the shared RTVI-VLM.")
    }

    def composeCmd = buildDockerComposeCommand(
        useSudo,
        envCredentials.ngcApiKey,
        envCredentials.nvidiaApiKey,
        envCredentials.openaiApiKey,
        envCredentials.hfToken,
        composeFile,
        envCredentials.artifactoryUser,
        envCredentials.artifactoryToken
    )
    def composeBak = "${composeDir}/${composeFile}.rtvi-e2e.bak"
    def sudoCmd = useSudo ? 'sudo ' : ''

    withDockerComposeEnvironment(
        envCredentials.ngcApiKey,
        envCredentials.nvidiaApiKey,
        envCredentials.openaiApiKey,
        envCredentials.hfToken,
        envCredentials.artifactoryUser,
        envCredentials.artifactoryToken
    ) {
        sh """
        set -eu
        cd ${composeDir}

    # media-server.yaml declares via-media-data as an external volume.
    # Ensure it exists before compose up (mirrors preCleanupDockerCompose).
    if ${sudoCmd}docker volume inspect via-media-data >/dev/null 2>&1; then
        echo "[RTVI-E2E] Docker volume 'via-media-data' already exists"
    else
        ${sudoCmd}docker volume create via-media-data >/dev/null
        echo "[RTVI-E2E] Created Docker volume 'via-media-data'"
    fi

    echo "[RTVI-E2E] Tearing down any existing stack..."
    ${composeCmd} down -v --remove-orphans || true

    echo "[RTVI-E2E] Replacing LVS image in ${composeFile} with: ${dockerImage}"
    cp -f "${composeFile}" "${composeBak}"
    sed -i 's|image: nvcr.io/.*/vss-video-summarization:.*|image: ${dockerImage}|g' ${composeFile}
    grep 'image:' ${composeFile} | head -5

    echo "[RTVI-E2E] Bringing stack up (shared RTVI_VLM_URL=${sharedRtviUrl})..."
    export RTVI_VLM_URL=${sharedRtviUrl}
    export KAFKA_ENABLED=false
    export LVS_DATABASE_BACKEND=elasticsearch_db
    export LVS_EMB_ENABLE=false
    export LVS_EMB_DIMENSIONS=1024

    ${composeCmd} up -d
    ${composeCmd} up --wait --wait-timeout 1500 --no-recreate
        ${composeCmd} ps
        """
    }

    def envMap = parseEnvFile("${composeDir}/.env")
    def rtviVlmPort = envMap?.get('RTVI_VLM_PORT') ?: '8420'

    def dockerRunArgs = [
        '--user root',
        '--entrypoint bash',
        '--network host',
        '--gpus all',
        '--runtime=nvidia',
        '--add-host=host.docker.internal:127.0.0.1',
        "-v ${serviceWorkspacePath()}:/workspace",
        '-w /workspace',
        '-v /tmp/via-logs:/tmp/via-logs',
        '-e RTVI_E2E_TEST=1',
        '-e LVS_BACKEND_PORT=38111',
        "-e RTVI_VLM_URL=${sharedRtviUrl}",
        "-e RTVI_VLM_PORT=${rtviVlmPort}",
        // LVS fetches the video directly from Artifactory using ASSET_DOWNLOAD_AUTH_TOKENS
        // (scoped into the compose stack by withDockerComposeEnvironment). The pytest container
        // just needs to know the URL to pass in the summarize request body.
        '-e RTVI_E2E_FILE_URL=https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/via-engine/media/perf/reencode/2min.mp4',
        // Secrets inherited from parent shell at docker-run time (set via
        // withEnv around the sh() call below) — keeps the rendered script
        // literal free of plaintext secrets in the Blue Ocean step header.
        '-e ARTIFACTORY_USER',
        '-e ARTIFACTORY_TOKEN',
        '-e PYTHONPATH=/workspace:/workspace/src',
        '-e PIP_INDEX_URL=https://urm.nvidia.com/artifactory/api/pypi/nv-shared-pypi/simple',
        "${dockerImage}",
    ]

    def testCommand = '-c "pip install pytest-timeout coverage requests -q && ' +
        'python3 -c \'import os, sys; [os.makedirs(p, exist_ok=True) for p in sys.argv[1:]]\' /workspace/coverage_reports /workspace/htmlcov-rtvi-e2e && ' +
        'RC=0; ' +
        'RTVI_E2E_TEST=1 coverage run --data-file=/workspace/.coverage.rtvi-e2e ' +
        '  -m pytest tests/integration/test_rtvi_e2e.py ' +
        '  --timeout=600 -vv --tb=short -ra ' +
        '  --junit-xml=/workspace/test_rtvi_e2e-report.xml && ' +
        'coverage combine --data-file=/workspace/.coverage.rtvi-e2e || true && ' +
        'coverage xml --data-file=/workspace/.coverage.rtvi-e2e -o /workspace/coverage_reports/coverage-rtvi-e2e.xml && ' +
        'coverage html --data-file=/workspace/.coverage.rtvi-e2e -d /workspace/htmlcov-rtvi-e2e && ' +
        'coverage report --data-file=/workspace/.coverage.rtvi-e2e > /workspace/coverage_reports/coverage-rtvi-e2e-summary.txt && ' +
        'python3 /workspace/ci/utils/convert_junit_to_csv.py /workspace/test_rtvi_e2e-report.xml /workspace/rtvi-e2e-test-results.csv ' +
        '|| RC=1; exit \\$RC"'

    def testRC = 0
    try {
        testRC = withEnv([
            "ARTIFACTORY_USER=${envCredentials.artifactoryUser ?: ''}",
            "ARTIFACTORY_TOKEN=${envCredentials.artifactoryToken ?: ''}",
        ]) {
            sh(
                script: runTestsInDocker(useSudo, dockerRunArgs, testCommand),
                returnStatus: true,
            )
        }
    } finally {
        withDockerComposeEnvironment(
            envCredentials.ngcApiKey,
            envCredentials.nvidiaApiKey,
            envCredentials.openaiApiKey,
            envCredentials.hfToken,
            envCredentials.artifactoryUser,
            envCredentials.artifactoryToken
        ) {
            sh """
            set +e
            cd ${composeDir}
            echo "[RTVI-E2E] Tearing down stack..."
            ${composeCmd} down -v --remove-orphans || true
            if [ -f "${composeBak}" ]; then
                echo "[RTVI-E2E] Restoring ${composeFile} from backup"
                mv -f "${composeBak}" "${composeFile}"
            fi
            """
        }
    }
    return testRC
}

/**
 * ES shard exhaustion + index-lifecycle regression suite.
 *
 * Runs ``tests/integration/test_es_shard_limit.py`` against the
 * BlueprintBuilderGenerated stack with the ES shard cap set deliberately
 * low (default 2) so the bug's failure mode reproduces in seconds.
 *
 * Two scenarios live in the pytest module, gated by markers
 * (``retain_mode`` / ``drop_mode``). They cannot share a stack because
 * ``LVS_DISABLE_DB_RESET_ON_REQUEST_DONE`` is read at LVS container
 * start, so this helper runs the full stack lifecycle TWICE:
 *
 *   Phase A -- retain mode
 *     LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=true,
 *     ES_MAX_SHARDS_PER_NODE=2.
 *     ``-m retain_mode`` -- assert sequential summarizes succeed up to
 *     the cap, then HTTP 503 with the classified shard-limit message.
 *
 *   Phase B -- drop mode
 *     LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=false (unset),
 *     ES_MAX_SHARDS_PER_NODE=2.
 *     ``-m drop_mode`` -- assert N >> cap sequential summarizes ALL
 *     succeed because each completion drops its per-file index.
 *
 * Steps per phase (mirrors runKafkaLogstashE2ETest):
 *   1. Tear down any pre-existing stack.
 *   2. Replace the LVS image in docker-compose.yml with ``dockerImage``.
 *   3. Bring the non-RTVI stack up with the phase-specific env overrides.
 *      The shared RTVI-VLM container is reused via RTVI_VLM_URL.
 *   4. Wait for healthy services (compose ``up --wait``).
 *   5. Run pytest in a fresh container with the matching marker.
 *   6. Tear down before moving to the next phase.
 *   7. Always-block: restore the docker-compose.yml backup so
 *      subsequent CI stages start from a clean tree.
 *
 * Skipped at the call-site when ``params.SKIP_ES_SHARD_LIMIT == 'true'``
 * (the only escape hatch -- see runIntegrationTests below). This is the
 * dedicated regression for the very bug we're fixing; it must run on
 * every integration pipeline so a future regression cannot sneak past.
 *
 * @param useSudo         pass through to compose helpers
 * @param envCredentials  same shape used by runIntegrationTest
 * @param dockerImage     Full LVS image tag for the pytest container
 * @param composeFilePath Absolute path to the BlueprintBuilderGenerated
 *                        compose file. Defaults to the standard CI
 *                        location.
 */
def runEsShardLimitTest(boolean useSudo, Map envCredentials, String dockerImage,
                        String composeFilePath = null) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        composeFilePath = "${serviceWorkspacePath()}/compose/BlueprintBuilderGenerated/docker-compose.yml"
    }
    def composed = parseComposePath(composeFilePath)
    def composeDir = composed.dir
    def composeFile = composed.file
    def sharedRtviUrl = env.SHARED_RTVI_VLM_URL ?: ''
    if (!sharedRtviUrl) {
        error("[ES-SHARD-LIMIT] SHARED_RTVI_VLM_URL is required so retain/drop phases can reuse shared RTVI-VLM.")
    }

    def composeCmd = buildDockerComposeCommand(
        useSudo,
        envCredentials.ngcApiKey,
        envCredentials.nvidiaApiKey,
        envCredentials.openaiApiKey,
        envCredentials.hfToken,
        composeFile,
        envCredentials.artifactoryUser,
        envCredentials.artifactoryToken
    )
    def composeBak = "${composeDir}/${composeFile}.es-shard-limit.bak"

    // ES_MAX_SHARDS_PER_NODE=2 is intentionally tiny so the cap is hit
    // after just two summarizes in retain mode. Drop mode runs many
    // more (test_es_shard_limit.py defaults to 4*cap = 8 requests) to
    // demonstrate the cluster shard pool stays drained.
    def esShardCap = '2'

    def sudoCmd = useSudo ? 'sudo ' : ''
    def overallRC = 0

    withDockerComposeEnvironment(
        envCredentials.ngcApiKey,
        envCredentials.nvidiaApiKey,
        envCredentials.openaiApiKey,
        envCredentials.hfToken,
        envCredentials.artifactoryUser,
        envCredentials.artifactoryToken
    ) {
        sh """
        set -eu
        cd ${composeDir}

    # media-server.yaml declares via-media-data as an external volume.
    # Ensure it exists before compose up (mirrors preCleanupDockerCompose).
    if ${sudoCmd}docker volume inspect via-media-data >/dev/null 2>&1; then
        echo "[ES-SHARD-LIMIT] Docker volume 'via-media-data' already exists"
    else
        ${sudoCmd}docker volume create via-media-data >/dev/null
        echo "[ES-SHARD-LIMIT] Created Docker volume 'via-media-data'"
    fi

    echo "[ES-SHARD-LIMIT] Replacing LVS image in ${composeFile} with: ${dockerImage}"
    cp -f "${composeFile}" "${composeBak}"
    sed -i 's|image: nvcr.io/.*/vss-video-summarization:.*|image: ${dockerImage}|g' ${composeFile}
        grep 'image:' ${composeFile} | head -5
        """

        try {
            // Two ordered phases. Each gets a fresh stack with the matching
            // LVS_DISABLE_DB_RESET_ON_REQUEST_DONE override.
            def phases = [
                [name: 'retain', disableReset: 'true',  marker: 'retain_mode'],
                [name: 'drop',   disableReset: 'false', marker: 'drop_mode'],
            ]
            for (phase in phases) {
                echo "[ES-SHARD-LIMIT] === phase: ${phase.name} (disable_reset=${phase.disableReset}) ==="

                sh """
                set -eu
                cd ${composeDir}

            echo "[ES-SHARD-LIMIT][${phase.name}] Tearing down any existing stack..."
            ${composeCmd} down -v --remove-orphans || true

            echo "[ES-SHARD-LIMIT][${phase.name}] Bringing the stack up with cap=${esShardCap}, disable_reset=${phase.disableReset}"
            export ES_MAX_SHARDS_PER_NODE=${esShardCap}
            export LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=${phase.disableReset}
            export USE_RTVI_VLM=true
            export RTVI_VLM_URL=${sharedRtviUrl}
            export KAFKA_ENABLED=false
            export LVS_DATABASE_BACKEND=elasticsearch_db
            export LVS_EMB_ENABLE=false
            export LVS_EMB_DIMENSIONS=1024

            ${composeCmd} up -d
            ${composeCmd} up --wait --wait-timeout 1500 --no-recreate
                ${composeCmd} ps
                """

                def dockerRunArgs = [
                '--user root',
                '--entrypoint bash',
                '--network host',
                '--gpus all',
                '--runtime=nvidia',
                '--add-host=host.docker.internal:127.0.0.1',
                "-v ${serviceWorkspacePath()}:/workspace",
                '-w /workspace',
                '-v /tmp/via-logs:/tmp/via-logs',
                '-e ES_SHARD_LIMIT_TEST=1',
                "-e ES_MAX_SHARDS_PER_NODE=${esShardCap}",
                "-e LVS_DISABLE_DB_RESET_ON_REQUEST_DONE=${phase.disableReset}",
                '-e LVS_BACKEND_PORT=38111',
                '-e ES_HOST=localhost',
                '-e ES_PORT=9200',
                "-e RTVI_VLM_URL=${sharedRtviUrl}",
                // Keep CI media inputs on Artifactory; do not require the media profile.
                '-e SHARD_LIMIT_FILE_URL=https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/lmm/streams/warehouse_gopro_1m_720.mp4',
                // Secrets inherited from parent shell at docker-run time (set via
                // withEnv around the sh() call below) — keeps the rendered script
                // literal free of plaintext secrets in the Blue Ocean step header.
                '-e ARTIFACTORY_USER',
                '-e ARTIFACTORY_TOKEN',
                '-e PYTHONPATH=/workspace:/workspace/src',
                '-e PIP_INDEX_URL=https://urm.nvidia.com/artifactory/api/pypi/nv-shared-pypi/simple',
                "${dockerImage}",
            ]

                def reportPath = "/workspace/test_es_shard_limit-${phase.name}-report.xml"
                def coverageDataFile = "/workspace/.coverage.es-shard-limit-${phase.name}"
                def coverageXmlFile =
                    "/workspace/coverage_reports/coverage-es-shard-limit-${phase.name}.xml"
                def htmlcovDir = "/workspace/htmlcov-es-shard-limit-${phase.name}"

                def testCommand = '-c "pip install pytest-timeout coverage requests -q && ' +
                    'python3 -c \'import os, sys; [os.makedirs(p, exist_ok=True) for p in sys.argv[1:]]\' /workspace/coverage_reports ' + htmlcovDir + ' && ' +
                    'RC=0; ' +
                    "ES_SHARD_LIMIT_TEST=1 coverage run --data-file=${coverageDataFile} " +
                    "  -m pytest tests/integration/test_es_shard_limit.py -m ${phase.marker} " +
                    '  --timeout=1800 -vv --tb=short -ra ' +
                    "  --junit-xml=${reportPath} && " +
                    "coverage combine --data-file=${coverageDataFile} || true && " +
                    "coverage xml --data-file=${coverageDataFile} -o ${coverageXmlFile} && " +
                    "coverage html --data-file=${coverageDataFile} -d ${htmlcovDir} && " +
                    "coverage report --data-file=${coverageDataFile} > " +
                    "/workspace/coverage_reports/coverage-es-shard-limit-${phase.name}-summary.txt && " +
                    'python3 /workspace/ci/utils/convert_junit_to_csv.py ' +
                    "${reportPath} /workspace/es-shard-limit-${phase.name}-test-results.csv " +
                    '|| RC=1; exit \\$RC"'

            // ``runTestsInDocker`` only BUILDS a shell script string
            // (see the helper around line 1351); to actually execute
            // pytest we have to hand the script to ``sh`` here.
            // Earlier revisions of this helper assigned the string
            // directly to phaseRC, which left the test command unrun
            // and made the stage report success while the regression
            // never ran.
            //
            // withEnv binds the artifactory creds for the parent shell so the
            // bare `-e ARTIFACTORY_USER` / `-e ARTIFACTORY_TOKEN` flags inside
            // dockerRunArgs inherit them at docker-run time, without baking the
            // values into the script literal Blue Ocean displays.
                def phaseRC = withEnv([
                    "ARTIFACTORY_USER=${envCredentials.artifactoryUser ?: ''}",
                    "ARTIFACTORY_TOKEN=${envCredentials.artifactoryToken ?: ''}",
                ]) {
                    sh(
                        script: runTestsInDocker(useSudo, dockerRunArgs, testCommand),
                        returnStatus: true,
                    )
                }
                overallRC = overallRC ?: phaseRC

                sh """
                set +e
                cd ${composeDir}
                echo "[ES-SHARD-LIMIT][${phase.name}] Tearing down stack..."
                ${composeCmd} down -v --remove-orphans || true
                """
            }
        } finally {
            sh """
            set +e
            cd ${composeDir}
            if [ -f "${composeBak}" ]; then
                echo "[ES-SHARD-LIMIT] Restoring ${composeFile} from backup"
                mv -f "${composeBak}" "${composeFile}"
            fi
            """
        }
    }
    return overallRC
}

/**
 * Runs all tests via pytest against a running VIA service.
 * Tests are marked with @pytest.mark.test_in_ci and run in categories.
 *
 * @param useSudo Whether to use sudo for docker commands (default: true for bare metal, false for DinD)
 * @param debugTests When true, capture LVS container logs before/during/after tests and archive (for debugging connection/service issues)
 * @param envCredentials Map of API keys and config (openaiApiKey, ngcApiKey, nvidiaApiKey, hfToken, dbHost, dbPort, grpcPort, lvsDatabaseBackend)
 * @param dockerImage Full docker image tag to run CA RAG integration tests in (e.g. from getImageTag())
 */
def runServiceTestSuite(boolean useSudo = true, boolean debugTests = false, Map envCredentials, String dockerImage, boolean runFunctional = true, boolean runIntegration = true) {
    def sudoCmd = useSudo ? 'sudo ' : ''
    def composeDir = "${serviceWorkspacePath()}/compose/BlueprintBuilderGenerated"

    def pytestCmd = ''
    if (runFunctional) {
        pytestCmd += runFunctionalTest(useSudo)
    }

    if (runIntegration) {
        def composeFilePath = "${composeDir}/docker-compose.yml"
        def lvsDbEnv = getLvsDatabaseEnvFromCompose(composeFilePath)
        def lvsDatabaseBackend = lvsDbEnv.lvsDatabaseBackend ?: ''
        def esPort = lvsDbEnv.esPort ?: ''
        def esHost = ''
        def llmBaseUrl = lvsDbEnv.llmBaseUrl ?: ''
        def llmModelName = lvsDbEnv.llmModelName ?: ''

        // Catch unresolved Compose placeholders (e.g. raw "${LVS_DATABASE_BACKEND:-...}"
        // strings that slipped through). The helper now interpolates them, so this is
        // belt-and-braces against compose files that introduce new placeholders.
        if (lvsDatabaseBackend.contains('${')) {
            error("Integration tests: LVS_DATABASE_BACKEND in ${composeFilePath} did not fully interpolate (got '${lvsDatabaseBackend}'). " +
                  "Add the value to ${composeDir}/.env or set it in the compose env section.")
        }

        if (lvsDatabaseBackend == 'elasticsearch_db') {
            if (!esPort) {
                esPort = '9200'
                echo "WARNING: ES_PORT not found in compose file; using default 9200"
            }
            esHost = 'host.docker.internal'
        } else if (!lvsDatabaseBackend) {
            error("Integration tests require LVS_DATABASE_BACKEND in ${composeFilePath} (or its sibling .env) to resolve to a non-empty value. " +
                  "Got ''. Check the lvs.environment section.")
        } else {
            error("Integration tests only support LVS_DATABASE_BACKEND=elasticsearch_db, but got '${lvsDatabaseBackend}' from ${composeFilePath}.")
        }

        if (!llmBaseUrl || hasUnresolvedComposePlaceholder(llmBaseUrl)) {
            error("Integration tests require a fully-resolved LVS_LLM_BASE_URL but got '${llmBaseUrl}'. " +
                  "Set LVS_LLM_HOST/LVS_LLM_PORT in ${composeDir}/.env (or set LVS_LLM_BASE_URL directly) " +
                  "so that ${composeFilePath} interpolates to a real http://host:port/v1 URL.")
        }

        if (!llmModelName || hasUnresolvedComposePlaceholder(llmModelName)) {
            error("Integration tests require a fully-resolved LVS_LLM_MODEL_NAME but got '${llmModelName}'. " +
                  "Set LVS_LLM_MODEL_NAME in ${composeDir}/.env so ViaTestServer uses a model served by ${llmBaseUrl}.")
        }

        echo "Integration test config: LVS_DATABASE_BACKEND=${lvsDatabaseBackend}, ES_HOST=${esHost}, ES_PORT=${esPort}, LVS_LLM_BASE_URL=${llmBaseUrl}, LVS_LLM_MODEL_NAME=${llmModelName}"

        // Shared RTVI mode points both the compose LVS service and host-network
        // pytest containers at the same long-lived RTVI-VLM endpoint.
        def envMap = parseEnvFile("${composeDir}/.env")
        def rtviVlmPort = envMap?.get('RTVI_VLM_PORT') ?: '8000'
        def rtviVlmUrl = env.SHARED_RTVI_VLM_URL ?: envMap?.get('RTVI_VLM_URL') ?: "http://localhost:${rtviVlmPort}"
        if (env.SHARED_RTVI_VLM_URL) {
            echo "CA RAG integration tests will use shared RTVI_VLM_URL=${rtviVlmUrl}"
        } else if (envMap?.get('RTVI_VLM_URL')) {
            echo "CA RAG integration tests will use external RTVI_VLM_URL=${rtviVlmUrl}"
        } else {
            echo "CA RAG integration tests will use in-stack RTVI-VLM via host URL ${rtviVlmUrl}"
        }

        pytestCmd += runIntegrationTest(useSudo, envCredentials, dockerImage, lvsDatabaseBackend, esHost, esPort, llmBaseUrl, llmModelName, rtviVlmUrl)
    }

    // Prepare test logs directory on host (for any other integration logs)
    sh 'mkdir -p /tmp/via-logs && chmod 777 /tmp/via-logs'

    // Export the secrets for inheritance by the docker run -e KEY (no-value)
    // flags in pytestCmd. This keeps the rendered sh script literal free of
    // plaintext secrets, which is what Blue Ocean displays as the step header.
    def testEnv = [
        "OPENAI_API_KEY=${envCredentials.openaiApiKey ?: ''}",
        "NGC_API_KEY=${envCredentials.ngcApiKey ?: ''}",
        "NVIDIA_API_KEY=${envCredentials.nvidiaApiKey ?: ''}",
        "VIA_VLM_API_KEY=${envCredentials.openaiApiKey ?: ''}",
        "HF_TOKEN=${envCredentials.hfToken ?: ''}",
        "ARTIFACTORY_USER=${envCredentials.artifactoryUser ?: ''}",
        "ARTIFACTORY_TOKEN=${envCredentials.artifactoryToken ?: ''}",
    ]
    withEnv(testEnv) {
        if (runFunctional) {
            runFunctionalArtifactoryDownloadPreflight(useSudo)
            runFunctionalLvsApiDiagnostic(useSudo)
        }
        if (debugTests) {
            echo "DEBUG: Capturing LVS logs before, during, and after tests..."
            sh """
            set +e
            COMPOSE_DIR="${composeDir}"
            echo "=== LVS logs (before tests) ==="
            cd "\$COMPOSE_DIR" && ${sudoCmd}docker compose logs lvs --tail=500 | tee ${serviceWorkspacePath()}/lvs-logs-before-tests.log
            cd "\$COMPOSE_DIR" && ${sudoCmd}docker compose logs -f lvs 2>&1 | tee ${serviceWorkspacePath()}/lvs-logs-during-tests.log &
            LOGPID=\$!
            cd ${serviceWorkspacePath()}
            ${pytestCmd}
            PYTEST_RC=\$?
            kill \$LOGPID 2>/dev/null || true
            echo "=== LVS logs (after tests) ==="
            cd "\$COMPOSE_DIR" && ${sudoCmd}docker compose logs lvs --tail=1000 > ${serviceWorkspacePath()}/lvs-logs-after-tests.log 2>&1
            exit \$PYTEST_RC
            """
        } else {
            sh pytestCmd
        }
    }

    // Archive shared coverage XML/JSON reports (produced by whichever tests ran)
    archiveArtifacts artifacts: 'coverage_reports/**', allowEmptyArchive: true

    if (runFunctional) {
        archiveArtifacts artifacts: 'pytest-report.api-tests.xml', allowEmptyArchive: true
        archiveArtifacts artifacts: 'functional-test-results.csv', allowEmptyArchive: true
    }
    if (runIntegration) {
        archiveArtifacts artifacts: 'test_ca_rag_integration-report.xml', allowEmptyArchive: true
        archiveArtifacts artifacts: 'integration-test-results.csv', allowEmptyArchive: true
        archiveArtifacts artifacts: 'htmlcov-integ/**', allowEmptyArchive: true
        archiveArtifacts artifacts: '.coverage.integ', allowEmptyArchive: true
        publishHTML(target: [
            reportDir: 'htmlcov-integ',
            reportFiles: 'index.html',
            reportName: 'Integration Test Coverage Report',
            keepAll: true,
            alwaysLinkToLastBuild: true
        ])
        archiveArtifacts artifacts: 'test_rtvi_integration-report.xml', allowEmptyArchive: true
        archiveArtifacts artifacts: 'rtvi-integration-test-results.csv', allowEmptyArchive: true
        archiveArtifacts artifacts: 'htmlcov-rtvi-integ/**', allowEmptyArchive: true
        archiveArtifacts artifacts: '.coverage.rtvi-integ', allowEmptyArchive: true
        publishHTML(target: [
            reportDir: 'htmlcov-rtvi-integ',
            reportFiles: 'index.html',
            reportName: 'RTVI Integration Test Coverage Report',
            keepAll: true,
            alwaysLinkToLastBuild: true
        ])
    }
    if (debugTests) {
        archiveArtifacts artifacts: 'lvs-logs-before-tests.log', allowEmptyArchive: true
        archiveArtifacts artifacts: 'lvs-logs-during-tests.log', allowEmptyArchive: true
        archiveArtifacts artifacts: 'lvs-logs-after-tests.log', allowEmptyArchive: true
    }
}

/**
 * Cleans up Docker Compose deployment after tests complete.
 * Shows final logs and tears down containers.
 *
 * @param useSudo Whether to use sudo for docker commands (default: true for bare metal, false for DinD)
 * @param composeFilePath Path to the docker compose file (e.g. "compose/h100-foo.yaml")
 */
def cleanupDockerCompose(boolean useSudo = true, String composeFilePath, String composeProfiles = null) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("cleanupDockerCompose: composeFilePath must be provided")
    }

    def composed = parseComposePath(composeFilePath)
    def sudoCmd = useSudo ? 'sudo ' : ''
    def composeCmd = buildDockerComposeCommand(useSudo, null, null, null, null, composed.file, null, null, composeProfiles)
    withDockerComposeEnvironment(null, null, null, null, null, null) {
        sh """
        echo "=========================================="
        echo "Cleaning up Docker Compose deployment"
        echo "=========================================="
        cd ${composed.dir}

    echo "[CLEANUP] docker ps -a before compose cleanup:"
    ${sudoCmd}docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}' || true
    echo "[CLEANUP] compose ps before cleanup:"
    ${composeCmd} ps || true

    # Show final logs before cleanup
    echo "Final service logs:"
    ${composeCmd} logs --tail=50 || true
    ${composeCmd} logs lvs --tail=500 || true

    # Cleanup
    ${composeCmd} down -v --remove-orphans || true
#    ${sudoCmd}rm -rf ${getNimCacheDir()} || true

    echo "[CLEANUP] compose ps after cleanup:"
    ${composeCmd} ps || true
    echo "[CLEANUP] docker ps -a after compose cleanup:"
    ${sudoCmd}docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}' || true
        echo "Cleanup completed"
        """
    }
}

/**
 * Handles Docker Compose deployment timeout or failure by collecting logs.
 *
 * @param useSudo Whether to use sudo for docker commands (default: true for bare metal, false for DinD)
 * @param composeFilePath Path to the docker compose file (e.g. "compose/h100-foo.yaml")
 */
def handleDockerComposeFailure(boolean useSudo = true, String composeFilePath, String composeProfiles = null) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("handleDockerComposeFailure: composeFilePath must be provided")
    }

    def composed = parseComposePath(composeFilePath)
    def sudoCmd = useSudo ? 'sudo ' : ''
    def composeCmd = buildDockerComposeCommand(useSudo, null, null, null, null, composed.file, null, null, composeProfiles)
    withDockerComposeEnvironment(null, null, null, null, null, null) {
        sh """
        cd ${composed.dir}
        echo "Container status at failure:"
        ${sudoCmd}docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}' || true
        echo "[FAILURE_DIAG] compose services:"
        ${composeCmd} config --services || true
        echo "[FAILURE_DIAG] compose logs (timestamps, tail=1000):"
        ${composeCmd} logs --timestamps --tail=1000 || true
        echo "[FAILURE_DIAG] host GPU snapshot:"
        ${sudoCmd}nvidia-smi || true
        """
    }
}

/**
 * Performs best-effort pre-cleanup before starting a new deployment.
 * This prevents stale containers from previous runs from affecting current results.
 *
 * @param useSudo Whether to use sudo for docker commands (default: true for bare metal, false for DinD)
 * @param composeFilePath Path to the docker compose file (e.g. "compose/h100-foo.yaml")
 */
def preCleanupDockerCompose(boolean useSudo = true, String composeFilePath, String composeProfiles = null) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("preCleanupDockerCompose: composeFilePath must be provided")
    }

    def composed = parseComposePath(composeFilePath)
    def sudoCmd = useSudo ? 'sudo ' : ''
    def composeCmd = buildDockerComposeCommand(useSudo, null, null, null, null, composed.file, null, null, composeProfiles)

    withDockerComposeEnvironment(null, null, null, null, null, null) {
        sh """
        echo "=========================================="
        echo "Pre-cleaning Docker Compose deployment"
        echo "=========================================="
        cd ${composed.dir}

    echo "[PRE_CLEANUP] docker ps -a before cleanup:"
    ${sudoCmd}docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}' || true
    echo "[PRE_CLEANUP] compose ps before cleanup:"
    ${composeCmd} ps || true

    # Kill running containers before attempting compose-based cleanup, preserving
    # the shared RTVI-VLM project that is reused across CI test stages.
    # This catches containers started outside of compose (e.g. direct `docker run` by another
    # team) that would otherwise hold GPU memory and cause decoder init failures in LVS.
    # compose down below will then handle network/volume cleanup cleanly.
    echo "[PRE_CLEANUP] Killing running containers on host except shared RTVI..."
    for cid in \$(${sudoCmd}docker ps -q); do
        project="\$(${sudoCmd}docker inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' "\$cid" 2>/dev/null || true)"
        name="\$(${sudoCmd}docker inspect -f '{{ .Name }}' "\$cid" 2>/dev/null | sed 's#^/##' || true)"
        if [ "\$project" = "${SHARED_RTVI_COMPOSE_PROJECT}" ]; then
            echo "[PRE_CLEANUP] Preserving shared RTVI container: \$name"
        else
            ${sudoCmd}docker kill "\$cid" || true
        fi
    done

    # Shut down every compose project currently known on this host (any directory, any variant).
    # This prevents "container name already in use" conflicts when different compose variants
    # share container names (e.g. gpt-oss-20b appears in both 2-GPU and 3-GPU variants).
    # Using 'docker compose -p <name> down' cleans containers, volumes, and networks cleanly.
    echo "[PRE_CLEANUP] Stopping all compose projects on host..."
    ${sudoCmd}docker compose ls --format json 2>/dev/null | \
        python3 -c "import sys,json; [print(p['Name']) for p in json.load(sys.stdin) if p.get('Name') and p.get('Name') != '${SHARED_RTVI_COMPOSE_PROJECT}']" | \
        xargs -r -I{} ${sudoCmd}docker compose -p {} down -v --remove-orphans || true

    # Force-remove any remaining stopped/exited containers not tracked by compose
    # (e.g. orphaned containers from a previous run where compose metadata was lost).
    echo "[PRE_CLEANUP] Removing any remaining non-running containers..."
    ${sudoCmd}docker ps -a -q --filter "status=exited" --filter "status=created" | \
        xargs -r ${sudoCmd}docker rm -f || true

    # Prune dangling (untagged) image layers and unused networks left over from previous runs.
    # This runs after all containers are stopped so no image is actively in use.
    # Intentionally NOT using -a (would also remove tagged images, including the freshly-built
    # LVS image) and NOT using --volumes (nim-cache and other persistent volumes must survive).
    echo "[PRE_CLEANUP] Pruning dangling images and unused networks to free disk space..."
    ${sudoCmd}docker image prune -f || true
    ${sudoCmd}docker network prune -f || true
    echo "[PRE_CLEANUP] Docker disk usage after prune:"
    ${sudoCmd}docker system df || true

    # media-server.yaml declares via-media-data as an external volume.
    # Ensure it exists on the host before docker compose up.
    echo "[PRE_CLEANUP] Ensuring external media volume exists: via-media-data"
    if ${sudoCmd}docker volume inspect via-media-data >/dev/null 2>&1; then
        echo "[PRE_CLEANUP] Docker volume 'via-media-data' already exists"
    else
        ${sudoCmd}docker volume create via-media-data >/dev/null
        echo "[PRE_CLEANUP] Created Docker volume 'via-media-data'"
    fi

    echo "[PRE_CLEANUP] compose ps after cleanup:"
    ${composeCmd} ps || true
    echo "[PRE_CLEANUP] docker ps -a after cleanup:"
    ${sudoCmd}docker ps -a --format 'table {{.Names}}\\t{{.Status}}\\t{{.Image}}' || true
    echo "[PRE_CLEANUP] nvidia-smi compute processes after cleanup:"
        nvidia-smi --query-compute-apps=pid,used_memory,name --format=csv || true
        """
    }
}

/**
 * Logs host disk and Docker storage usage before deployment.
 * Purely informational — warns loudly if any filesystem is >= 80% full but does not fail the build.
 * Helps diagnose slow startups caused by disk pressure (e.g. on shared Jenkins nodes).
 *
 * @param useSudo Whether to use sudo for docker commands
 */
def checkDiskUsage(boolean useSudo = true) {
    def sudoCmd = useSudo ? 'sudo ' : ''
    sh """
    echo "[DISK_CHECK] Host filesystem usage:"
    df -h

    echo "[DISK_CHECK] Docker disk usage:"
    ${sudoCmd}docker system df || true

    df -h | awk 'NR>1 {
        pct=\$5; sub(/%/, "", pct)
        if (pct+0 >= 80)
            print "[DISK_WARNING] " \$6 " is " \$5 " full (" \$3 " used / " \$2 " total)"
    }' || true
    """
}

/**
 * Prepares Docker Compose deployment by logging in to NGC and creating cache directory.
 * Optionally replaces the image tag in docker-compose.yml with a custom built image.
 *
 * @param ngcApiKey NGC API key for container registry access
 * @param builtImageTag Optional image tag to replace in docker-compose.yml (null to use default)
 * @param useSudo Whether to use sudo for docker commands (default: true for bare metal, false for DinD)
 * @param composeFilePath Path to docker-compose.yml for updating image tag
 */
def prepareDockerComposeDeployment(String ngcApiKey, String builtImageTag = null, boolean useSudo = true, String composeFilePath) {
    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("prepareDockerComposeDeployment: composeFilePath must be provided")
    }

    def sudoCmd = useSudo ? 'sudo ' : ''
    def nimCacheDir = getNimCacheDir()
    def targetComposeDir = composeFilePath.replaceAll(/\/[^\/]+$/, '')
    def targetComposeFile = composeFilePath.replaceAll(/^.*\//, '')
    def imageReplacementCmd = ""
    def manifestCheckCmd = ""

    if (builtImageTag != null) {
        echo "Will replace docker-compose.yml image with built image: ${builtImageTag}"
        imageReplacementCmd = """
            echo "Replacing LVS image with: ${builtImageTag}"
            sed -i 's|image: nvcr.io/.*/vss-video-summarization:.*|image: ${builtImageTag}|g' ${targetComposeFile}
            echo "Image replacement done. Verifying:"
            grep "image:" ${targetComposeFile} | grep -A2 "lvs:" || grep "image: nvcr.io" ${targetComposeFile} | head -3
        """
        manifestCheckCmd = """
            echo "[IMAGE_CHECK] Verifying image exists: ${builtImageTag}"
            if docker image inspect ${builtImageTag} > /dev/null 2>&1; then
                echo "[IMAGE_CHECK] Image found in local Docker daemon: ${builtImageTag}"
            elif ${sudoCmd}docker manifest inspect ${builtImageTag} > /dev/null 2>&1; then
                echo "[IMAGE_CHECK] Image verified in NGC registry: ${builtImageTag}"
            else
                echo "ERROR: Image ${builtImageTag} not found locally or in NGC registry."
                echo "Ensure Jenkinsfile.develop.multiarch ran for this commit first, or pass an explicit LVS_IMAGE_TAG."
                exit 1
            fi
        """
    } else {
        echo "Using hardcoded image from docker-compose.yml"
        imageReplacementCmd = """
            echo "Using hardcoded image from docker-compose.yml:"
            grep "image:" ${targetComposeFile} | grep -A2 "lvs:" || grep "image: nvcr.io" ${targetComposeFile} | head -3
        """
    }

    // NGC key is passed via withEnv (not Groovy-interpolated into the sh body)
    // so it doesn't appear in the Blue Ocean step header. The literal
    // ${NGC_API_KEY_FOR_LOGIN} is preserved into the bash script via the
    // Groovy `\$` escape; bash then expands it at runtime from the env.
    runNgcCliPreflight(ngcApiKey)
    withEnv(["NGC_API_KEY_FOR_LOGIN=${ngcApiKey}"]) {
        sh """
        echo "Running Docker Compose deployment test..."
        cd ${targetComposeDir}

        ${imageReplacementCmd}

        # Reset any cached nvcr.io auth before logging in with the current key
        ${sudoCmd}docker logout nvcr.io || true

        # Login to NGC (key read from env at runtime; never embedded in script literal)
        echo "\${NGC_API_KEY_FOR_LOGIN}" | ${sudoCmd}docker login nvcr.io -u '\$oauthtoken' --password-stdin

        ${manifestCheckCmd}

        # Create NIM cache directory with proper permissions
        echo "Using NIM cache directory: ${nimCacheDir}"
        mkdir -p '${nimCacheDir}'
        ${sudoCmd}chown -R 1000:1000 '${nimCacheDir}'
        ${sudoCmd}chmod -R a+rwx '${nimCacheDir}'
        """
    }
}

/**
 * Best-effort timeout detection for FlowInterruptedException that is safe in Jenkins sandbox.
 * We intentionally avoid using e.causes/getCauses because Script Security may reject it.
 */
def isLikelyTimeoutInterruption(org.jenkinsci.plugins.workflow.steps.FlowInterruptedException e) {
    def msg = "${e?.message ?: ''}".toLowerCase()
    return msg.contains('timeout') || msg.contains('time out') || msg.contains('exceeded')
}

/**
 * Resolve deployment timeout from DEPLOYMENT_TIMEOUT_MINUTES env var.
 * Falls back to a default timeout (20 minutes) if unset/invalid.
 */
def resolveDeploymentTimeoutMinutes() {
    int defaultTimeoutMinutes = 20
    def rawTimeout = env.DEPLOYMENT_TIMEOUT_MINUTES
    if (rawTimeout == null || rawTimeout.toString().trim().isEmpty()) {
        return defaultTimeoutMinutes
    }

    try {
        int parsedTimeout = rawTimeout.toString().trim().toInteger()
        if (parsedTimeout <= 0) {
            echo "Invalid DEPLOYMENT_TIMEOUT_MINUTES=${rawTimeout}; using default ${defaultTimeoutMinutes}"
            return defaultTimeoutMinutes
        }
        return parsedTimeout
    } catch (Exception ignored) {
        echo "Invalid DEPLOYMENT_TIMEOUT_MINUTES=${rawTimeout}; using default ${defaultTimeoutMinutes}"
        return defaultTimeoutMinutes
    }
}

/**
 * Resolve Docker pull timeout from DOCKER_PULL_TIMEOUT_MINUTES env var.
 * Falls back to a default timeout (25 minutes) if unset/invalid.
 */
def resolveDockerPullTimeoutMinutes() {
    int defaultTimeoutMinutes = 25
    def rawTimeout = env.DOCKER_PULL_TIMEOUT_MINUTES
    if (rawTimeout == null || rawTimeout.toString().trim().isEmpty()) {
        return defaultTimeoutMinutes
    }

    try {
        int parsedTimeout = rawTimeout.toString().trim().toInteger()
        if (parsedTimeout <= 0) {
            echo "Invalid DOCKER_PULL_TIMEOUT_MINUTES=${rawTimeout}; using default ${defaultTimeoutMinutes}"
            return defaultTimeoutMinutes
        }
        return parsedTimeout
    } catch (Exception ignored) {
        echo "Invalid DOCKER_PULL_TIMEOUT_MINUTES=${rawTimeout}; using default ${defaultTimeoutMinutes}"
        return defaultTimeoutMinutes
    }
}

/**
 * Runs the complete Docker Compose workflow and executes a provided test runner.
 * Runs the complete Docker Compose test workflow on a bare metal node or in DinD.
 * This includes: deployment preparation, service startup, integration tests, and cleanup.
 *
 * @param config Map containing:
 *   - ngcApiKey: NGC API key
 *   - nvidiaApiKey: NVIDIA API key
 *   - openaiApiKey: OpenAI API key
 *   - builtImageTag: (optional) Custom image tag to use
 *   - useSudo: (optional, default: true) Whether to use sudo for docker commands
 *   - composeFilePath: Path to docker-compose.yml
 *   - hfToken: (optional) Hugging Face token for model access
 *
 * Timeout behavior:
 *   - Image pull phase: controlled by DOCKER_PULL_TIMEOUT_MINUTES (default: 25)
 *   - Deployment phase (service startup + health checks): controlled by DEPLOYMENT_TIMEOUT_MINUTES (default: 20)
 *
 * @param testRunner Closure to execute tests after deployment
 *   - runFunctionalTests/runIntegrationTests: (optional) Which test types to run; legacy runAllIntegrationTests key also accepted (default: true)
 *   - debugTests: (optional, default: false) When true, capture LVS logs before/during/after integration tests and archive
 */
def runBareMetalDockerComposeWorkflow(Map config, Closure testRunner) {
    def ngcApiKey = config.ngcApiKey
    def nvidiaApiKey = config.nvidiaApiKey
    def openaiApiKey = config.openaiApiKey
    def hfToken = config.hfToken
    def artifactoryUser = config.artifactoryUser
    def artifactoryToken = config.artifactoryToken
    def builtImageTag = config.builtImageTag
    def envCredentials = [
        openaiApiKey: openaiApiKey,
        ngcApiKey: ngcApiKey,
        nvidiaApiKey: nvidiaApiKey,
        hfToken: config.hfToken,
        artifactoryUser: artifactoryUser,
        artifactoryToken: artifactoryToken
    ]
    // dockerPythonPath: config.dockerPythonPath
    def useSudo = config.useSudo != null ? config.useSudo : true
    def composeFilePath = config.composeFilePath
    def composeProfiles = config.composeProfiles ?: ''
    def useSharedRtvi = config.useSharedRtvi == true
    def deploymentTimeoutMinutes = resolveDeploymentTimeoutMinutes()
    def pullTimeoutMinutes = resolveDockerPullTimeoutMinutes()

    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("runBareMetalDockerComposeWorkflow: composeFilePath must be provided")
    }

    try {
        // Pre-clean any stale services from earlier runs on the same node/workspace
        preCleanupDockerCompose(useSudo, composeFilePath, composeProfiles)

        // Surface disk pressure early — a full disk is a common cause of slow LVS startup on shared nodes
        checkDiskUsage(useSudo)

        // Prepare deployment
        prepareDockerComposeDeployment(ngcApiKey, builtImageTag, useSudo, composeFilePath)

        if (useSharedRtvi) {
            def sharedRtviUrl = startSharedRtviVlm(useSudo, envCredentials, composeFilePath)
            echo "[SHARED_RTVI] Compose test stack will use RTVI_VLM_URL=${sharedRtviUrl}"
        }

        // Pull images with a separate timeout so slow pulls do not consume the deployment health-check budget.
        try {
            echo "[PULL] Starting Docker Compose image pull phase (timeout: ${pullTimeoutMinutes} minutes)"
            timeout(time: pullTimeoutMinutes, unit: 'MINUTES') {
                pullDockerComposeImages(
                    ngcApiKey,
                    nvidiaApiKey,
                    openaiApiKey,
                    useSudo,
                    composeFilePath,
                    hfToken,
                    builtImageTag,
                    artifactoryUser,
                    artifactoryToken,
                    composeProfiles
                )
            }
            echo "[PULL] Docker Compose image pull phase completed successfully"
        } catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException e) {
            def timedOut = isLikelyTimeoutInterruption(e)
            if (timedOut) {
                echo "[PULL_TIMEOUT] Docker Compose image pull timed out after ${pullTimeoutMinutes} minutes"
                echo "[PULL_TIMEOUT] Large images (like lvs) may require more time. Consider pre-pulling or using cached images."
            } else {
                echo "[PULL_INTERRUPTED] Docker Compose image pull interrupted: ${e.message}"
            }
            throw e
        } catch (Exception e) {
            echo "[PULL_ERROR] Docker Compose image pull failed: ${e.message}"
            throw e
        }

        // Start services and wait for health checks with separate timeout
        try {
            echo "[DEPLOYMENT] Starting Docker Compose deployment phase (timeout: ${deploymentTimeoutMinutes} minutes)"
            timeout(time: deploymentTimeoutMinutes, unit: 'MINUTES') {
                runDockerComposeDeployment(
                    ngcApiKey,
                    nvidiaApiKey,
                    openaiApiKey,
                    useSudo,
                    composeFilePath,
                    hfToken,
                    artifactoryUser,
                    artifactoryToken,
                    composeProfiles
                )
            }
            echo "[DEPLOYMENT] Docker Compose deployment phase completed successfully"
        } catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException e) {
            def timedOut = isLikelyTimeoutInterruption(e)
            if (timedOut) {
                echo "[DEPLOYMENT_TIMEOUT] Docker Compose deployment timed out after ${deploymentTimeoutMinutes} minutes"
                echo "[DEPLOYMENT_TIMEOUT] Collecting compose status/logs before rethrowing"
            } else {
                echo "[DEPLOYMENT_INTERRUPTED] Docker Compose deployment interrupted: ${e.message}"
                echo "[DEPLOYMENT_INTERRUPTED] Collecting compose status/logs before rethrowing"
            }
            handleDockerComposeFailure(useSudo, composeFilePath, composeProfiles)
            throw e
        } catch (Exception e) {
            echo "[DEPLOYMENT_ERROR] Docker Compose deployment failed: ${e.message}"
            echo "[DEPLOYMENT_ERROR] Collecting compose status/logs before rethrowing"
            handleDockerComposeFailure(useSudo, composeFilePath, composeProfiles)
            throw e
        }

        // Run closure for tests
        try {
            echo "[TEST_PHASE] Starting post-deployment test phase"
            testRunner()
            echo "[TEST_PHASE] Post-deployment test phase completed successfully"
        } catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException e) {
            def timedOut = isLikelyTimeoutInterruption(e)
            if (timedOut) {
                echo "[TEST_TIMEOUT] Post-deployment test phase timed out"
            } else {
                echo "[TEST_INTERRUPTED] Post-deployment test phase interrupted: ${e.message}"
            }
            throw e
        } catch (Exception e) {
            echo "[TEST_ERROR] Post-deployment test phase failed: ${e.message}"
            throw e
        }
    } finally {
        echo "[CLEANUP] Starting Docker Compose cleanup phase"
        cleanupDockerCompose(useSudo, composeFilePath, composeProfiles)
        if (useSharedRtvi) {
            clearSharedRtviAssets(useSudo)
        }
        echo "[CLEANUP] Docker Compose cleanup phase finished"
    }
}

/**
 * Runs the complete Docker Compose test workflow on a bare metal node or in DinD.
 * This includes: deployment preparation, service startup, API tests, and cleanup.
 *
 * @param config Map containing:
 *   - ngcApiKey: NGC API key
 *   - nvidiaApiKey: NVIDIA API key
 *   - openaiApiKey: OpenAI API key
 *   - builtImageTag: (optional) Custom image tag to use
 *   - useSudo: (optional, default: true) Whether to use sudo for docker commands
 *   - runFunctionalTests/runIntegrationTests: (optional) Which test types to run; legacy runAllIntegrationTests key also accepted (default: true)
 *   - composeFilePath: Path to docker-compose.yml
 *   - debugTests: (optional, default: false) When true, capture LVS logs before/during/after API tests and archive
 *
 * Timeout behavior:
 *   - Image pull phase: controlled by DOCKER_PULL_TIMEOUT_MINUTES (default: 25)
 *   - Deployment phase: controlled by DEPLOYMENT_TIMEOUT_MINUTES (default: 20)
 */
def runBareMetalDockerComposeTest(Map config) {
    // runFunctionalTests/runIntegrationTests take precedence over the legacy runAllIntegrationTests flag.
    // Backward compat: if neither specific key is present, fall back to runAllIntegrationTests (default true).
    def legacyRunAll = config.runAllIntegrationTests != false
    def runFunctional = config.containsKey('runFunctionalTests') ? config.runFunctionalTests == true : legacyRunAll
    def runIntegration = config.containsKey('runIntegrationTests') ? config.runIntegrationTests == true : legacyRunAll
    def debugTests = config.debugTests == true

    // Backward-compatible default used by integration-tests stage in Jenkinsfile.
    def workflowConfig = [:] + config
    if (workflowConfig.composeFilePath == null || workflowConfig.composeFilePath.toString().trim().isEmpty()) {
        workflowConfig.composeFilePath = "${serviceWorkspacePath()}/compose/BlueprintBuilderGenerated/docker-compose.yml"
    }
    if (workflowConfig.useSharedRtvi == null) {
        workflowConfig.useSharedRtvi = true
    }
    if (workflowConfig.composeProfiles == null || workflowConfig.composeProfiles.toString().trim().isEmpty()) {
        workflowConfig.composeProfiles = workflowConfig.useSharedRtvi == true ? '' : 'rtvi'
    }

    // Build envCredentials in this scope so the closure below can capture it.
    // (runBareMetalDockerComposeWorkflow also builds one internally, but closures
    // only see variables from their *defining* scope, not the callee's locals.)
    def envCredentials = [
        openaiApiKey: config.openaiApiKey,
        ngcApiKey: config.ngcApiKey,
        nvidiaApiKey: config.nvidiaApiKey,
        hfToken: config.hfToken,
        artifactoryUser: config.artifactoryUser,
        artifactoryToken: config.artifactoryToken,
        composeFilePath: workflowConfig.composeFilePath,
    ]

    def builtImageTag = config.builtImageTag

    runBareMetalDockerComposeWorkflow(workflowConfig) {
        // Read the docker-compose.yml file and get the LVS service image (not the first image; wait-for-* use curl).
        def dockerComposeFile = readFile(workflowConfig.composeFilePath)
        def imageName = null
        def foundLvs = false
        for (String line in dockerComposeFile.split('\n')) {
            def trimmed = line.trim()
            if (trimmed == 'lvs:' || trimmed.startsWith('lvs:')) {
                foundLvs = true
                continue
            }
            if (foundLvs && trimmed.startsWith('image:') && !trimmed.startsWith('#')) {
                imageName = trimmed.split(':', 2).last().trim()
                break
            }
        }
        echo "Image name: ${imageName}"
        if (!imageName) {
            error "No image name found in docker-compose.yml file"
        } else {
            builtImageTag = imageName
        }

        // Run tests (if any requested)
        if (runFunctional || runIntegration) {
            echo "Running tests (functional=${runFunctional}, integration=${runIntegration})..."
            runServiceTestSuite(workflowConfig.useSudo != null ? workflowConfig.useSudo : true, debugTests, envCredentials, builtImageTag, runFunctional, runIntegration)
        } else {
            echo "Skipping tests (runFunctionalTests=false, runIntegrationTests=false)"
        }
    }
}

/**
 * Runs the VSS performance benchmark for a specific scenario using a local venv.
 *
 * @param scenarioName Benchmark scenario to run (default: quick_test)
 * @param configPath Optional path to config YAML (defaults to config.yaml in perf/benchmark)
 * @param vlmGpus Optional comma-separated VLM GPU list (e.g., "0,1")
 * @param llmGpus Optional comma-separated LLM GPU list (e.g., "2,3")
 * @param configId Optional config identifier for Metropolis JSON (e.g., "tp1-nemo3nano-9k")
 * @param uploadToMinIO Optional service name to upload results to MinIO (e.g., "LVS", "RTVI-VLM")
 * @param composeFilePath Optional docker compose file path for benchmark log context
 * @param vlmModel Optional VLM model metadata string for perf JSON
 * @param llmModel Optional LLM model metadata string for perf JSON
 * @param visionInputTokens Optional vision token budget metadata (e.g., "9k")
 * @param gpuModel Optional GPU model metadata override for perf JSON
 *
 * Example:
 *   runPerfBenchmark('quick_test', null, '0,1', '2,3', 'tp1-nemo3nano-9k', 'LVS', 'compose/foo/docker-compose.yml', 'cr2-8b', 'nemotron-nano-9b-v2', '9k', 'H100')
 */
def runPerfBenchmark(
    String scenarioName = 'quick_test',
    String configPath = null,
    String vlmGpus = null,
    String llmGpus = null,
    String configId = null,
    String uploadToMinIO = null,
    String composeFilePath = null,
    String vlmModel = null,
    String llmModel = null,
    String visionInputTokens = null,
    String gpuModel = null,
    String release = null
) {
    def shellQuote = { value ->
        return "'${value.toString().replace("'", "'\"'\"'")}'"
    }
    def scenarioArg = scenarioName.split(',').collect { it.trim() }.findAll { it }.join(' ')
    def configArg = configPath ? "--config ${configPath}" : ""
    def vlmArg = vlmGpus ? "--vlm-gpus ${vlmGpus}" : ""
    def llmArg = llmGpus ? "--llm-gpus ${llmGpus}" : ""
    def vlmModelArg = vlmModel ? "--vlm-model ${shellQuote(vlmModel)}" : ""
    def llmModelArg = llmModel ? "--llm-model ${shellQuote(llmModel)}" : ""
    def visionInputTokensArg = visionInputTokens ? "--vision-input-tokens ${shellQuote(visionInputTokens)}" : ""
    def gpuModelArg = gpuModel ? "--gpu-model ${shellQuote(gpuModel)}" : ""
    def releaseArg = release ? "--release ${shellQuote(release)}" : ""
    def outputJsonArg = "--output-json vss-perf-results"
    def configIdArg = configId ? "--config-id ${configId}" : ""
    def triggeredByArg = "--triggered-by ci_pipeline"
    def pipelineUrlArg = env.BUILD_URL ? "--pipeline-url ${env.BUILD_URL}" : ""
    def minioArg = uploadToMinIO ? "--upload" : ""
    def benchmarkConfigId = configId?.trim() ? configId.trim() : "auto(output_dir_basename)"
    def benchmarkComposeFile = composeFilePath?.trim() ? composeFilePath.trim() : "unknown"
    def benchmarkStatus = "FAILURE"
    def benchmarkStartMillis = System.currentTimeMillis()

    echo "[BENCHMARK_START] scenario=${scenarioArg} config_id=${benchmarkConfigId} compose_file=${benchmarkComposeFile}"
    try {
        withCredentials([
            string(credentialsId: 'metropolis-minio-access-key', variable: 'MINIO_ACCESS_KEY'),
            string(credentialsId: 'metropolis-minio-secret-key', variable: 'MINIO_SECRET_KEY')
        ]) {
            sh """
            echo "Running performance benchmark scenario: ${scenarioArg}"
            cd ${serviceWorkspacePath()}/perf/benchmark
            # Ensure venv support (ensurepip) is available on bare-metal nodes.
            # `python3 -m venv --help` can succeed even when ensurepip is missing.
            PYTHON_VER=\$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            if ! python3 -m ensurepip --version >/dev/null 2>&1; then
                echo "[PERF] python\${PYTHON_VER}-venv missing; installing..."
                sudo apt-get update -qq
                sudo apt-get install -y python\${PYTHON_VER}-venv python3-venv
            fi
            rm -rf /tmp/vss-perf-venv
            python3 -m venv /tmp/vss-perf-venv
            . /tmp/vss-perf-venv/bin/activate
            python -m pip install --upgrade pip
            python -m pip install -r requirements.txt
            export VIA_BACKEND=http://localhost:38111
            python vss_perf_benchmark.py ${configArg} --scenario ${scenarioArg} ${vlmArg} ${llmArg} ${vlmModelArg} ${llmModelArg} ${visionInputTokensArg} ${gpuModelArg} ${releaseArg} ${outputJsonArg} ${configIdArg} ${triggeredByArg} ${pipelineUrlArg} ${minioArg}
            """
        }
        benchmarkStatus = "SUCCESS"
    } finally {
        def durationSeconds = ((System.currentTimeMillis() - benchmarkStartMillis) / 1000).toInteger()
        echo "[BENCHMARK_COMPLETE] status=${benchmarkStatus} scenario=${scenarioArg} config_id=${benchmarkConfigId} compose_file=${benchmarkComposeFile} duration_seconds=${durationSeconds}"
    }
}

/**
 * Runs perf via compose/run_benchmark.sh: preps env (cleanup, disk check, image tag, NGC login),
 * then invokes the script to launch compose, wait for LVS, run vss_perf_benchmark.py, and teardown.
 * Caller is responsible for copying perf/benchmark/vss-perf-report to results/${configId}/ and archiving.
 *
 * @param config Same map as runBareMetalDockerComposePerfTest (ngcApiKey, builtImageTag, composeFilePath,
 *   scenarioName, vlmGpus, llmGpus, vlmModel, llmModel, visionInputTokens, gpuModel, configId, uploadToMinIO,
 *   ociDeferMinioUpload, release, etc.)
 */
def runBareMetalPerfViaRunBenchmarkScript(Map config) {
    def useSudo = config.useSudo != null ? config.useSudo : true
    def composeFilePath = config.composeFilePath
    def scenarioName = config.scenarioName ?: 'quick_test'
    def timeoutMinutes = resolveDeploymentTimeoutMinutes()
    def pullTimeoutMinutes = resolveDockerPullTimeoutMinutes()
    def timeoutSec = timeoutMinutes * 60
    def nimCacheDir = getNimCacheDir()

    if (composeFilePath == null || composeFilePath.trim().isEmpty()) {
        error("runBareMetalPerfViaRunBenchmarkScript: composeFilePath must be provided")
    }

    preCleanupDockerCompose(useSudo, composeFilePath)
    checkDiskUsage(useSudo)
    prepareDockerComposeDeployment(config.ngcApiKey, config.builtImageTag, useSudo, composeFilePath)

    // Explicit pull in same session as login so run_benchmark.sh "compose up -d" sees cached images (avoids auth/pull in script).
    try {
        echo "[PULL] Starting Docker Compose image pull phase (timeout: ${pullTimeoutMinutes} minutes)"
        timeout(time: pullTimeoutMinutes, unit: 'MINUTES') {
            pullDockerComposeImages(
                config.ngcApiKey,
                config.nvidiaApiKey,
                config.openaiApiKey,
                useSudo,
                composeFilePath,
                config.hfToken,
                null,  // pass builtImageTag=null to pull all images including lvs here
                config.artifactoryUser,
                config.artifactoryToken
            )
        }
        echo "[PULL] Docker Compose image pull phase completed successfully"
    } catch (org.jenkinsci.plugins.workflow.steps.FlowInterruptedException e) {
        def timedOut = isLikelyTimeoutInterruption(e)
        if (timedOut) {
            echo "[PULL_TIMEOUT] Docker Compose image pull timed out after ${pullTimeoutMinutes} minutes"
        } else {
            echo "[PULL_INTERRUPTED] Docker Compose image pull interrupted: ${e.message}"
        }
        throw e
    } catch (Exception e) {
        echo "[PULL_ERROR] Docker Compose image pull failed: ${e.message}"
        throw e
    }

    def configId = config.configId ?: ''
    def uploadToMinIO = config.uploadToMinIO
    def ociDeferMinioUpload = (config.ociDeferMinioUpload == true)
    def enableMinioUploadOnBm = uploadToMinIO && !ociDeferMinioUpload
    if (ociDeferMinioUpload && uploadToMinIO) {
        echo '[PERF] OCI defer: skipping MinIO upload on bare metal; orchestrator will upload after archive'
    }
    def vlmGpus = config.vlmGpus ?: ''
    def llmGpus = config.llmGpus ?: ''
    def vlmModel = config.vlmModel ?: ''
    def llmModel = config.llmModel ?: ''
    def visionInputTokens = config.visionInputTokens ?: ''
    def gpuModel = config.gpuModel ?: ''
    def release = config.release ?: ''

    def shellQuote = { value -> return "'${value.toString().replace("'", "'\"'\"'")}'" }
    def vArg = vlmGpus ? "-v ${vlmGpus}" : ''
    def lArg = llmGpus ? "-l ${llmGpus}" : ''
    def rArg = release ? "-r ${shellQuote(release)}" : ''
    // PERF_SCENARIO carries comma-separated checkbox values (e.g. "a,b"). vss_perf_benchmark.py's
    // --scenario uses nargs="+", so split commas → space-separated args. Sentinel "all" (or empty)
    // omits -s entirely, letting run_benchmark.sh fall through to "all scenarios in config".
    def scenarioList = scenarioName ? scenarioName.split(',').collect { it.trim() }.findAll { it } : []
    def runAll = scenarioList.isEmpty() || scenarioList.any { it.equalsIgnoreCase('all') }
    def sArg = runAll ? '' : "-s ${scenarioList.join(' ')}"

    withCredentials([
        string(credentialsId: 'metropolis-minio-access-key', variable: 'MINIO_ACCESS_KEY'),
        string(credentialsId: 'metropolis-minio-secret-key', variable: 'MINIO_SECRET_KEY')
    ]) {
        sh """
            export DOCKER_USE_SUDO="${useSudo ? 'true' : ''}"
            export NGC_API_KEY="\${NGC_API_KEY_PERF}"
            export NVIDIA_API_KEY="\${NVIDIA_API_KEY_PERF}"
            export HF_TOKEN="\${HF_TOKEN_PERF}"
            export LOCAL_NIM_CACHE="${nimCacheDir}"
            export CONFIG_ID="${configId}"
            export TRIGGERED_BY="ci_pipeline"
            export PIPELINE_URL="${env.BUILD_URL ?: ''}"
            export UPLOAD_TO_MINIO="${enableMinioUploadOnBm ? 'true' : ''}"
            export VLM_MODEL="${vlmModel}"
            export LLM_MODEL="${llmModel}"
            export VISION_INPUT_TOKENS="${visionInputTokens}"
            export GPU_MODEL="${gpuModel}"
            export COMPOSE_PROFILES="\${COMPOSE_PROFILES:-media}"

            # ensurepip/apt are cwd-independent (runPerfBenchmark cd's to perf/benchmark only because pip uses requirements.txt there next).
            # Ensure venv support (ensurepip) is available on bare-metal nodes.
            # `python3 -m venv --help` can succeed even when ensurepip is missing.
            PYTHON_VER=\$(python3 -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
            if ! python3 -m ensurepip --version >/dev/null 2>&1; then
                echo "[PERF] python\${PYTHON_VER}-venv missing; installing..."
                sudo apt-get update -qq
                sudo apt-get install -y python\${PYTHON_VER}-venv python3-venv
            fi

            echo "[PERF] Running compose/run_benchmark.sh (deploy + benchmark + teardown)"
            cd ${serviceWorkspacePath()}
            # Let run_benchmark.sh apply device-specific MODEL_PATH overlays for
            # supported GPUs such as H100, H200, B200, GB200, RTX Pro, Spark, and
            # Thor. perf-configs.yaml still selects the node label, compose file,
            # and GPU placement; -G auto only changes the VLM checkpoint overlay
            # when the reserved node's GPU name matches a known overlay. The old
            # RTX Pro 6000 crash guard was for an HF NVFP4 overlay; the checked-in
            # RTX Pro overlay now uses an NGC MODEL_PATH, so CI can exercise it.
            ./compose/run_benchmark.sh -f "${composeFilePath}" -G auto ${sArg} -p 38111 -t ${timeoutSec} ${vArg} ${lArg} ${rArg} -O vss-perf-results -d
        """
    }
}

/**
 * Runs the complete Docker Compose deployment workflow and executes perf benchmarks.
 * Path is chosen by params.PERF_USE_RUN_BENCHMARK_SCRIPT: 'true' → compose/run_benchmark.sh; else → Groovy runBareMetalDockerComposeWorkflow + runPerfBenchmark.
 *
 * @param config Map containing:
 *   - ngcApiKey: NGC API key
 *   - nvidiaApiKey: NVIDIA API key
 *   - openaiApiKey: OpenAI API key
 *   - builtImageTag: (optional) Custom image tag to use
 *   - useSudo: (optional, default: true) Whether to use sudo for docker commands
 *   - composeFilePath: Path to docker-compose.yml (full path or relative to workspace)
 *   - scenarioName: (optional, default: quick_test) Perf benchmark scenario
 *   - vlmGpus: (optional) Comma-separated VLM GPU list
 *   - llmGpus: (optional) Comma-separated LLM GPU list
 *   - vlmModel: (optional) VLM model metadata for perf JSON
 *   - llmModel: (optional) LLM model metadata for perf JSON
 *   - visionInputTokens: (optional) Vision token budget metadata (e.g., "9k")
 *   - gpuModel: (optional) GPU model metadata override for perf JSON
 *   - configId: (optional) Config identifier for Metropolis JSON
 *   - uploadToMinIO: (optional) Service name to upload to MinIO (e.g., "LVS")
 *   - ociDeferMinioUpload: (optional) When true with uploadToMinIO set, do not pass --upload on the BM
 *     (caller uploads from the orchestrator after stash/unstash — for OCI BMs without MinIO reachability).
 *
 * Timeout behavior:
 *   compose/run_benchmark.sh: timeout controlled by DEPLOYMENT_TIMEOUT_MINUTES (default: 20)
 *   Groovy path
 *   - Image pull phase: controlled by DOCKER_PULL_TIMEOUT_MINUTES (default: 25)
 *   - Deployment phase: controlled by DEPLOYMENT_TIMEOUT_MINUTES (default: 20)
 *
 * Example with MinIO upload:
 *   runBareMetalDockerComposePerfTest([
 *       ngcApiKey: ngcKey,
 *       composeFilePath: 'compose/...',
 *       scenarioName: 'quick_test',
 *       configId: 'tp1-nemo3nano-9k',
 *       uploadToMinIO: 'LVS'
 *   ])
 */
def runBareMetalDockerComposePerfTest(Map config) {
    // Same interface: extract params for readability and for Groovy path (runPerfBenchmark).
    def scenarioName = config.scenarioName ?: 'quick_test'
    def vlmGpus = config.vlmGpus
    def llmGpus = config.llmGpus
    def vlmModel = config.vlmModel
    def llmModel = config.llmModel
    def visionInputTokens = config.visionInputTokens
    def gpuModel = config.gpuModel
    def configId = config.configId
    def uploadToMinIO = config.uploadToMinIO  // Optional: service name to upload to MinIO (e.g., 'LVS')
    def composeFilePath = config.composeFilePath
    def release = config.release
    def useRunBenchmarkScript = (params.PERF_USE_RUN_BENCHMARK_SCRIPT ?: 'true').toString().toLowerCase() == 'true'

    // Perf needs media-server (profile-gated in media-server.yaml).
    withEnv(['COMPOSE_PROFILES=media']) {
        if (useRunBenchmarkScript) {
            runBareMetalPerfViaRunBenchmarkScript(config)
        } else {
            runBareMetalDockerComposeWorkflow(config) {
                runPerfBenchmark(
                    scenarioName,
                    null,
                    vlmGpus,
                    llmGpus,
                    configId,
                    uploadToMinIO,
                    composeFilePath,
                    vlmModel,
                    llmModel,
                    visionInputTokens,
                    gpuModel,
                    release
                )
            }
        }
    }
}

/**
 * Prepares the environment for running an example stage.
 * Extracts the tar file and installs prerequisites.
 *
 * @param stageName The name of the stage/example
 * @param tarFile The tar file to extract
 */
def prepareExampleStage(String stageName, String tarFile) {
    sh("""\
    #!/usr/bin/env bash
    ls -lrt
    mkdir ${stageName}
    tar -xvf ${tarFile} -C ${stageName}
    chmod u+x ${stageName}/dist/envbuild.sh
    apt-get update -y
    apt-get install --no-install-recommends -y wget curl
    helper-scripts/install-pre-requisites.sh -y
    #apt install openssh-client -y
    """.stripIndent())
}

/**
 * Runs the infrastructure install command for an example.
 *
 * @param stageName The name of the stage/example
 * @param configFile The config file to use
 */
def runInfraInstall(String stageName, String configFile) {
    sh("""\
    #!/usr/bin/env bash
    ${stageName}/dist/envbuild.sh install --component infra --config-file ci/configs/${configFile} 2>&1 | tee ${stageName}/output.txt
    ls -lrt
    pwd
    """.stripIndent())
}

/**
 * Runs the infrastructure uninstall command for an example.
 *
 * @param stageName The name of the stage/example
 * @param configFile The config file to use
 */
def runInfraUninstall(String stageName, String configFile) {
    sh """
    chmod u+x ${stageName}/dist/envbuild.sh
    ${stageName}/dist/envbuild.sh uninstall --component infra --config-file ci/configs/${configFile}
    """
}

/**
 * Repairs broken apt state on a bare metal node before infra install.
 *
 * Other teams may leave nodes with a broken apt state (e.g. half-removed packages
 * with unmet dependencies). This runs `dpkg --configure -a` and
 * `apt-get --fix-broken install` over SSH to restore a clean apt state before
 * nv-one-click attempts to remove/install NVIDIA packages.
 *
 * Uses branch-local host/user values from getNodeIp() instead of shared env vars.
 * This avoids cross-branch races in parallel perf runs.
 *
 * @param host Bare metal node IP/host
 * @param user SSH user on the bare metal node
 * @param maxRetries Number of repair attempts before failing the branch
 */
def repairAptOnBareMetal(String host, String user, int maxRetries = 4) {
    if (!host?.trim()) {
        error("repairAptOnBareMetal: host is required")
    }
    if (!user?.trim()) {
        error("repairAptOnBareMetal: user is required")
    }
    if (!env.NVOC_SSH_PRIVATE_KEY_FILE?.trim()) {
        error("repairAptOnBareMetal: NVOC_SSH_PRIVATE_KEY_FILE is not set")
    }
    sh("""\
    #!/usr/bin/env bash
    set -euo pipefail

    attempt=1
    max_retries=${maxRetries}
    host='${host}'
    user='${user}'

    echo "=== Repairing apt state on bare metal node \${host} (\${user}) ==="

    # Detect conflicting Docker apt Signed-By entries (docker.gpg vs docker.asc for the same
    # repo). apt-get cannot read its sources at all in this state, so --fix-broken never runs.
    # Remove source files referencing the old docker.gpg keyring, keeping the .asc one.
    # Use || true so pipefail doesn't fire when apt-get update itself exits non-zero.
    apt_update_out=\$(ssh -i '${env.NVOC_SSH_PRIVATE_KEY_FILE}' \
        -o StrictHostKeyChecking=no \
        -o ConnectTimeout=30 \
        "\${user}@\${host}" 'sudo apt-get update 2>&1' 2>&1 || true)
    if echo "\${apt_update_out}" | grep -q 'Conflicting values set for option Signed-By'; then
        echo "[APT_REPAIR] Detected conflicting Docker apt Signed-By on \${host} — removing stale .gpg source entry"
        ssh -i '${env.NVOC_SSH_PRIVATE_KEY_FILE}' \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=30 \
            "\${user}@\${host}" \
            'sudo bash -c '"'"'grep -rl "download.docker.com" /etc/apt/sources.list.d/ | xargs grep -l "docker\\.gpg" | xargs rm -f'"'"''
    fi

    while [ "\${attempt}" -le "\${max_retries}" ]; do
        echo "[APT_REPAIR] attempt \${attempt}/\${max_retries} on \${user}@\${host}"
        if ssh -i '${env.NVOC_SSH_PRIVATE_KEY_FILE}' \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=30 \
            "\${user}@\${host}" \
            "sudo flock -w 900 /tmp/lvs-apt-repair.lock bash -lc 'DEBIAN_FRONTEND=noninteractive dpkg --configure -a && DEBIAN_FRONTEND=noninteractive apt-get -y --fix-broken install'" ; then
            echo "=== apt state repair complete on \${host} ==="
            exit 0
        fi

        if [ "\${attempt}" -lt "\${max_retries}" ]; then
            sleep \$((attempt * 15))
        fi
        attempt=\$((attempt + 1))
    done

    echo "ERROR: apt repair failed on \${host} after \${max_retries} attempts"
    exit 1
    """.stripIndent())
}

/**
 * True when the reserved Jenkins agent name denotes an OCI (Oracle Cloud) bare metal
 * node. Those agents typically cannot resolve or reach internal NVIDIA GitLab; perf
 * then runs {@link #gitCheckoutShallow()} on the orchestrator and copies that sparse tree (tar over ssh) instead
 * of running {@code gitCheckoutShallow()} on the agent.
 *
 * Heuristic matches {@code getNodeIp} OCI-style detection: {@code -OCI} in the name
 * or name ending with {@code OCI}.
 */
def isOciStyleBareMetalNode(String reservedNodeName) {
    if (!reservedNodeName?.trim()) {
        return false
    }
    return reservedNodeName.contains('-OCI') || reservedNodeName.toUpperCase().endsWith('OCI')
}

/**
 * Copies a local directory from the current executor (K8s pod) to {@code remoteDir} on the bare metal
 * host via {@code tar | ssh tar} (remote dir is wiped first). OpenSSH 9+ {@code scp} rejects {@code dir/.}
 * as a source ("unexpected filename: ."); a tar stream avoids that and still includes dotfiles (e.g. .git).
 * Defaults to {@code env.WORKSPACE};
 * pass {@code localSourceDir} to sync a subdirectory (e.g. orchestrator sparse checkout tree).
 *
 * Must run outside {@code node(bm)} — same context as {@link #repairAptOnBareMetal}.
 * The bare metal stage should use {@code ws(remoteDir)} so {@code env.WORKSPACE} on the
 * agent points at the synced tree (including {@code .git} for {@link #computeImageVersion}).
 *
 * @param host BM host/IP from {@code getNodeIp}
 * @param user SSH user on the BM
 * @param remoteDir Absolute path on the BM (e.g. {@code /home/ubuntu/lvs-perf-ws-<BUILD_TAG>-H100})
 * @param localSourceDir Optional absolute path to copy from; defaults to {@code env.WORKSPACE}
 */
def syncWorkspaceToBareMetal(String host, String user, String remoteDir, String localSourceDir = null) {
    if (!host?.trim()) {
        error("syncWorkspaceToBareMetal: host is required")
    }
    if (!user?.trim()) {
        error("syncWorkspaceToBareMetal: user is required")
    }
    if (!remoteDir?.trim()) {
        error("syncWorkspaceToBareMetal: remoteDir is required")
    }
    if (!env.NVOC_SSH_PRIVATE_KEY_FILE?.trim()) {
        error("syncWorkspaceToBareMetal: NVOC_SSH_PRIVATE_KEY_FILE is not set")
    }
    def localWs = (localSourceDir ?: lvsWorkspace())?.trim()
    if (!localWs) {
        error("syncWorkspaceToBareMetal: local source path is empty — run after checkout-source (or pass localSourceDir)")
    }
    echo "syncWorkspaceToBareMetal: ${localWs}/ -> ${user}@${host}:${remoteDir}/"
    withEnv([
        "SYNC_LOCAL_WS=${localWs}",
        "SYNC_REMOTE_DIR=${remoteDir}",
        "SYNC_SSH_KEY=${env.NVOC_SSH_PRIVATE_KEY_FILE}",
        "SYNC_HOST=${host}",
        "SYNC_USER=${user}"
    ]) {
        sh '''#!/usr/bin/env bash
set -euo pipefail
command -v ssh >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y --no-install-recommends openssh-client; }
SSH_BASE=( -i "${SYNC_SSH_KEY}" -o StrictHostKeyChecking=no -o ConnectTimeout=120 -o BatchMode=yes )
attempt=1
max_attempts=2
while [ "${attempt}" -le "${max_attempts}" ]; do
  echo "[SYNC_SSH] attempt ${attempt}/${max_attempts}: tar workspace to ${SYNC_USER}@${SYNC_HOST}:${SYNC_REMOTE_DIR}"
  if ssh "${SSH_BASE[@]}" "${SYNC_USER}@${SYNC_HOST}" "rm -rf '${SYNC_REMOTE_DIR}' && mkdir -p '${SYNC_REMOTE_DIR}'" &&
     tar -C "${SYNC_LOCAL_WS}" -cf - . | ssh "${SSH_BASE[@]}" "${SYNC_USER}@${SYNC_HOST}" "tar -C '${SYNC_REMOTE_DIR}' -xf -"; then
    break
  fi
  if [ "${attempt}" -ge "${max_attempts}" ]; then
    echo "ERROR: syncWorkspaceToBareMetal failed after ${max_attempts} attempts"
    exit 1
  fi
  sleep_seconds=$((attempt * 10))
  echo "[SYNC_SSH] attempt ${attempt}/${max_attempts} failed; retrying after ${sleep_seconds}s"
  sleep "${sleep_seconds}"
  attempt=$((attempt + 1))
done
echo "=== syncWorkspaceToBareMetal: done (tar over ssh) ==="
'''
    }
}

/**
 * OCI bare metal nodes often cannot reach NVIDIA Artifactory; the Jenkins orchestrator can.
 * Downloads perf media onto the pod, streams each file over SSH into
 * {@code via-media-data} on the BM ({@code wget -O - | ssh docker run dd}) so the pod never
 * accumulates the full asset set (avoids ephemeral disk OOM when e.g. 360min/720min follow smaller clips).
 *
 * Runs outside {@code node(bm)} — requires {@code env.NVOC_SSH_PRIVATE_KEY_FILE}, Artifactory
 * credentials, and {@code wget} on the orchestrator.
 *
 * @param host BM host/IP
 * @param user SSH user on BM
 * @param artifactoryUser Artifactory Basic user (same as compose downloader)
 * @param artifactoryToken Artifactory token/password
 * @param labelSlug Unique slug per parallel branch (staging dir under workspace)
 * @param useSudoDocker When true, remote docker commands use {@code sudo -E docker} (default: true)
 */
def syncPerfMediaVolumeToBareMetal(String host, String user, String artifactoryUser, String artifactoryToken, String labelSlug, boolean useSudoDocker = true) {
    // Keep MEDIA_BASE_URL and MEDIA_FILES in sync with compose/media-server.yaml (downloader).
    def mediaBaseUrl = 'https://artifactory.nvidia.com/artifactory/sw-ds-generic-bld-local/via-engine/media/perf/reencode'
    def mediaFiles = '0.5min.mp4 1min.mp4 2min.mp4 5min.mp4 10min.mp4 30min.mp4 60min.mp4 120min.mp4 720min.mkv'
    if (!host?.trim()) {
        error('syncPerfMediaVolumeToBareMetal: host is required')
    }
    if (!user?.trim()) {
        error('syncPerfMediaVolumeToBareMetal: user is required')
    }
    if (!artifactoryUser?.trim() || !artifactoryToken?.trim()) {
        error('syncPerfMediaVolumeToBareMetal: artifactoryUser and artifactoryToken are required')
    }
    if (!labelSlug?.trim()) {
        error('syncPerfMediaVolumeToBareMetal: labelSlug is required')
    }
    if (!env.NVOC_SSH_PRIVATE_KEY_FILE?.trim()) {
        error('syncPerfMediaVolumeToBareMetal: NVOC_SSH_PRIVATE_KEY_FILE is not set')
    }
    def remoteDocker = useSudoDocker ? 'sudo -E docker' : 'docker'
    echo "syncPerfMediaVolumeToBareMetal: stream Artifactory → ${user}@${host} via-media-data (${labelSlug}; one file at a time, no local pile-up)"
    withEnv([
        "MEDIA_SSH_KEY=${env.NVOC_SSH_PRIVATE_KEY_FILE}",
        "MEDIA_SSH_HOST=${host}",
        "MEDIA_SSH_USER=${user}",
        "MEDIA_REMOTE_DOCKER=${remoteDocker}",
        "MEDIA_BASE_URL=${mediaBaseUrl}",
        "MEDIA_FILES=${mediaFiles}",
        "MEDIA_AF_USER=${artifactoryUser}",
        "MEDIA_AF_TOKEN=${artifactoryToken}"
    ]) {
        sh '''#!/usr/bin/env bash
set -euo pipefail
command -v wget >/dev/null 2>&1 || { apt-get update -qq && apt-get install -y --no-install-recommends wget; }
SSH_BASE=( -i "${MEDIA_SSH_KEY}" -o StrictHostKeyChecking=no -o ConnectTimeout=120 -o BatchMode=yes )
# Ensure Docker volume exists on BM, then wipe so nginx/media-server gets a full consistent tree.
ssh "${SSH_BASE[@]}" "${MEDIA_SSH_USER}@${MEDIA_SSH_HOST}" \
  "${MEDIA_REMOTE_DOCKER} volume inspect via-media-data >/dev/null 2>&1 || ${MEDIA_REMOTE_DOCKER} volume create via-media-data"
ssh "${SSH_BASE[@]}" "${MEDIA_SSH_USER}@${MEDIA_SSH_HOST}" \
  "${MEDIA_REMOTE_DOCKER} run --rm -v via-media-data:/data alpine:3.20 sh -c 'rm -rf /data/*'"
set +x
for f in ${MEDIA_FILES}; do
  echo "[OCI perf media] streaming ${f}: wget (orchestrator) → ssh → docker volume (no full copy on pod disk)"
  # -q only: --show-progress floods Jenkins console (hundreds of lines per file).
  wget -q --user="${MEDIA_AF_USER}" --password="${MEDIA_AF_TOKEN}" -O - "${MEDIA_BASE_URL}/${f}" \
    | ssh "${SSH_BASE[@]}" "${MEDIA_SSH_USER}@${MEDIA_SSH_HOST}" \
      "${MEDIA_REMOTE_DOCKER} run --rm -i -v via-media-data:/data alpine:3.20 dd of=/data/${f} bs=32M"
  echo "[OCI perf media] finished ${f}"
done
set -x
echo "=== syncPerfMediaVolumeToBareMetal: done ==="
'''
    }
}

/**
 * Installs NVIDIA driver + Docker on a reserved bare metal node via nv-one-click.
 *
 * Must be called from the K8s pod context (not inside a node() block).
 * Host/user are passed explicitly and scoped to this branch with withEnv() so
 * parallel branches do not overwrite each other's target node.
 * The nv-one-click tarballs must already be built (run `make` in nv-one-click/
 * before calling this).
 *
 * Uses a nodeLabel-scoped working directory so parallel branches calling this
 * simultaneously do not conflict with each other.
 *
 * @param nodeLabel The node label (e.g. "H100", "RTXPRO6000BW-SE") — used only
 *                  to create a unique working directory per parallel branch.
 * @param host Bare metal node IP/host
 * @param user SSH user on the bare metal node
 */
def installInfraOnBareMetal(String nodeLabel, String host, String user) {
    if (!host?.trim()) {
        error("installInfraOnBareMetal: host is required")
    }
    if (!user?.trim()) {
        error("installInfraOnBareMetal: user is required")
    }
    def stageDir = "BM-Docker-${nodeLabel}"
    echo "HARDWARE_PROFILE=${nodeLabel}"
    withEnv([
        "BM_SSH_HOST=${host}",
        "BM_SSH_USER=${user}",
        "HARDWARE_PROFILE=${nodeLabel}"
    ]) {
        try {
            dir('./nv-one-click') {
                sh("""\
                #!/usr/bin/env bash
                set -euo pipefail
                rm -rf '${stageDir}'
                mkdir '${stageDir}'
                tar -xf deploy-lvs-bm-docker-nvoc.tar.gz -C '${stageDir}'
                chmod u+x '${stageDir}/dist/envbuild.sh'
                set -o pipefail
                '${stageDir}/dist/envbuild.sh' install -x --component infra --config-file ci/configs/config-lvs-infra.yml 2>&1 | tee '${stageDir}/output.txt'
                """.stripIndent())
            }
        } finally {
            archiveArtifacts artifacts: "nv-one-click/${stageDir}/output.txt", allowEmptyArchive: true
            archiveArtifacts artifacts: "nv-one-click/${stageDir}/dist/logs/**", allowEmptyArchive: true
        }
    }
}

/**
 * Verifies NVIDIA driver installation on bare metal node.
 */
def verifyNvidiaDriver() {
    sh 'echo "Verifying NVIDIA driver on bare metal host..."'
    sh 'echo "PATH=${PATH}"; which nvidia-smi 2>/dev/null || find /usr /opt -name nvidia-smi 2>/dev/null || echo "nvidia-smi not found on PATH or in /usr, /opt"'
    sh "nvidia-smi"
    sh "nvidia-smi --query-gpu=name,driver_version,memory.total --format=csv"
    sh 'echo "Checking GPU topology for multi-GPU communication..."'
    sh "nvidia-smi topo -m"
    sh 'echo "Checking for NVLink devices..."'
    sh "ls -la /proc/driver/nvidia-nvlink/ 2>/dev/null || echo 'No NVLink devices found'"
    sh 'echo "Checking NVLink status and connectivity..."'
    sh "nvidia-smi nvlink --status 2>/dev/null || echo 'NVLink status query not supported or no links active'"
    sh "nvidia-smi nvlink --capabilities 2>/dev/null || echo 'NVLink capabilities query not supported'"
    sh 'echo "Checking for NVSwitch devices..."'
    sh "ls -la /proc/driver/nvidia-nvswitch/devices/ 2>/dev/null || echo 'No NVSwitch devices found'"
    sh 'echo "Checking Fabric Manager status..."'
    sh "systemctl status nvidia-fabricmanager 2>/dev/null || echo 'Fabric Manager not installed or not running'"
}

/**
 * Fails early when a lockable resource does not satisfy the pre-provisioning
 * contract used while the nv-one-click install is disabled for AAAI-718.
 */
def verifyPreProvisionedBareMetalInfra() {
    sh '''#!/usr/bin/env bash
set -euo pipefail

expected_driver='580.105.08'
driver_versions="$(nvidia-smi --query-gpu=driver_version --format=csv,noheader | sort -Vu)"
if [ -z "${driver_versions}" ]; then
    echo 'ERROR: nvidia-smi returned no GPU driver versions'
    exit 1
fi
while IFS= read -r installed_driver; do
    [ -n "${installed_driver}" ] || continue
    lowest_version="$(printf '%s\n%s\n' "${expected_driver}" "${installed_driver}" | sort -V | head -n 1)"
    if [ "${lowest_version}" != "${expected_driver}" ]; then
        echo "ERROR: lockable resource driver '${installed_driver}' is older than required '${expected_driver}' (AAAI-718)"
        exit 1
    fi
done <<< "${driver_versions}"
installed_drivers="$(printf '%s' "${driver_versions}" | xargs)"

command -v nvidia-container-cli >/dev/null 2>&1 || {
    echo 'ERROR: nvidia-container-cli is unavailable on the pre-provisioned lockable resource'
    exit 1
}

if ! sudo -n docker info >/dev/null; then
    echo 'ERROR: Docker is unavailable, the daemon is not running, or passwordless sudo is not configured'
    exit 1
fi
if ! sudo -n docker compose version; then
    echo 'ERROR: Docker Compose is unavailable or cannot be run with passwordless sudo'
    exit 1
fi
sudo -n docker info --format '{{json .Runtimes}}' | grep -q '"nvidia"' || {
    echo 'ERROR: Docker does not report an NVIDIA container runtime'
    exit 1
}

echo "Pre-provisioned infrastructure verified: driver=${installed_drivers} (minimum=${expected_driver})"
nvidia-container-cli --version
'''
}

/**
 * Verifies GPU access from within a Docker container.
 * Useful for testing GPU passthrough in Docker-in-Docker scenarios.
 *
 * @return Map with keys: available (boolean), name (String), driver_version (String)
 */
def verifyGpuAccessInDocker() {
    echo "Verifying GPU access from Docker container..."

    def result = [
        available: false,
        name: 'N/A',
        driver_version: 'N/A'
    ]

    try {
        // First, run nvidia-smi to show full output in CI logs
        echo "=========================================="
        echo "Full nvidia-smi output:"
        echo "=========================================="
        sh """
        docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi
        """
        echo "=========================================="

        // Then parse specific fields for structured data
        def nvidiaSmiOutput = sh(
            script: """
            docker run --rm --gpus all nvidia/cuda:12.2.0-base-ubuntu22.04 nvidia-smi --query-gpu=name,driver_version --format=csv,noheader
            """,
            returnStdout: true
        ).trim()

        // Parse output: "NVIDIA A100-PCIE-40GB, 535.104.05"
        def parts = nvidiaSmiOutput.split(',')
        if (parts.size() >= 2) {
            result.name = parts[0].trim()
            result.driver_version = parts[1].trim()
        }

        result.available = true
        echo "✓ GPU is accessible from Docker containers"
        echo "  GPU: ${result.name}"
        echo "  Driver: ${result.driver_version}"
    } catch (Exception e) {
        echo "✗ GPU is NOT accessible from Docker containers"
        echo "Error: ${e.message}"
        echo "This may require:"
        echo "  1. nvidia-container-runtime installed on host"
        echo "  2. Docker daemon configured with nvidia runtime"
        echo "  3. For DinD: custom dind image with nvidia support"
        echo ""
    }

    return result
}

/**
 * Checks whether the base image exists in the registry (manifest inspect only, no pull).
 * Uses the same getBaseImageName() as build/pull so the tag is identical.
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @param ngcApiKey NGC API key for authentication
 * @return boolean true if base image exists in registry and can be pulled
 */
def checkBaseImageExists(String arch, String ngcApiKey) {
    loginToNvcr(ngcApiKey)
    def baseImageNcvr = getBaseImageName(arch)
    def manifestExit = sh(
        script: "docker manifest inspect ${baseImageNcvr}",
        returnStatus: true
    )
    def exists = (manifestExit == 0)
    echo "checkBaseImageExists(arch=${arch}): ${exists} (image: ${baseImageNcvr})"
    return exists
}

/**
 * Prepares the base Docker image for the specified architecture.
 * Pulls from NGCR if available; otherwise builds locally (via-engine-base), tags, and pushes.
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @param ngcApiKey NGC API key for authentication
 * @return String base image name/tag on NGCR
 */
def runBaseImageBuild(String arch, String ngcApiKey) {
    def localBaseTag = 'via-engine-base'
    echo "Preparing base image for ${arch} (will pull from NGCR or build if not found)..."

    def archInfo = parseArchitecture(arch)
    def baseArch = archInfo.arch
    def armPlatform = archInfo.platform ?: 'sbsa'

    echo "Base Architecture: ${baseArch}"
    echo "ARM Platform: ${armPlatform}"

    loginToNvcr(ngcApiKey)

    def baseImageNcvr = getBaseImageName(arch)
    echo "NGCR base image tag: ${baseImageNcvr}"

    echo "Attempting to pull base image from NGCR..."
    def pullExit = sh(
        script: "docker pull ${baseImageNcvr}",
        returnStatus: true
    )
    if (pullExit == 0) {
        echo "Successfully pulled base image from NGCR: ${baseImageNcvr}"
        return baseImageNcvr
    }

    echo "Pull failed, building base image locally as ${localBaseTag}..."

    def pkgMgr = getPackageManager()
    if (pkgMgr == 'apk') {
        sh "apk update && apk add --no-cache make || true"
    } else {
        sh "apt-get update && apt-get install -y make || true"
    }

    configureGit()

    def buildCmd = "make -C docker/base build"
    if (baseArch == 'arm64' && armPlatform) {
        buildCmd = "ARM_PLATFORM=${armPlatform} ${buildCmd}"
    }

    // Artifactory credentials needed when docker build pulls private deps
    withCredentials([
        usernamePassword(credentialsId: 'ARTIFACTORY_DS_GENERIC_BLD_TOKEN', usernameVariable: 'ARTIFACTORY_USER', passwordVariable: 'ARTIFACTORY_TOKEN')
    ]) {
        inLvsDir {
            sh buildCmd
        }
    }

    sh "docker tag ${localBaseTag} ${baseImageNcvr}"
    sh "docker push ${baseImageNcvr}"
    echo "Base image built, tagged, and pushed to NGCR: ${baseImageNcvr}"

    return baseImageNcvr
}

/*
 * Base and LVS image build/reuse: four cases from two booleans.
 *
 * canReuseBase = checkBaseImageExists(arch, ngcApiKey)  — base image exists in registry (manifest inspect).
 * canReuseLvs  = canReuseLvsImage('HEAD^')              — only app-only paths changed.
 *
 * | canReuseBase | canReuseLvs | Base stage              | LVS stage                    |
 * |--------------|-------------|--------------------------|------------------------------|
 * | true         | true        | skip                     | runLvsImageReuse()           |
 * | true         | false       | runBaseImageBuild()      | runLvsImageBuild()           |
 * | false        | true        | runBaseImageBuild()      | runLvsImageBuild()           |
 * | false        | false       | runBaseImageBuild()      | runLvsImageBuild()           |
 *
 * When (canReuseBase && canReuseLvs): skip base, call runLvsImageReuse() (pull previous LVS + overlay).
 * Otherwise: call runBaseImageBuild(), then runLvsImageBuild(arch, baseImage, ngcApiKey).
 */

/**
 * Reuses an ancestor's LVS image: finds the closest ancestor (HEAD~1, HEAD~2, ...) that has an image in the
 * registry and for which changes from that ref to HEAD are app-only; pulls that image, tags as current, prepares
 * app overlay. No push — so jkl3456 can resolve to def5678's image (ghi9012 was never pushed).
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @param previousImagePath Reusable ancestor image found for this architecture
 * @param ngcApiKey NGC API key for authentication
 * @return String the image tag (same as getImageTag(arch)) that the rest of the pipeline uses
 */
def runLvsImageReuse(String arch, String previousImagePath, String ngcApiKey) {
    previousImagePath = previousImagePath?.toString()?.trim()
    if (!previousImagePath) {
        error("Reuse path chosen but no reusable LVS image was provided for ${arch}. Ensure check-reuse found a reusable image.")
    }
    def imageTag = getImageTag(arch)
    echo "Reusing LVS image: pull ${previousImagePath}, tag as ${imageTag}, overlay app code (no push)"
    loginToNvcr(ngcApiKey)
    sh """
        docker pull ${previousImagePath}
        docker tag ${previousImagePath} ${imageTag}
    """
    def overlayDir = prepareAppOverlayDir()
    // Copy overlay into the container and commit so the image has current app code; no host mount or merge dir.
    def copyContainer = "lvs-via-copy-${env.BUILD_NUMBER ?: 'tmp'}-${arch}"
    sh """
        docker rm -f ${copyContainer} 2>/dev/null || true
        docker create --name ${copyContainer} ${imageTag}
        docker cp ${overlayDir}/. ${copyContainer}:/opt/nvidia/via/
        docker commit ${copyContainer} ${imageTag}
        docker rm -f ${copyContainer}
    """
    echo "LVS image reused: ${imageTag} (from ${previousImagePath}); overlay copied into image (no push)"
    return imageTag
}

/**
 * Retries a closure up to maxAttempts times on transient network errors.
 * Any non-network failure, or exhausted retries, re-throws immediately.
 *
 * @param maxAttempts Maximum number of attempts
 * @param body Closure to execute
 */
def retryOnNetworkError(int maxAttempts, Closure body) {
    for (int attempt = 1; attempt <= maxAttempts; attempt++) {
        try {
            body()
            return
        } catch (err) {
            def isNetworkError = err.message?.contains('TLS handshake timeout') ||
                                 err.message?.contains('net/http') ||
                                 err.message?.contains('connection refused') ||
                                 err.message?.contains('i/o timeout')
            if (isNetworkError && attempt < maxAttempts) {
                echo "Network error on attempt ${attempt}, retrying... (${err.message})"
            } else {
                throw err
            }
        }
    }
}

/**
 * Builds the VIA/LVS Docker image for the specified architecture.
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @param baseImage Base image to build from
 * @param ngcApiKey NGC API key for authentication
 * @return String built image tag
 */
def runLvsImageBuild(String arch, String baseImage, String ngcApiKey) {
    echo "Using base image: ${baseImage}"
    loginToNvcr(ngcApiKey)

    def archInfo = parseArchitecture(arch)
    def baseArch = archInfo.arch
    def imageTag = getImageTag(arch)

    echo "Building image locally: ${imageTag}"

    // Pre-flight: verify TLS connectivity to nvcr.io before attempting build.
    // A failure here indicates a firewall or persistent network issue, not a transient blip.
    // Note: nvcr.io/v2/ returns 401 when unauthenticated — that is expected and still proves
    // the registry is reachable, so we do not use --fail here.
    sh """
        echo "Pre-flight: checking TLS connectivity to nvcr.io..."
        curl --silent --max-time 30 -o /dev/null https://nvcr.io/v2/ || \
            (echo "ERROR: Cannot reach nvcr.io - possible firewall or network issue" && exit 1)
    """

    // Use registry cache for dev builds, skip for production releases or if manually disabled
    def useCache = !isTagBuild() && (params.USE_NGC_CACHE == 'true')
    def cacheRef = "${IMAGE_NAME}:buildcache-${arch}"

    if (!useCache) {
        if (isTagBuild()) {
            echo "Production release detected (TAG_NAME=${env.TAG_NAME}) - skipping cache for clean build"
        } else if (params.USE_NGC_CACHE != 'true') {
            echo "NGC cache manually disabled (USE_NGC_CACHE=${params.USE_NGC_CACHE}) - building without cache"
        }
    } else {
        echo "Using NGC registry cache from: ${cacheRef}"

        // Create buildx builder (needed for registry cache feature)
        // Remove old builder if it exists to ensure correct config
        sh """
            docker buildx rm via-builder 2>/dev/null || true
            docker buildx create --name via-builder --driver docker-container \
                --buildkitd-flags '--allow-insecure-entitlement network.host' --use
        """
    }

    // Build the image, retrying on transient network errors only; real build errors fail immediately.
    retryOnNetworkError(3) {
        inLvsDir {
            if (useCache) {
                sh """
                    docker buildx build \
                        --network host \
                        --tag ${imageTag} \
                        --cache-from type=registry,ref=${cacheRef} \
                        --cache-to type=registry,ref=${cacheRef},mode=max \
                        --build-arg BASE_IMAGE=${baseImage} \
                        --build-arg TARGETARCH=${baseArch} \
                        --build-arg BUILD_COMMIT_SHA=${env.GIT_COMMIT?.take(7) ?: 'unknown'} \
                        --load \
                        -f docker/Dockerfile .
                """
            } else {
                sh """
                    docker build \
                        --network host \
                        --tag ${imageTag} \
                        --build-arg BASE_IMAGE=${baseImage} \
                        --build-arg TARGETARCH=${baseArch} \
                        --build-arg BUILD_COMMIT_SHA=${env.GIT_COMMIT?.take(7) ?: 'unknown'} \
                        -f docker/Dockerfile .
                """
            }
        }
    }

    echo "${arch.toUpperCase()} image built locally: ${imageTag}"
    echo "Image will be pushed to NVCR after tests pass"

    return imageTag
}

/**
 * Runs unit tests on the specified image and generates CSV results.
 *
 * @param arch Architecture string (e.g. 'amd64' or 'arm64-sbsa')
 * @param imageTag Docker image tag to test
 * @param credentials Map of API keys (openaiApiKey, ngcApiKey, nvidiaApiKey, hfToken)
 */
def runUnitTests(String arch, String imageTag, Map credentials) {
    echo "Running unit tests with coverage on image: ${imageTag}"

    def isStandalone = params.TEST_IMAGE_TAG?.trim()
    def archSlug = (arch?.trim() ?: 'amd64').replaceAll(/[^A-Za-z0-9_.-]+/, '-')
    def unitResultsRoot = isStandalone ? "test-results/${archSlug}" : "."
    def unitResultsCsv = isStandalone ? "${unitResultsRoot}/unit-tests-results.csv" : 'unit-tests-results.csv'
    def pytestReport = isStandalone ? "${unitResultsRoot}/pytest-report.xml" : 'pytest-report.xml'
    def coverageReportsDir = isStandalone ? "${unitResultsRoot}/coverage_reports" : 'coverage_reports'
    def htmlCovDir = isStandalone ? "${unitResultsRoot}/htmlcov-unit" : 'htmlcov-unit'
    def coverageDataFile = isStandalone ? "${unitResultsRoot}/.coverage.unit" : '.coverage.unit'
    def outputDir = isStandalone ? "/output/${unitResultsRoot}" : '/workspace'

    // Prepare test logs and coverage directories
    sh 'mkdir -p /tmp/via-logs && chmod 777 /tmp/via-logs'
    inLvsDir {
        if (isStandalone) {
            sh "mkdir -p ${coverageReportsDir} ${htmlCovDir} && chmod -R 777 test-results"
        } else {
            sh "mkdir -p ${coverageReportsDir} ${htmlCovDir} && chmod 777 ${coverageReportsDir} ${htmlCovDir}"
        }
    }

    // In standalone image test mode:
    //   - /workspace → $(pwd)/tests  (test sources only; image's own src is used)
    //   - /output    → $(pwd)        (Jenkins workspace; all artifacts written here)
    //   - /ci-utils  → $(pwd)/ci/utils (converter script)
    // In normal build mode:
    //   - /workspace → $(pwd)        (full workspace; src mounted alongside tests)
    def volumeMounts = isStandalone
        ? """-v \$(pwd)/tests:/workspace:ro -v \$(pwd):/output -v \$(pwd)/ci/utils:/ci-utils:ro"""
        : """-v \$(pwd):/workspace"""
    def pythonPath = isStandalone
        ? '/opt/tritonserver/backends/dali/wheel/dali:/opt/nvidia/via/via-engine'
        : '/opt/tritonserver/backends/dali/wheel/dali:/workspace/src'
    def pytestTarget     = isStandalone ? 'unit --ignore=unit/test_core.py' : 'tests/unit'
    def pytestCacheFlags = isStandalone ? '-p no:cacheprovider' : ''
    def converterPath    = isStandalone ? '/ci-utils/convert_junit_to_csv.py' : '/workspace/ci/utils/convert_junit_to_csv.py'
    // In standalone mode the image may reference transient PyTorch/TorchScript
    // temp files in its coverage data that no longer exist at report time.
    def coverageFlags    = isStandalone ? '--ignore-errors' : ''

    // Run pytest with coverage and convert to CSV in same container.
    // Write directly to output volume (mounted volume works, /tmp mount doesn't).
    // Secrets are bound to the parent shell via withEnv so the bare `-e KEY`
    // flags inherit them at docker-run time — keeps the rendered sh script
    // literal (which Blue Ocean displays as the step header) free of plaintext.
    withEnv([
        "OPENAI_API_KEY=${credentials.openaiApiKey ?: ''}",
        "NGC_API_KEY=${credentials.ngcApiKey ?: ''}",
        "NVIDIA_API_KEY=${credentials.nvidiaApiKey ?: ''}",
        "VIA_VLM_API_KEY=${credentials.openaiApiKey ?: ''}",
        "HF_TOKEN=${credentials.hfToken ?: ''}",
    ]) {
    inLvsDir {
    sh """
        docker run --rm --user root --entrypoint bash \
            --gpus all \
            --runtime=nvidia \
            ${volumeMounts} \
            -w /workspace \
            -e OPENAI_API_KEY \
            -e NGC_API_KEY \
            -e NVIDIA_API_KEY \
            -e VIA_VLM_API_KEY \
            -e VIA_VLM_OPENAI_MODEL_DEPLOYMENT_NAME=gpt-4o \
            -e HF_TOKEN \
            -e PYTHONDONTWRITEBYTECODE=1 \
            -e PYTHONPATH=${pythonPath} \
            -e PIP_INDEX_URL=https://urm.nvidia.com/artifactory/api/pypi/nv-shared-pypi/simple \
            ${imageTag} \
            -c "python3 -c 'import os, sys; [os.makedirs(p, exist_ok=True) for p in sys.argv[1:]]' /tmp/via-logs ${outputDir}/coverage_reports ${outputDir}/htmlcov-unit && \
                pip install pytest-timeout coverage sseclient-py && \
                coverage run --data-file=${outputDir}/.coverage.unit -m pytest ${pytestCacheFlags} ${pytestTarget} --timeout=300 -vv --tb=short -ra --junit-xml=${outputDir}/pytest-report.xml && \
                coverage combine --data-file=${outputDir}/.coverage.unit || true && \
                coverage xml ${coverageFlags} --data-file=${outputDir}/.coverage.unit -o ${outputDir}/coverage_reports/coverage.xml && \
                coverage html ${coverageFlags} --data-file=${outputDir}/.coverage.unit -d ${outputDir}/htmlcov-unit && \
                coverage json ${coverageFlags} --data-file=${outputDir}/.coverage.unit -o ${outputDir}/coverage_reports/coverage.json && \
                coverage report ${coverageFlags} --data-file=${outputDir}/.coverage.unit > ${outputDir}/coverage_reports/coverage-summary.txt && \
                python3 ${converterPath} ${outputDir}/pytest-report.xml ${outputDir}/unit-tests-results.csv"
    """
    }
    }

    // Archive test artifacts
    archiveArtifacts artifacts: "${lvsPath(unitResultsCsv)}", allowEmptyArchive: true
    archiveArtifacts artifacts: "${lvsPath(pytestReport)}", allowEmptyArchive: true

    // Archive coverage artifacts
    archiveArtifacts artifacts: "${lvsPath("${htmlCovDir}/**")}", allowEmptyArchive: true
    archiveArtifacts artifacts: "${lvsPath(coverageDataFile)}", allowEmptyArchive: true

    // Publish coverage reports
    publishHTML(target: [
        reportDir: lvsPath(htmlCovDir),
        reportFiles: 'index.html',
        reportName: isStandalone ? "Unit Test Coverage Report (${archSlug})" : 'Unit Test Coverage Report',
        keepAll: true,
        alwaysLinkToLastBuild: true
    ])

    // Parse and display coverage summary
    def coverageSummary = readFile("${lvsPath("${coverageReportsDir}/coverage-summary.txt")}").trim()
    echo "=========================================="
    echo "COVERAGE SUMMARY"
    echo "=========================================="
    echo coverageSummary
    echo "=========================================="

    // Extract total coverage percentage for trending
    def coverageMatch = (coverageSummary =~ /TOTAL\s+\d+\s+\d+\s+\d+\s+\d+\s+(\d+)%/)
    if (coverageMatch) {
        def coveragePercent = coverageMatch[0][1].toInteger()
        echo "Total Coverage: ${coveragePercent}%"

        // Optional: Fail build if coverage is too low (disabled by default)
        // if (coveragePercent < 60) {
        //     error("Coverage ${coveragePercent}% is below minimum threshold of 60%")
        // }
    }

    echo "Unit tests passed successfully"
}


/**
 * Combines multiple coverage data files (e.g. from unit and integration runs) and generates
 * a single XML/HTML/report. Run after unit and integration tests when both produce coverage.
 * Expects coverage files (e.g. .coverage.unit, .coverage.integ) to exist in workspace from prior stages.
 *
 * @param imageTag Docker image tag to run coverage combine/report in
 * @param coverageFiles List of workspace-relative coverage data paths (e.g. ['.coverage.unit', '.coverage.integ'])
 */
def generateCombinedCoverageReport(String imageTag, List<String> coverageFiles) {
    def existing = coverageFiles.findAll { fileExists(it) }
    if (existing.isEmpty()) {
        echo "generateCombinedCoverageReport: no coverage files found from ${coverageFiles}; skipping combined report"
        return
    }
    echo "Generating combined coverage report from: ${existing}"

    sh 'mkdir -p coverage_reports htmlcov-combined && chmod 777 coverage_reports htmlcov-combined'
    def filesArg = existing.join(' ')
    sh """
        docker run --rm --user root --entrypoint bash \
            -v \$(pwd):/workspace \
            -w /workspace \
            -e PYTHONPATH=/workspace/src \
            -e PIP_INDEX_URL=https://urm.nvidia.com/artifactory/api/pypi/nv-shared-pypi/simple \
            ${imageTag} \
            -c "pip install coverage -q && \
                coverage combine ${filesArg} && \
                coverage xml -o /workspace/coverage_reports/coverage.xml && \
                coverage html -d /workspace/htmlcov-combined && \
                coverage json -o /workspace/coverage_reports/coverage.json && \
                coverage report > /workspace/coverage_reports/coverage-summary.txt"
    """

    archiveArtifacts artifacts: 'coverage_reports/**', allowEmptyArchive: true
    archiveArtifacts artifacts: 'htmlcov-combined/**', allowEmptyArchive: true
    stash name: COVERAGE_STASH_NAME, includes: 'coverage_reports/coverage.xml', allowEmpty: true
    publishHTML(target: [
        reportDir: 'htmlcov-combined',
        reportFiles: 'index.html',
        reportName: 'Combined Coverage Report',
        keepAll: true,
        alwaysLinkToLastBuild: true
    ])
    def summary = readFile('coverage_reports/coverage-summary.txt').trim()
    echo "=========================================="
    echo "COMBINED COVERAGE SUMMARY"
    echo "=========================================="
    echo summary
    echo "=========================================="
}


/**
 * Returns true if the given NGC resource version already exists.
 * Uses 'ngc registry resource info' which exits 0 when found, non-zero otherwise.
 *
 * @param org NGC org
 * @param team NGC team
 * @param resourceName Resource name (e.g. 'test_results')
 * @param version Resource version string
 * @param ngcApiKey NGC API key for authentication
 */
def ngcResourceVersionExists(String org, String team, String resourceName, String version, String ngcApiKey) {
    withEnv(["NGC_CLI_API_KEY=${ngcApiKey}"]) {
        sh """
            set +x
            ngc config set --org ${org} --team ${team}
            set -x
        """
        def exitCode = sh(
            script: "ngc registry resource info --format_type ascii ${org}/${team}/${resourceName}:${version}",
            returnStatus: true
        )
        return exitCode == 0
    }
}

/**
 * Uploads unit test results CSV to NGC.
 * Normal build mode creates AMD64 and ARM64-SBSA resource versions; standalone
 * mode uploads only the arch/image tag represented by the current CSV.
 * Skips upload if the resource version already exists.
 *
 * @param csvFilePath Path to the CSV file to upload (relative to workspace)
 * @param ngcApiKey NGC API key for authentication
 * @param imageName Full image name (will extract base name automatically)
 * @param arch Architecture label for standalone uploads
 * @param imageTagOverride Full image tag for standalone uploads; defaults to TEST_IMAGE_TAG
 */
def uploadTestResultsToNgc(String csvFilePath, String ngcApiKey, String imageName, String arch = '', String imageTagOverride = null) {
    echo "Uploading test results to NGC..."

    def isStandalone = params.TEST_IMAGE_TAG?.trim()
    def standaloneImageTag = isStandalone ? (imageTagOverride?.trim() ?: params.TEST_IMAGE_TAG.trim()) : null

    // Publish test results to the same org/team as the LVS CI images. Do not
    // derive org/team from standalone image tags; those may point at staging.
    def parts = imageName.tokenize('/')
    def targetOrg = parts[-3]
    def targetTeam = TEST_RESULTS_NGC_TEAM
    def baseImageName = parts[-1]
    echo "NGC org: ${targetOrg}, team: ${targetTeam}, image: ${baseImageName}"

    def uploadResourceVersion = { String displayArch, String imageVersion ->
        def resourceName = "${baseImageName}-${imageVersion}"
        echo "Uploading test results for ${displayArch}: ${resourceName}"

        if (ngcResourceVersionExists(targetOrg, targetTeam, 'test_results', resourceName, ngcApiKey)) {
            echo "⚠ Test results already uploaded to NGC: ${targetOrg}/${targetTeam}/test_results:${resourceName} — skipping."
        } else {
            createResource([
                target_org: targetOrg,
                target_team: targetTeam,
                target_resource_name: 'test_results',
                target_resource_version: resourceName,
                target_api_key: ngcApiKey,
                application: 'OTHER',
                framework: 'Other',
                format: 'CSV',
                precision: 'OTHER',
                short_desc: "Unit test results for ${resourceName}",
                path: csvFilePath
            ])
            echo "✓ Uploaded to NGC: ${targetOrg}/${targetTeam}/test_results:${resourceName}"
        }
    }

    if (isStandalone) {
        def standaloneVersion = getImageVersionFromTag(standaloneImageTag)
        def standaloneArch = arch?.trim() ?: 'standalone'
        uploadResourceVersion(standaloneArch.toUpperCase(), standaloneVersion)
    } else {
        uploadResourceVersion('AMD64', computeImageVersion())
        uploadResourceVersion('ARM64-SBSA', computeImageVersion('arm64-sbsa'))
    }

    echo "Test results NGC upload completed (already-existing resources were skipped)"
}

/**
 * Merges multiple test-result CSV files (same schema) into one output CSV.
 * Uses ci/utils/merge_test_result_csvs.py; skips missing inputs.
 * Supports workspace-relative paths and absolute paths (e.g. /tmp/via-logs/...).
 *
 * @param inputPaths List of paths to input CSV files (e.g. ['unit-tests-results.csv', '/tmp/via-logs/integration-test-results.csv'])
 * @param outputPath Path for merged output CSV (e.g. 'combined-test-results.csv')
 */
def mergeTestResultCsvs(List<String> inputPaths, String outputPath) {
    def existing = inputPaths.findAll { path ->
        path.startsWith('/') ? sh(script: "test -f '${path}'", returnStatus: true) == 0 : fileExists(path)
    }
    if (existing.isEmpty()) {
        error("mergeTestResultCsvs: no existing input files found from ${inputPaths}")
    }
    def escaped = existing.collect { it.contains(' ') ? "'${it}'" : it }.join(' ')
    sh "python3 ${lvsPath('ci/utils/merge_test_result_csvs.py')} -o ${outputPath} ${escaped}"
}

/**
 * Pushes test result CSV to the Dev Dashboard (non-blocking).
 *
 * @param csvFilePath Absolute path to the CSV file
 * @param dashboardApiUrl Dev Dashboard API base URL (e.g. http://10.111.53.164:8000)
 * @param arch Optional architecture label included in metadata
 * @param nvidiaDriverVersion Optional NVIDIA driver version string to include in metadata
 * @param commitHashOverride Optional tested git commit hash; defaults to Jenkins GIT_COMMIT
 */
def uploadTestResultsToDashboard(String csvFilePath, String dashboardApiUrl, String arch = '', String nvidiaDriverVersion = 'unknown', String commitHashOverride = '') {
    if (!dashboardApiUrl?.trim()) {
        echo "Warning: dashboardApiUrl not provided, skipping dashboard push"
        return
    }
    if (!fileExists(csvFilePath)) {
        echo "Warning: CSV not found at ${csvFilePath}, skipping dashboard push"
        return
    }
    echo "========================================="
    echo "Pushing test results to Dev Dashboard"
    echo "========================================="
    try {
        def dashboardResult = pushToDashboard(
            apiUrl: dashboardApiUrl,
            microservice: 'vss-video-summarization',
            branch: env.GIT_BRANCH ?: 'unknown',
            commitHash: commitHashOverride?.trim() ?: (env.GIT_COMMIT ?: ''),
            jenkinsJobUrl: env.BUILD_URL ?: '',
            jenkinsBuildNumber: env.BUILD_NUMBER ?: '',
            metadata: [
                test_type: 'unit+integration',
                arch: arch ?: 'amd64',
                nvidia_driver_version: nvidiaDriverVersion
            ],
            csvFile: csvFilePath
        )
        if (dashboardResult?.success) {
            echo "Test results successfully pushed to Dev Dashboard"
        } else {
            echo "Warning: Dashboard push may have failed. ${dashboardResult?.message ?: ''}"
        }
    } catch (Exception e) {
        echo "Warning: Failed to push results to Dev Dashboard (non-blocking): ${e.getMessage()}"
    }
}

/**
 * Gathers existing unit/integration test CSVs, merges to combined-test-results.csv,
 * archives it, pushes to the Dev Dashboard, and uploads to NGC.
 * Call from the upload-test-results stage (amd64).
 * Always merges + archives available test CSVs.
 * External uploads are gated to git-tag builds, standalone TEST_IMAGE_TAG runs,
 * or PUSH_TEST_RESULTS_EOS=true.
 *
 * @param ngcApiKey NGC API key for NGC upload
 * @param imageName Full image name (e.g. IMAGE_NAME)
 * @param arch Optional architecture label for logs (e.g. ARCH from matrix)
 * @param dashboardApiUrl Dev Dashboard API URL (e.g. http://10.111.53.164:8000)
 * @param nvidiaDriverVersion Optional NVIDIA driver version string to include in dashboard metadata
 * @param imageTagOverride Full image tag represented by the test results; defaults to TEST_IMAGE_TAG or getImageTag()
 */
def mergeAndUploadTestResults(String ngcApiKey, String imageName, String arch = '', String dashboardApiUrl = '', String nvidiaDriverVersion = 'unknown', String imageTagOverride = null) {
    echo "=========================================="
    echo "ARCHITECTURE: ${arch}"
    echo "STAGE: upload-test-results"
    echo "Git Tag: ${env.TAG_NAME}"
    echo "=========================================="
    def inputs = []
    def isStandalone = params.TEST_IMAGE_TAG?.trim()
    def archSlug = (arch?.trim() ?: 'amd64').replaceAll(/[^A-Za-z0-9_.-]+/, '-')
    def standaloneUnitResults = "test-results/${archSlug}/unit-tests-results.csv"
    if (isStandalone) {
        if (fileExists(standaloneUnitResults)) inputs << standaloneUnitResults
    } else {
        if (fileExists('unit-tests-results.csv')) inputs << 'unit-tests-results.csv'
        if (fileExists('functional-test-results.csv')) inputs << 'functional-test-results.csv'
        if (fileExists('integration-test-results.csv')) inputs << 'integration-test-results.csv'
    }
    if (inputs.isEmpty()) error('No test result CSVs found (unit, functional, and/or integration) to upload')
    def imageTagForCsv = imageTagOverride?.trim() ?: (isStandalone ? params.TEST_IMAGE_TAG.trim() : getImageTag())
    def csvFileName = "${imageTagForCsv.tokenize('/').last().replace(':', '-')}.csv"
    mergeTestResultCsvs(inputs, csvFileName)
    archiveArtifacts artifacts: csvFileName, allowEmptyArchive: false
    def testedCommitHash = isStandalone
        ? (fileExists('standalone-test-commit.txt')
            ? readFile('standalone-test-commit.txt').trim()
            : sh(script: 'git rev-parse HEAD 2>/dev/null || true', returnStdout: true).trim())
        : ''

    def shouldUploadExternally = isTagBuild() || (params.PUSH_TEST_RESULTS_EOS == true) || (params.TEST_IMAGE_TAG?.trim())
    if (shouldUploadExternally) {
        uploadTestResultsToDashboard("${lvsWorkspace()}/${csvFileName}", dashboardApiUrl, arch, nvidiaDriverVersion, testedCommitHash)
        uploadTestResultsToNgc(csvFileName, ngcApiKey, imageName, arch, imageTagForCsv)
    } else {
        echo "Skipping dashboard/NGC upload (requires git tag, TEST_IMAGE_TAG, or PUSH_TEST_RESULTS_EOS=true); merged CSV archived in Jenkins."
    }
}

def standaloneTestCredentialsFromEnv() {
    return [
        openaiApiKey: env.OPENAI_API_KEY_FOR_BUILDS,
        ngcApiKey: env.NGC_API_KEY_FOR_BUILDS,
        nvidiaApiKey: env.NVIDIA_API_KEY_FOR_BUILDS,
        hfToken: env.HF_TOKEN_FOR_BUILDS,
    ]
}

def checkoutStandaloneTestSource(String imageTag) {
    gitCheckout()
    checkoutForStandaloneTest(imageTag)
    sh 'git rev-parse HEAD > standalone-test-commit.txt'
}

def resolveStandaloneUnitTimeoutMinutes(Map config) {
    return (config.unitTimeoutMinutes ?: params.UNIT_TEST_TIMEOUT_MINUTES ?: '15').toString().toInteger()
}

/**
 * Runs standalone amd64 image tests. Kept in the helper file so the
 * declarative Jenkinsfile stays below Jenkins CPS method-size limits.
 */
def runStandaloneAmd64ImageTests(Map config = [:]) {
    def imageTag = config.imageTag?.trim()
    if (!imageTag) error('runStandaloneAmd64ImageTests requires imageTag')

    def imageName = config.imageName ?: IMAGE_NAME
    def dashboardApiUrl = config.dashboardApiUrl ?: ''
    def unitTimeoutMinutes = resolveStandaloneUnitTimeoutMinutes(config)

    stage('checkout-amd64') {
        gitlabCommitStatus(name: 'standalone-image-tests-checkout', connection: gitLabConnection('gitlab-vss-lvs')) {
            checkoutStandaloneTestSource(imageTag)
        }
    }

    stage('setup-amd64') {
        gitlabCommitStatus(name: 'standalone-image-tests-setup', connection: gitLabConnection('gitlab-vss-lvs')) {
            def gpuInfo = runWaitForDockerd('amd64')
            env.GPU_TYPE = gpuInfo?.gpuType ?: 'N/A'
            env.GPU_DRIVER_VERSION = gpuInfo?.gpuDriverVersion ?: 'N/A'
            loginToNvcr(env.NGC_API_KEY_FOR_BUILDS)
            sh "docker pull ${imageTag}"
        }
    }

    if (shouldRunUnitTests()) {
        stage('unit-tests-amd64') {
            timeout(time: unitTimeoutMinutes, unit: 'MINUTES') {
                gitlabCommitStatus(name: 'unit-tests-amd64', connection: gitLabConnection('gitlab-vss-lvs')) {
                    runUnitTests('amd64', imageTag, standaloneTestCredentialsFromEnv())
                }
            }
        }

        stage('upload-test-results-amd64') {
            timeout(time: 5, unit: 'MINUTES') {
                gitlabCommitStatus(name: 'upload-test-results-amd64', connection: gitLabConnection('gitlab-vss-lvs')) {
                    mergeAndUploadTestResults(
                        env.NGC_API_KEY_FOR_BUILDS, imageName, 'amd64',
                        dashboardApiUrl, env.GPU_DRIVER_VERSION ?: 'unknown',
                        imageTag)
                }
            }
        }
    }
}

/**
 * Runs standalone ARM64-SBSA image tests on a lockable DGX Spark node and
 * uploads results from the Kubernetes orchestrator.
 */
def runStandaloneSbsaImageTests(Map config = [:]) {
    def imageTag = config.imageTag?.trim()
    if (!imageTag) error('runStandaloneSbsaImageTests requires imageTag')

    def imageName = config.imageName ?: IMAGE_NAME
    def dashboardApiUrl = config.dashboardApiUrl ?: ''
    def sbsaNodeLabel = config.nodeLabel?.trim() ?: 'DGX-SPARK'
    def unitTimeoutMinutes = resolveStandaloneUnitTimeoutMinutes(config)
    def standaloneSbsaInputStash = 'standalone-sbsa-test-inputs'
    def standaloneSbsaResultsStash = 'standalone-sbsa-test-results'
    def sbsaWorkspaceDir = "standalone-sbsa-${env.BUILD_NUMBER ?: 'run'}"
    def sbsaDriverVersion = 'unknown'
    def lockInfo = null
    def initialJenNode = env.jen_node
    def initialLockJobUrl = env.lock_job_url

    try {
        stage('checkout-source-for-standalone-sbsa') {
            gitlabCommitStatus(name: 'standalone-sbsa-checkout', connection: gitLabConnection('gitlab-vss-lvs')) {
                checkoutStandaloneTestSource(imageTag)
                stash name: standaloneSbsaInputStash,
                    includes: "${lvsPath('ci/utils/**')},${lvsPath('tests/**')},standalone-test-commit.txt"
            }
        }

        stage('get-vault-credentials-standalone-sbsa') {
            gitlabCommitStatus(name: 'standalone-sbsa-credentials', connection: gitLabConnection('gitlab-vss-lvs')) {
                fetchCredsFromVault()
                env.NGC_API_KEY_FOR_BUILDS = env.NGC_API_KEY
                env.HF_TOKEN_FOR_BUILDS = env.HF_TOKEN
                env.OPENAI_API_KEY_FOR_BUILDS = env.OPENAI_API_KEY
                env.NVIDIA_API_KEY_FOR_BUILDS = env.NVIDIA_API_KEY
                if (!env.SSH_PUBLIC_KEY?.trim()) {
                    error('SSH_PUBLIC_KEY was not fetched from Vault; cannot reserve DGX Spark node for SBSA tests')
                }
            }
        }

        stage('reserve-standalone-sbsa-node') {
            gitlabCommitStatus(name: 'reserve-standalone-sbsa-node', connection: gitLabConnection('gitlab-vss-lvs')) {
                echo "Requesting ARM64-SBSA standalone test node with label: ${sbsaNodeLabel}"
                lockInfo = getNodeIp(sbsaNodeLabel, env.SSH_PUBLIC_KEY)
            }
        }

        stage('unit-tests-arm64-sbsa') {
            gitlabCommitStatus(name: 'unit-tests-arm64-sbsa', connection: gitLabConnection('gitlab-vss-lvs')) {
                node(lockInfo.nodeName) {
                    loginToNvcr(env.NGC_API_KEY_FOR_BUILDS)
                    sh "docker pull ${imageTag}"
                    dir(sbsaWorkspaceDir) {
                        deleteDir()
                        unstash standaloneSbsaInputStash

                        sbsaDriverVersion = sh(
                            script: "nvidia-smi --query-gpu=driver_version --format=csv,noheader | head -1 || echo unknown",
                            returnStdout: true
                        ).trim()
                        echo "DGX Spark driver version: ${sbsaDriverVersion}"

                        try {
                            timeout(time: unitTimeoutMinutes, unit: 'MINUTES') {
                                runUnitTests('arm64-sbsa', imageTag, standaloneTestCredentialsFromEnv())
                            }
                            stash name: standaloneSbsaResultsStash,
                                includes: 'test-results/arm64-sbsa/**',
                                allowEmpty: false
                        } finally {
                            try {
                                deleteDir()
                            } catch (Exception e) {
                                echo "Warning: failed to clean standalone SBSA workspace ${sbsaWorkspaceDir}: ${e.getMessage()}"
                            }
                        }
                    }
                }
            }
        }

        stage('upload-test-results-arm64-sbsa') {
            timeout(time: 5, unit: 'MINUTES') {
                gitlabCommitStatus(name: 'upload-test-results-arm64-sbsa', connection: gitLabConnection('gitlab-vss-lvs')) {
                    unstash standaloneSbsaResultsStash
                    container('jenkins-shared-lib-base') {
                        mergeAndUploadTestResults(
                            env.NGC_API_KEY_FOR_BUILDS, imageName, 'arm64-sbsa',
                            dashboardApiUrl, sbsaDriverVersion ?: 'unknown',
                            imageTag)
                    }
                }
            }
        }
    } finally {
        def fallbackNodeName = (env.jen_node && env.jen_node != initialJenNode) ? env.jen_node : null
        def fallbackLockJobUrl = (env.lock_job_url && env.lock_job_url != initialLockJobUrl) ? env.lock_job_url : null
        def nodeToRelease = lockInfo?.nodeName ?: fallbackNodeName
        def lockJobUrlToRelease = lockInfo?.lockJobUrl ?: fallbackLockJobUrl
        if (nodeToRelease || lockJobUrlToRelease) {
            echo "Releasing standalone SBSA node lock: ${nodeToRelease ?: 'unknown'}"
            try {
                container('jenkins-shared-lib-base') {
                    if (lockJobUrlToRelease) {
                        releaseLock(lockJobUrlToRelease)
                    } else {
                        echo "Warning: no lock job URL available for standalone SBSA node ${nodeToRelease ?: 'unknown'}; skipping releaseLock()"
                    }
                }
            } catch (Exception e) {
                echo "Warning: releaseLock failed for standalone SBSA node ${nodeToRelease ?: 'unknown'}: ${e.getMessage()}"
            }
        }
    }
}

/**
 * Echo stage banner (ARCH + STAGE name) for build logs.
 */
def echoStageBanner(String arch, String stageName) {
    echo "=========================================="
    echo "ARCHITECTURE: ${arch}"
    echo "STAGE: ${stageName}"
    echo "=========================================="
}

/**
 * Run integration tests (Docker Compose + API) on built image. Call from integration-tests stage.
 * @param integrationDebug when true, capture LVS container logs before/during/after integration tests and archive (see INTEGRATION_DEBUG pipeline parameter)
 * @param hfToken Optional Hugging Face token used by compose services requiring gated model access
 * @param integrationDebug when true, capture LVS container logs before/during/after API tests and archive (see INTEGRATION_DEBUG pipeline parameter)
 * @param useComposeImageOnly when true, do not replace LVS image in docker-compose.yml (use image from file as-is). Passes builtImageTag: null so prepareDockerComposeDeployment uses the else branch. Use with build stages disabled for quick integration debug.
 * @param credentials Map of API keys (openaiApiKey, ngcApiKey, nvidiaApiKey, hfToken, dbHost, dbPort, grpcPort, lvsDatabaseBackend, dockerPythonPath)
 */
/**
 * Shared implementation for functional and integration test stages.
 * Resolves the image tag, masks credentials, and delegates to runBareMetalDockerComposeTest.
 *
 * @param arch Architecture string (e.g., 'amd64')
 * @param stageName Banner label and log prefix (e.g., 'functional-tests', 'integration-tests')
 * @param debug When true, capture LVS container logs before/during/after tests
 * @param useComposeImageOnly When true, use LVS image from docker-compose.yml as-is
 * @param credentials Map with openaiApiKey, ngcApiKey, nvidiaApiKey, hfToken
 * @param runFunctional Whether to run functional tests (ci/tests/)
 * @param runIntegration Whether to run integration tests (tests/integration/)
 */
def runComposeTests(String arch, String stageName, boolean debug, boolean useComposeImageOnly, Map credentials, boolean runFunctional, boolean runIntegration, String imageTag = null) {
    echoStageBanner(arch, stageName)
    def resolvedTag = useComposeImageOnly ? null : (imageTag ?: getImageTag(arch))
    echo resolvedTag != null ? "Running ${stageName} on built image: ${resolvedTag}" : "Running ${stageName} using LVS image from docker-compose.yml (no replacement)"
    if (debug) { echo "${stageName} debug=true: will capture LVS logs before/during/after tests" }
    wrap([$class: 'MaskPasswordsBuildWrapper', varPasswordPairs: [
        [var: 'NGC_API_KEY_FOR_BUILDS', password: credentials.ngcApiKey],
        [var: 'NVIDIA_API_KEY_FOR_BUILDS', password: credentials.nvidiaApiKey],
        [var: 'OPENAI_API_KEY_FOR_BUILDS', password: credentials.openaiApiKey],
        [var: 'HF_TOKEN_FOR_BUILDS', password: credentials.hfToken],
    ]]) {
        withCredentials([
            usernamePassword(credentialsId: 'ARTIFACTORY_DS_GENERIC_BLD_TOKEN', usernameVariable: 'ARTIFACTORY_USER', passwordVariable: 'ARTIFACTORY_TOKEN')
        ]) {
            runBareMetalDockerComposeTest([
                ngcApiKey: credentials.ngcApiKey,
                nvidiaApiKey: credentials.nvidiaApiKey,
                openaiApiKey: credentials.openaiApiKey,
                hfToken: credentials.hfToken,
                artifactoryUser: env.ARTIFACTORY_USER,
                artifactoryToken: env.ARTIFACTORY_TOKEN,
                builtImageTag: resolvedTag,
                useSudo: false,
                runFunctionalTests: runFunctional,
                runIntegrationTests: runIntegration,
                debugTests: debug,
            ])
        }
    }
}

def runIntegrationTests(String arch, boolean debug = false, boolean useComposeImageOnly = false, Map credentials, String imageTag = null) {
    runComposeTests(arch, 'integration-tests', debug, useComposeImageOnly, credentials, false, true, imageTag)
    // Streaming RTVI -> Kafka -> Logstash -> ES end-to-end (opt-in).
    // runIntegrationTest above runs against the existing stack; this third
    // invocation tears that stack down, patches configmaps/config.yaml to
    // set kafka_enabled=true on summarization_online, brings the stack back
    // up with USE_RTVI_VLM=true, KAFKA_ENABLED=true and the rtvi+kafka
    // profiles, runs the pytest, then restores the config and brings the
    // stack down. Skipped unless KAFKA_E2E_TEST=1 is set in the pipeline
    // env or RUN_KAFKA_E2E param is true.
    // TODO: re-enable once Kafka E2E tests are stable in CI (remove `false &&`).
    if (false &&
        arch == 'amd64' &&
        ((env.KAFKA_E2E_TEST ?: '0') == '1' || (params.RUN_KAFKA_E2E ?: '') == 'true')) {
        stage("kafka-logstash-e2e-${arch}") {
            timeout(time: 60, unit: 'MINUTES') {
                gitlabCommitStatus(name: "kafka-logstash-e2e-${arch}", connection: gitLabConnection('gitlab-vss-lvs')) {
                    def envCredentials = [
                        openaiApiKey: credentials.openaiApiKey,
                        ngcApiKey: credentials.ngcApiKey,
                        nvidiaApiKey: credentials.nvidiaApiKey,
                        hfToken: credentials.hfToken,
                        artifactoryUser: env.ARTIFACTORY_USER,
                        artifactoryToken: env.ARTIFACTORY_TOKEN,
                    ]
                    def resolvedTag = imageTag ?: getImageTag()
                    runKafkaLogstashE2ETest(false, envCredentials, resolvedTag)

                    archiveArtifacts artifacts: 'test_kafka_logstash_e2e-report.xml', allowEmptyArchive: true
                    archiveArtifacts artifacts: 'kafka-e2e-test-results.csv', allowEmptyArchive: true
                    archiveArtifacts artifacts: 'coverage_reports/coverage-kafka-e2e.xml', allowEmptyArchive: true
                    archiveArtifacts artifacts: 'coverage_reports/coverage-kafka-e2e-summary.txt', allowEmptyArchive: true
                    archiveArtifacts artifacts: 'htmlcov-kafka-e2e/**', allowEmptyArchive: true
                    archiveArtifacts artifacts: '.coverage.kafka-e2e', allowEmptyArchive: true
                    publishHTML(target: [
                        reportDir: 'htmlcov-kafka-e2e',
                        reportFiles: 'index.html',
                        reportName: 'Kafka E2E Test Coverage Report',
                        keepAll: true,
                        alwaysLinkToLastBuild: true
                    ])
                }
            }
        }
    }

    // RTVI + LVS file-path end-to-end sanity test.
    // Mirrors run_sanity.sh in Python pytest form. Runs UNCONDITIONALLY on
    // amd64 against the shared RTVI-VLM (no Kafka, no shard throttling).
    // Only ``params.SKIP_RTVI_E2E == 'true'`` skips it (emergency hatch).
    // Must run BEFORE the ES shard limit test so the regression always
    // follows a known-good sanity pass.
    if (arch == 'amd64' && (params.SKIP_RTVI_E2E ?: '') != 'true') {
        stage("rtvi-e2e-${arch}") {
            timeout(time: 60, unit: 'MINUTES') {
                gitlabCommitStatus(name: "rtvi-e2e-${arch}", connection: gitLabConnection('gitlab-vss-lvs')) {
                    withCredentials([
                        usernamePassword(credentialsId: 'ARTIFACTORY_DS_GENERIC_BLD_TOKEN', usernameVariable: 'ARTIFACTORY_USER', passwordVariable: 'ARTIFACTORY_TOKEN')
                    ]) {
                        def envCredentials = [
                            openaiApiKey: credentials.openaiApiKey,
                            ngcApiKey: credentials.ngcApiKey,
                            nvidiaApiKey: credentials.nvidiaApiKey,
                            hfToken: credentials.hfToken,
                            artifactoryUser: env.ARTIFACTORY_USER,
                            artifactoryToken: env.ARTIFACTORY_TOKEN,
                        ]
                        def resolvedTag = imageTag ?: getImageTag(arch)
                        def rtviE2ERC = runRtviE2ETest(false, envCredentials, resolvedTag)

                        archiveArtifacts artifacts: 'test_rtvi_e2e-report.xml', allowEmptyArchive: true
                        archiveArtifacts artifacts: 'rtvi-e2e-test-results.csv', allowEmptyArchive: true
                        archiveArtifacts artifacts: 'coverage_reports/coverage-rtvi-e2e.xml', allowEmptyArchive: true
                        archiveArtifacts artifacts: 'coverage_reports/coverage-rtvi-e2e-summary.txt', allowEmptyArchive: true
                        archiveArtifacts artifacts: 'htmlcov-rtvi-e2e/**', allowEmptyArchive: true
                        archiveArtifacts artifacts: '.coverage.rtvi-e2e', allowEmptyArchive: true
                        publishHTML(target: [
                            reportDir: 'htmlcov-rtvi-e2e',
                            reportFiles: 'index.html',
                            reportName: 'RTVI E2E Test Coverage Report',
                            keepAll: true,
                            alwaysLinkToLastBuild: true
                        ])

                        if (rtviE2ERC != 0) {
                            error("[RTVI-E2E] One or more tests failed (exit code ${rtviE2ERC}). Check test reports for details.")
                        }
                    }
                }
            }
        }
    }

    // ES shard exhaustion + index-lifecycle regression guard.
    // Runs UNCONDITIONALLY on amd64 (this is the dedicated regression for
    // the very bug we're fixing — gating it off by default would defeat
    // its purpose). Only ``params.SKIP_ES_SHARD_LIMIT == 'true'`` skips
    // it, intended as an emergency escape hatch when the test is hot-
    // broken and an unrelated fix needs to land first.
    if (arch == 'amd64' && (params.SKIP_ES_SHARD_LIMIT ?: '') != 'true') {
        stage("es-shard-limit-${arch}") {
            timeout(time: 60, unit: 'MINUTES') {
                gitlabCommitStatus(name: "es-shard-limit-${arch}", connection: gitLabConnection('gitlab-vss-lvs')) {
                    withCredentials([
                        usernamePassword(credentialsId: 'ARTIFACTORY_DS_GENERIC_BLD_TOKEN', usernameVariable: 'ARTIFACTORY_USER', passwordVariable: 'ARTIFACTORY_TOKEN')
                    ]) {
                        def envCredentials = [
                            openaiApiKey: credentials.openaiApiKey,
                            ngcApiKey: credentials.ngcApiKey,
                            nvidiaApiKey: credentials.nvidiaApiKey,
                            hfToken: credentials.hfToken,
                            artifactoryUser: env.ARTIFACTORY_USER,
                            artifactoryToken: env.ARTIFACTORY_TOKEN,
                        ]
                        def resolvedTag = imageTag ?: getImageTag()
                        def esShardRC = runEsShardLimitTest(false, envCredentials, resolvedTag)
                        if (esShardRC != 0) {
                            error("[ES-SHARD-LIMIT] One or more phases failed (accumulated exit code ${esShardRC}). Check test reports for details.")
                        }

                        archiveArtifacts artifacts: 'test_es_shard_limit-*-report.xml', allowEmptyArchive: true
                        archiveArtifacts artifacts: 'es-shard-limit-*-test-results.csv', allowEmptyArchive: true
                        archiveArtifacts artifacts: 'coverage_reports/coverage-es-shard-limit-*.xml', allowEmptyArchive: true
                        archiveArtifacts artifacts: 'coverage_reports/coverage-es-shard-limit-*-summary.txt', allowEmptyArchive: true
                        archiveArtifacts artifacts: 'htmlcov-es-shard-limit-*/**', allowEmptyArchive: true
                        archiveArtifacts artifacts: '.coverage.es-shard-limit-*', allowEmptyArchive: true
                        publishHTML(target: [
                            reportDir: 'htmlcov-es-shard-limit-retain',
                            reportFiles: 'index.html',
                            reportName: 'ES Shard Limit (retain mode) Coverage Report',
                            keepAll: true,
                            alwaysLinkToLastBuild: true
                        ])
                        publishHTML(target: [
                            reportDir: 'htmlcov-es-shard-limit-drop',
                            reportFiles: 'index.html',
                            reportName: 'ES Shard Limit (drop mode) Coverage Report',
                            keepAll: true,
                            alwaysLinkToLastBuild: true
                        ])
                    }
                }
            }
        }
    }
}

/**
 * Top-level entry point for the functional-tests stage (amd64 only).
 * Deploys the full Docker Compose stack, runs ci/tests/ API endpoint tests with coverage,
 * archives artifacts, and tears down.
 *
 * @param arch Architecture string (must be 'amd64')
 * @param debug When true, capture LVS container logs before/during/after tests
 * @param useComposeImageOnly When true, use LVS image from docker-compose.yml as-is (no replacement)
 * @param credentials Map with openaiApiKey, ngcApiKey, nvidiaApiKey, hfToken
 */
def runFunctionalTests(String arch, boolean debug = false, boolean useComposeImageOnly = false, Map credentials, String imageTag = null) {
    runComposeTests(arch, 'functional-tests', debug, useComposeImageOnly, credentials, true, false, imageTag)
}

/**
 * Run all applicable test stages (unit, functional, integration, coverage, results upload).
 * Gating mirrors the per-stage params checks so both callers stay in sync automatically.
 * Used by the build-matrix (local image) and the standalone run-tests stage (registry image).
 *
 * @param arch        Architecture string — only 'amd64' runs tests
 * @param imageTag    Full image tag to test against (local or registry)
 * @param credentials Map with openaiApiKey, ngcApiKey, nvidiaApiKey, hfToken
 */
def runTestStages(String arch, String imageTag, Map credentials) {
    def shouldStopSharedRtvi = arch == 'amd64' &&
        (shouldRunFunctionalTests() || shouldRunIntegrationTests())
    def composeFilePath = "${serviceWorkspacePath()}/compose/BlueprintBuilderGenerated/docker-compose.yml"

    try {
        if (arch == 'amd64' && shouldRunUnitTests() && !shouldUseComposeImageOnly()) {
            stage("unit-tests-${arch}") {
                timeout(time: 10, unit: 'MINUTES') {
                    echoStageBanner(arch, 'unit-tests')
                    gitlabCommitStatus(name: "unit-tests-${arch}", connection: gitLabConnection('gitlab-vss-lvs')) {
                        runUnitTests(arch, imageTag, credentials)
                    }
                }
            }
        }

        if (arch == 'amd64' && shouldRunFunctionalTests()) {
            stage("functional-tests-${arch}") {
                timeout(time: 120, unit: 'MINUTES') {
                    gitlabCommitStatus(name: "functional-tests-${arch}", connection: gitLabConnection('gitlab-vss-lvs')) {
                        runFunctionalTests(arch, params.FUNCTIONAL_TESTS_DEBUG == 'true',
                            shouldUseComposeImageOnly(), credentials, imageTag)
                    }
                }
            }
        }

        if (arch == 'amd64' && shouldRunIntegrationTests()) {
            stage("integration-tests-${arch}") {
                timeout(time: 120, unit: 'MINUTES') {
                    gitlabCommitStatus(name: "integration-tests-${arch}", connection: gitLabConnection('gitlab-vss-lvs')) {
                        runIntegrationTests(arch, params.INTEGRATION_DEBUG == 'true',
                            shouldUseComposeImageOnly(), credentials, imageTag)
                    }
                }
            }
        }

        if (arch == 'amd64' && shouldRunUnitTests() && (shouldRunFunctionalTests() || shouldRunIntegrationTests()) && !shouldUseComposeImageOnly()) {
            stage("combine-coverage-${arch}") {
                catchError(buildResult: 'UNSTABLE', stageResult: 'FAILURE') {
                    timeout(time: 5, unit: 'MINUTES') {
                        gitlabCommitStatus(name: "combine-coverage-${arch}", connection: gitLabConnection('gitlab-vss-lvs')) {
                            generateCombinedCoverageReport(imageTag, ['.coverage.unit', '.coverage.integ'])
                        }
                    }
                }
            }
        }

        if (arch == 'amd64' && shouldRunUnitTests() && (shouldRunFunctionalTests() || shouldRunIntegrationTests()) && !shouldUseComposeImageOnly()) {
            stage("upload-test-results-${arch}") {
                timeout(time: 5, unit: 'MINUTES') {
                    gitlabCommitStatus(name: "upload-test-results-${arch}", connection: gitLabConnection('gitlab-vss-lvs')) {
                        mergeAndUploadTestResults(credentials.ngcApiKey, IMAGE_NAME, arch,
                            params.DASHBOARD_API_URL, env.GPU_DRIVER_VERSION ?: 'unknown')
                    }
                }
            }
        }
    } finally {
        if (shouldStopSharedRtvi) {
            stopSharedRtviVlm(false, composeFilePath)
        }
    }
}

/**
 * Wait for Docker daemon and optionally verify GPU (amd64). Call from wait-for-dockerd stage.
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa')
 * @return Map with gpuType (String) and gpuDriverVersion (String) for use in build metadata; non-amd64 returns 'N/A' for gpuType
 */
def runWaitForDockerd(String arch) {
    def gpuInfo = [ gpuType: 'N/A', gpuDriverVersion: 'N/A' ]
    // Debug: confirm matrix ARCH and actual pod/container CPU (x86_64 = amd64, aarch64 = arm64)
    def actualCpu = sh(script: 'uname -m', returnStdout: true).trim()
    echo "DEBUG: Matrix ARCH (requested): ${arch} | Container CPU (actual): ${actualCpu}"
    waitForDockerd()
    if (arch == 'amd64') {
        def result = verifyGpuAccessInDocker()
        gpuInfo.gpuType = result?.name ?: 'N/A'
        gpuInfo.gpuDriverVersion = result?.driver_version ?: 'N/A'
    }
    return gpuInfo
}

/**
 * Writes build metadata (branch, commit, build number, timestamp, GPU type, base image) to a file
 * and archives it. Call from archive-build-metadata stage inside build-multi-arch-images.
 * Uses architecture-local metadata passed by the Jenkinsfile; falls back to env/params for legacy callers.
 *
 * @param arch Architecture string (e.g., 'amd64', 'arm64-sbsa') used for the output filename
 * @param baseImageOverride Base image used by this architecture branch
 * @param gpuTypeOverride GPU type detected by this architecture branch
 * @param gpuDriverVersionOverride GPU driver version detected by this architecture branch
 */
def writeAndArchiveBuildMetadata(
    String arch,
    String baseImageOverride = null,
    String gpuTypeOverride = null,
    String gpuDriverVersionOverride = null
) {
    def baseImage = baseImageOverride ?: env.BASE_IMAGE_FOR_ARCH ?: params.BASE_IMAGE ?: 'N/A'
    def gpuType = gpuTypeOverride ?: env.GPU_TYPE ?: 'N/A'
    def gpuDriverVersion = gpuDriverVersionOverride ?: env.GPU_DRIVER_VERSION ?: 'N/A'
    def timestamp = sh(script: "date -u +'%Y-%m-%dT%H:%M:%SZ'", returnStdout: true).trim()
    def metadata = """
branch_name=${env.BRANCH_NAME ?: 'N/A'}
commit_hash=${env.GIT_COMMIT ?: 'N/A'}
build_number=${env.BUILD_NUMBER ?: 'N/A'}
timestamp=${timestamp}
gpu_type=${gpuType}
gpu_driver_version=${gpuDriverVersion}
base_image=${baseImage}
""".trim()
    def filename = "build-metadata-${arch}.txt"
    writeFile file: filename, text: metadata
    echo "Build metadata written to ${filename}"
    archiveArtifacts artifacts: filename, allowEmptyArchive: false
}

/**
 * Push built image to NVCR. Call from push-image stage.
 * Skips push when this architecture reused an LVS image since no new registry image was built.
 */
def runPushImage(String arch, String ngcApiKey, boolean lvsImageReused = false) {
    echoStageBanner(arch, 'push-image')
    if (lvsImageReused) {
        echo "LVS image was reused (app-only overlay path); skipping push to NVCR"
        return
    }
    wrap([$class: 'MaskPasswordsBuildWrapper', varPasswordPairs: [
        [var: 'NGC_API_KEY_FOR_BUILDS', password: ngcApiKey]
    ]]) {
        def imageTag = getImageTag(arch)
        echo "Tests passed! Pushing ${arch} image to NVCR: ${imageTag}"
        loginToNvcr(ngcApiKey)
        sh "docker push ${imageTag}"
        env.ANY_LVS_IMAGE_PUSHED = 'true'
        echo "${arch.toUpperCase()} image pushed successfully to NVCR"
    }
}

/**
 * Verify that all selected architecture images exist in registry.
 */
def verifyArchImages(String buildArchs, String ngcApiKey) {
    wrap([$class: 'MaskPasswordsBuildWrapper', varPasswordPairs: [
        [var: 'NGC_API_KEY', password: ngcApiKey]
    ]]) {
        loginToNvcr(ngcApiKey)
        sh 'echo "Verifying multi-arch images..."'

        if (buildArchs == 'all' || buildArchs == 'amd64') {
            def amd64Version = computeImageVersion('amd64')
            dockerVerify(
                image_name: "${IMAGE_NAME}",
                image_version: "${amd64Version}"
            )
        }

        if (buildArchs == 'all' || buildArchs == 'arm64-sbsa') {
            def arm64SbsaVersion = computeImageVersion('arm64-sbsa')
            dockerVerify(
                image_name: "${IMAGE_NAME}",
                image_version: "${arm64SbsaVersion}"
            )
        }

        if (buildArchs == 'all' || buildArchs == 'arm64-igpu') {
            def arm64IgpuVersion = computeImageVersion('arm64-igpu')
            dockerVerify(
                image_name: "${IMAGE_NAME}",
                image_version: "${arm64IgpuVersion}"
            )
        }
    }
}

/**
 * Merge multi-architecture images into unified manifests.
 */
def mergeArchImages(String imageVersion, String versionSuffix, String ngcApiKey) {
    wrap([$class: 'MaskPasswordsBuildWrapper', varPasswordPairs: [
        [var: 'NGC_API_KEY', password: ngcApiKey]
    ]]) {
        loginToNvcr(ngcApiKey)
        sh 'echo "Merging multi-arch images..."'

        // Compute image versions for all architectures.
        // arm64-sbsa is intentionally excluded from the multiarch manifest: it is published
        // as a standalone <version>-arm64-sbsa tag and must not appear in the general
        // 3.x.y multiarch tag (which targets amd64 + arm64-igpu/Jetson users only).
        def amd64Version     = computeImageVersion('amd64')
        def arm64IgpuVersion = computeImageVersion('arm64-igpu')

        // Create versioned multi-arch manifest (with build suffix)
        dockerMerge(
            image_name: "${IMAGE_NAME}",
            image_version: "${imageVersion}-${versionSuffix}",
            image_versions: [
                "${amd64Version}",
                "${arm64IgpuVersion}"
            ]
        )

        // Create latest multi-arch manifest (without build suffix)
        dockerMerge(
            image_name: "${IMAGE_NAME}",
            image_version: "${imageVersion}",
            image_versions: [
                "${amd64Version}",
                "${arm64IgpuVersion}"
            ]
        )
    }
}

/**
 * Print all built image URLs based on BUILD_ARCHS parameter.
 */
def printBuiltImages(String buildArchs, String imageVersion, String versionSuffix) {
    echo "============== IMAGE NAMES ====================================="
    if (buildArchs == 'all') {
        echo "Multi-arch (amd64, arm64-igpu) with build suffix: ${IMAGE_NAME}:${imageVersion}-${versionSuffix}"
        echo "Multi-arch (amd64, arm64-igpu) latest: ${IMAGE_NAME}:${imageVersion}"
    }
    if (buildArchs == 'all' || buildArchs == 'amd64') {
        def amd64Version = computeImageVersion('amd64')
        echo "amd64 only: ${IMAGE_NAME}:${amd64Version}"
    }
    if (buildArchs == 'all' || buildArchs == 'arm64-sbsa') {
        def arm64SbsaVersion = computeImageVersion('arm64-sbsa')
        echo "arm64-sbsa only: ${IMAGE_NAME}:${arm64SbsaVersion}"
    }
    if (buildArchs == 'all' || buildArchs == 'arm64-igpu') {
        def arm64IgpuVersion = computeImageVersion('arm64-igpu')
        echo "arm64-igpu only: ${IMAGE_NAME}:${arm64IgpuVersion}"
    }
    echo "==============================================================="
}

/**
 * Run security scans for all selected architectures.
 */
def runSecurityScansForAllArchs(String buildArchs, String imageName, String nspectId) {
    def archsToScan = []
    if (buildArchs == 'all') {
        archsToScan = ['amd64', 'arm64-sbsa', 'arm64-igpu']
    } else {
        archsToScan = [buildArchs]
    }

    archsToScan.each { arch ->
        echo "Running security scan for ${arch}..."
        runSecurityScanForArch(imageName, arch, nspectId)
    }
}

/**
 * Execute Build_Examples stage - reads CI config, creates parallel stages,
 * and orchestrates bare metal node testing.
 */
def runBuildExamplesStage(String examplesToBuild, String baseImage, def loadedHelpers) {
    echo "Building examples..."
    sh "ls -lrt"

    dir('./nv-one-click') {
        def ciData = readYaml file: 'ci/ci.yml'
        echo "CI Data: ${ciData}"

        // Filter ciData on choices
        def filteredCiData = ciData.findAll {
            it.name == examplesToBuild || "All" == examplesToBuild
        }

        // Group stages by prefix
        def stagesByPrefix = filteredCiData.groupBy { stage ->
            stage.name.split('-')[0]
        }

        // Create parallel stages
        def parallelStages = [:]
        sh "ls -lrt && pwd"

        stagesByPrefix.each { prefix, stages ->
            parallelStages[prefix] = createExampleStage(prefix, stages, baseImage, loadedHelpers)
        }

        // Run the parallel stages
        parallel parallelStages
    }
}

/**
 * Helper to create a single example stage closure.
 */
def createExampleStage(String prefix, List stages, String baseImage, def loadedHelpers) {
    return {
        stage(prefix) {
            stages.each { stageData ->
                def stageName = stageData.name
                def tarFile = stageData.tar
                def configFile = stageData.config
                def helperStashName = "pipeline-helpers-${prefix}-${stageName}".replaceAll(/[^A-Za-z0-9_.-]/, '-')
                stage(stageName) {
                    catchError(buildResult: 'SUCCESS', stageResult: 'FAILURE') {
                        def containerName = String.format("%s-for-%s", baseImage, prefix).toLowerCase()
                        echo "Running stage ${stageName} in container: ${containerName}"
                        container(containerName) {
                            // Load helpers for this container context
                            def containerHelpers = load(resolveHelpersGroovyPath())
                            containerHelpers.prepareExampleStage(stageName, tarFile)
                            try {
                                echo "Executing try block"
                                containerHelpers.runInfraInstall(stageName, configFile)

                                dir(lvsWorkspace()) {
                                    stash name: helperStashName, includes: "${lvsPath('ci/pipeline-helpers.groovy')}"
                                }
                                script {
                                    try {
                                        node(env.jen_node) {
                                            unstash helperStashName
                                            // Execute bare metal node tests using helper
                                            def bmHelpers = load(resolveHelpersGroovyPath())
                                            bmHelpers.executeBareMetalNodeTests([
                                                runBuild: shouldRunBuild()
                                            ])
                                        }
                                    } catch (Exception e) {
                                        echo "Error in executing commands on locked node"
                                        throw e
                                    }
                                }

                                containerHelpers.runInfraUninstall(stageName, configFile)
                            }
                            catch (Exception e) {
                                echo "Executing catch block"
                                containerHelpers.runInfraUninstall(stageName, configFile)
                                throw e
                            }
                        }
                    }
                }
            }
        }
    }
}

/**
 * Executes the bare metal node tests including driver verification and Docker Compose deployment.
 *
 * @param config Map containing: runBuild (boolean)
 */
def executeBareMetalNodeTests(Map config = [:]) {
    def runBuild = config.runBuild
    if (!(runBuild instanceof Boolean)) {
        runBuild = "${runBuild}".toLowerCase() == 'true'
    }

    gitCheckout()

    stage('Verify NVIDIA Driver Installation') {
        verifyNvidiaDriver()
    }

    stage('Docker Compose Perf Test') {
        withCredentials([string(credentialsId: 'NGC_API_KEY_LVS', variable: 'NGC_API_KEY_LVS')]) {
            wrap([$class: 'MaskPasswordsBuildWrapper', varPasswordPairs: [
                [var: 'NGC_API_KEY_LVS', password: env.NGC_API_KEY_LVS],
                [var: 'NVIDIA_API_KEY', password: env.NVIDIA_API_KEY],
                [var: 'OPENAI_API_KEY', password: env.OPENAI_API_KEY],
                [var: 'HF_TOKEN', password: env.HF_TOKEN ?: '']
            ]]) {
                def builtImageTag = resolveBuiltImageTag(runBuild)
                def composeFilePath = "${serviceWorkspacePath()}/compose/BlueprintBuilderGenerated/LVS_Integrated-CR2_GptOss20B_3gpu/docker-compose.yml"
                def perfConfigId = "3xH100-1x2-cr2-8b-gptoss-30b"

                runBareMetalDockerComposePerfTest([
                    ngcApiKey: env.NGC_API_KEY_LVS,
                    nvidiaApiKey: env.NVIDIA_API_KEY,
                    openaiApiKey: env.OPENAI_API_KEY,
                    hfToken: env.HF_TOKEN,
                    builtImageTag: builtImageTag,
                    composeFilePath: composeFilePath,
                    scenarioName: 'quick_test',
                    vlmGpus: '0,1',
                    llmGpus: '2',
                    configId: perfConfigId,
                    uploadToMinIO: 'LVS'
                ])
            }
        }
    }

    stage('Archive Perf Schema') {
        archiveArtifacts artifacts: 'perf/benchmark/vss-perf-report/benchmark_results/*.json', allowEmptyArchive: true
    }
}

/**
 * Applies custom-node overrides to a list of perf configs (single place for this logic).
 * For each config whose nodeLabel is a key in customNodeOverrides, returns a copy with
 * nodeLabel replaced by the override value; configs whose nodeLabel is not a key in
 * customNodeOverrides are returned unchanged.
 *
 * Implementation: uses a .collect {} (Groovy closure) over the config list; only the
 * nodeLabel field is changed so getNode() uses the custom label. All other fields from
 * perf-configs.yaml (id, composePath, enabled, vlmGpus, etc.) are preserved, so schema
 * changes in perf-configs.yaml do not require changes here.
 *
 * Example (customNodeOverrides = ['H100-SXM': 'a4u8g-0141_H100']):
 *   BEFORE: [id: 'h100_1_1x1_...', nodeLabel: 'H100-SXM', composePath: 'compose/...', ...]
 *   AFTER:  [id: 'h100_1_1x1_...', nodeLabel: 'a4u8g-0141_H100', composePath: 'compose/...', ...]
 *
 * @param configs             List of config maps (e.g. after filter by selectedIds and enabled)
 * @param customNodeOverrides  Map of base nodeLabel -> custom label (e.g. ['H100-SXM': 'a4u8g-0141_H100'])
 * @return New list of configs with nodeLabel replaced when in overrides (empty overrides returns configs unchanged).
 */
def applyCustomNodeOverridesToConfigs(List configs, Map customNodeOverrides) {
    if (!customNodeOverrides) return configs ?: []
    def overrides = customNodeOverrides
    return (configs ?: []).collect { cfg ->
        if (overrides.containsKey(cfg.nodeLabel)) {
            def copy = new HashMap(cfg)
            copy.nodeLabel = overrides[cfg.nodeLabel]
            return copy
        }
        return cfg
    }
}

/**
 * Builds the custom-node override map from Jenkins pipeline params.
 * Single place for "which params map to which base label"; add new GPU types here.
 *
 * @param params Map with keys CUSTOM_NODE_LABEL_H100, CUSTOM_NODE_LABEL_RTXPRO6000BW (trimmed; empty or "null" => no override).
 * @return Map of base nodeLabel -> custom label (e.g. [H100-SXM: 'a4u8g-0141_H100']), possibly empty.
 */
def buildCustomNodeOverridesFromParams(Map params) {
    def overrides = [:]
    def h100 = params?.CUSTOM_NODE_LABEL_H100?.trim()
    if (h100 && !h100.equalsIgnoreCase('null')) overrides['H100-SXM'] = h100
    def rtx = params?.CUSTOM_NODE_LABEL_RTXPRO6000BW?.trim()
    if (rtx && !rtx.equalsIgnoreCase('null')) overrides['RTXPRO6000BW-SE'] = rtx
    return overrides
}

/**
 * Applies custom-node overrides to perf configs and returns effective configs plus
 * the list of custom labels in use. The label list is retained for compatibility
 * with the temporarily disabled nv-one-click setup path.
 *
 * @param allConfigs      Full list from ci/perf-configs.yaml (perf_configs)
 * @param selectedIds     "all" or comma-separated config ids
 * @param customNodeOverrides Map of base nodeLabel -> custom label (e.g. ['H100-SXM': 'a4u8g-0141_H100'])
 * @return List of two elements: [effectiveConfigs, customLabelsInUse]. effectiveConfigs
 *         are filtered by selectedIds and enabled, with nodeLabel replaced when in overrides.
 *         customLabelsInUse is the list of override values (custom labels) that appear in effectiveConfigs.
 */
def getPerfConfigsWithCustomNodeOverrides(List allConfigs, String selectedIds, Map customNodeOverrides) {
    def overrides = customNodeOverrides ?: [:]
    def configs = (selectedIds?.trim() == 'all')
        ? (allConfigs ?: [])
        : (allConfigs ?: []).findAll { cfg -> (selectedIds?.split(',')?.collect { it.trim() } ?: []).contains(cfg.id) }
    configs = configs.findAll { it.enabled != false }
    def effectiveConfigs = applyCustomNodeOverridesToConfigs(configs, overrides)
    def customLabelsInUse = effectiveConfigs.collect { it.nodeLabel }.unique().findAll { overrides.values().contains(it) }
    return [effectiveConfigs, customLabelsInUse]
}

/**
 * Runs performance benchmarks for multiple Docker Compose configurations.
 *
 * Configs are grouped by nodeLabel.  Groups run in PARALLEL (one Jenkins parallel
 * branch per unique node label), so H100 and RTX6000PROBW-SE tests execute
 * concurrently.  Within each group the individual configs run sequentially on the
 * single reserved node — the node is reserved once, all configs for that label run,
 * then the node is released.
 *
 * Each individual config runs inside its own named stage(), so Jenkins' Stage View
 * and Blue Ocean show per-config pass/fail status independently.  A single config
 * failure does not abort sibling configs (add failFast:true to the stageMap if you
 * want abort-on-first-failure behaviour).
 *
 * Thread safety: this function calls getNodeIp(), which returns per-branch lock data
 * (nodeName + lockJobUrl). Each parallel branch keeps those values local and avoids
 * shared env state for releaseLock().
 *
 * Prerequisites (must be set before calling this function):
 *   - Vault credentials exported to pipeline env
 *   - SSH public key available as the sshPublicKey argument
 *   - ci/pipeline-helpers.groovy stashed under helpersStashName
 *
 * @param args Map with:
 *   allConfigs        - Full list of config maps loaded from ci/perf-configs.yaml
 *   selectedIds       - "all" or comma-separated config ids to run (default: "all")
 *   imageTag          - LVS image tag to test; null/empty → auto-computed from git
 *   scenarioName      - Benchmark scenario for vss_perf_benchmark.py (default: "quick_test")
 *   credentials       - Map: { ngcApiKey, nvidiaApiKey, openaiApiKey, hfToken, artifactoryUser, artifactoryToken }
 *   sshPublicKey      - SSH public key string for bare metal node access
 *   helpersStashName  - Stash name containing ci/pipeline-helpers.groovy
 *   uploadToMinio     - Boolean; upload results to MinIO when true (default: false)
 *   customNodeOverrides - Optional map of base nodeLabel -> custom label (e.g. [H100: 'a4u8g-0141_H100']).
 *                         When set, configs with that nodeLabel use the custom label for getNode().
 *                         Under AAAI-718 all lockable nodes are treated as pre-provisioned.
 */

/**
 * Stable stash id for OCI-deferred MinIO upload (one stash per parallel branch label + config id).
 * Stash carries only BM-written JSON under perf/benchmark/results/ — not archiveArtifacts (controller-only).
 */
def ociMinioPerfStashName(String nodeLabel, String configId) {
    def l = nodeLabel.replaceAll(/[^a-zA-Z0-9._-]+/, '_')
    def c = configId.replaceAll(/[^a-zA-Z0-9._-]+/, '_')
    def b = (env.BUILD_NUMBER ?: '0').toString()
    return "ociMinio-${b}-${l}-${c}"
}

/**
 * Unstash perf JSON from the BM and upload to MinIO from the K8s orchestrator workspace.
 * Upload scope matches vss_perf_benchmark.py when --upload: a single upload_result_file(json_path, "LVS") for the
 * Metropolis JSON from build_and_save (path like .../vss-perf-results/lvs_<config>_<ts>.json — see generate_run_id
 * in vss_perf_common.py). The copied tree also contains auxiliary *.json (metrics, execution_summary, etc.); we only
 * upload files named lvs_*.json so behavior aligns with on-node --upload, not a blind *.json sweep.
 * Uses perf/benchmark/vss_perf_common.py from the pod checkout; needs pip pydantic + minio.
 * OCI bare-metal agents often cannot reach internal MinIO; this runs after node(bm) returns to the pod.
 */
def uploadOciDeferredPerfJsonToMinio(String branchLabel, List cfgBatch) {
    withCredentials([
        string(credentialsId: 'metropolis-minio-access-key', variable: 'MINIO_ACCESS_KEY'),
        string(credentialsId: 'metropolis-minio-secret-key', variable: 'MINIO_SECRET_KEY')
    ]) {
        cfgBatch.each { cfg ->
            def stashName = ociMinioPerfStashName(branchLabel, cfg.id)
            try {
                unstash stashName
            } catch (Exception e) {
                echo "Warning: [${branchLabel}/${cfg.id}] MinIO skip — unstash failed (empty results?): ${e.getMessage()}"
                return
            }
            withEnv(["OCI_MINIO_CFG_ID=${cfg.id}"]) {
                // Match on-node --upload: only the VSS Metropolis result JSON (lvs_* from generate_run_id for service LVS),
                // typically under vss-perf-results/ when run_benchmark.sh uses -O vss-perf-results. Skip metrics/*.json peers.
                sh """
                set +e
                cd "${serviceWorkspacePath()}/perf/benchmark"
                RES_DIR="results/\${OCI_MINIO_CFG_ID}"
                if [ ! -d "\$RES_DIR" ]; then
                  echo "Warning: [OCI MinIO] missing directory after unstash: \$RES_DIR"
                  exit 0
                fi
                python3 -m pip install -q pydantic minio 2>/dev/null || pip3 install -q pydantic minio
                n=\$(find "\$RES_DIR" -type f -name 'lvs_*.json' 2>/dev/null | wc -l)
                n=\$(echo "\$n" | tr -d ' ')
                if [ "\${n:-0}" -eq 0 ]; then
                  echo "Warning: [OCI MinIO] no lvs_*.json under \$RES_DIR (expected build_and_save Metropolis JSON)"
                  exit 0
                fi
                find "\$RES_DIR" -type f -name 'lvs_*.json' 2>/dev/null | while read -r f; do
                  [ -f "\$f" ] || continue
                  python3 vss_perf_common.py "\$f" --service LVS || echo "Warning: MinIO upload failed for \$f"
                done
                """
            }
        }
    }
}

def runPerfConfigsParallel(Map args) {
    def allConfigs       = args.allConfigs       ?: []
    def selectedIds      = args.selectedIds?.trim() ?: 'all'
    def scenarioName     = args.scenarioName     ?: 'quick_test'
    def credentials      = args.credentials      ?: [:]
    def sshPublicKey     = args.sshPublicKey
    def helpersStashName = args.helpersStashName ?: 'perf-pipeline-helpers'
    def uploadToMinIO    = args.uploadToMinio     ? 'LVS' : null
    def customNodeOverrides = args.customNodeOverrides ?: [:]
    // set of labels for which we skip installInfraOnBareMetal (derived from overrides only).
    def customNodeLabels    = (customNodeOverrides.values() ?: []) as Set

    if (!sshPublicKey?.trim()) {
        error("runPerfConfigsParallel: sshPublicKey is required (vault credential fetch may have failed)")
    }

    // ── Filter: "all" or a comma-separated list of specific ids ─────────────
    def configs
    if (selectedIds == 'all') {
        configs = allConfigs
    } else {
        def ids = selectedIds.split(',').collect { it.trim() }.findAll { it }
        configs = allConfigs.findAll { ids.contains(it.id) }
        def missingIds = ids.findAll { id -> !configs.any { it.id == id } }
        if (missingIds) {
            echo "Warning: config id(s) not found in perf-configs.yaml: ${missingIds.join(', ')}"
        }
        if (configs.isEmpty()) {
            error("runPerfConfigsParallel: no configs matched PERF_CONFIG_IDS='${selectedIds}'")
        }
    }

    // ── Filter: skip disabled configs (enabled: false) ───────────────────────
    def disabledConfigs = configs.findAll { it.enabled == false }
    if (disabledConfigs) {
        echo "Skipping ${disabledConfigs.size()} disabled config(s): ${disabledConfigs.collect { it.id }.join(', ')}"
    }
    configs = configs.findAll { it.enabled != false }
    if (configs.isEmpty()) {
        error("runPerfConfigsParallel: no enabled configs to run")
    }

    // ── Apply custom node overrides ───────────────────────────────────────────
    if (customNodeOverrides) {
        configs = applyCustomNodeOverridesToConfigs(configs, customNodeOverrides)
        echo "Custom node overrides applied: ${customNodeOverrides}"
    }

    echo "Perf configs to run (${configs.size()}): ${configs.collect { it.id }.join(', ')}"

    // ── Group by nodeLabel, preserving insertion order ───────────────────────
    def byNode = [:] as LinkedHashMap
    configs.each { cfg ->
        byNode.get(cfg.nodeLabel, []) << cfg
    }

    // ── Build parallel stage map — one branch per unique node label ──────────
    //
    // stageMap is a plain LinkedHashMap<String, Closure>.  The key becomes the
    // branch name shown in the Jenkins stage view; the value is the code that runs
    // for that branch.  Passing stageMap to parallel() launches all branches
    // concurrently and waits for all to complete (or fail) before returning.
    //
    // Stage hierarchy produced in Jenkins UI:
    //   run-perf-benchmarks
    //   ├── perf-H100          (parallel branch — one branch per node label)
    //   │   ├── h100-cr2-gptoss20b-3gpu   (nested stage — one per config)
    //   │   └── h100-cr2-nemo3nano-nim
    //   └── perf-RTX6000PROBW-SE
    //       ├── rtxpro-integrated-cr2-nemo3nano-8gpu
    //       └── rtxpro-nim-cr2-nemo3nano-8gpu
    //
    def stageMap = [:] as LinkedHashMap
    byNode.each { nodeLabel, nodeCfgs ->
        // Bind loop variables to local vals before the closure captures them
        def label    = nodeLabel
        def cfgBatch = nodeCfgs

        stageMap["perf-${label}"] = {
            echo "=== [${label}] Reserving node for ${cfgBatch.size()} config(s) ==="

            // Reserve this node type once for the entire batch.
            // getNodeIp() returns a local map (nodeName + lockJobUrl) to keep
            // parallel branches independent from shared env fields.
            def lockInfo = getNodeIp(label, sshPublicKey)
            def jenNode = lockInfo.nodeName
            def lockJobUrl = lockInfo.lockJobUrl
            def bmHost = lockInfo.nodeHost
            def bmUser = lockInfo.nodeUser
            if (!bmHost?.trim() || !bmUser?.trim()) {
                error("runPerfConfigsParallel: missing node host/user for ${label}; getNodeIp returned host='${bmHost}' user='${bmUser}'")
            }

            try {
                // AAAI-718: all lockable resources are now pre-provisioned with
                // NVIDIA driver 580.105.08, Docker, and NVIDIA Container Toolkit.
                // Temporarily retain the old mutation path as comments so it can be
                // restored quickly if the lockable-resource contract changes.
                // if (!customNodeLabels.contains(label)) {
                //     repairAptOnBareMetal(bmHost, bmUser)
                //     installInfraOnBareMetal(label, bmHost, bmUser)
                // } else {
                //     echo "=== [${label}] Custom node: skipping installInfraOnBareMetal ==="
                // }
                echo "=== [${label}] Using pre-provisioned lockable-resource infrastructure (AAAI-718) ==="

                // OCI BM agents cannot reach internal GitLab. Match non-OCI BM: run the same
                // shallow sparse gitCheckoutShallow() on the orchestrator (in a subdir so we do
                // not delete the full pod workspace / nv-one-click), then sync that tree to the BM (tar over ssh).
                def useOciSyncedWorkspace = isOciStyleBareMetalNode(jenNode)
                def remoteWsDir = null
                if (useOciSyncedWorkspace) {
                    def labelSlug = label.replaceAll(/[^a-zA-Z0-9._-]+/, '_')
                    def sparseRelDir = "lvs-perf-sparse-${labelSlug}"
                    echo "=== [${label}] OCI BM: gitCheckoutShallow() on orchestrator in ${sparseRelDir}/ (same as BM sparse paths) ==="
                    dir(sparseRelDir) {
                        gitCheckoutShallow()
                    }
                    def sparseAbs = "${env.WORKSPACE}/${sparseRelDir}/${LVS_ROOT}"
                    remoteWsDir = "/home/${bmUser}/lvs-perf-ws-${env.BUILD_TAG ?: env.BUILD_NUMBER}-${labelSlug}"
                    echo "=== [${label}] OCI BM: sync ${sparseAbs} → ${bmHost}:${remoteWsDir} (tar over ssh) ==="
                    syncWorkspaceToBareMetal(bmHost, bmUser, remoteWsDir, sparseAbs)
                    echo "=== [${label}] OCI BM: hydrate docker volume via-media-data (orchestrator Artifactory download → BM) ==="
                    syncPerfMediaVolumeToBareMetal(
                        bmHost,
                        bmUser,
                        credentials.artifactoryUser?.toString() ?: '',
                        credentials.artifactoryToken?.toString() ?: '',
                        labelSlug,
                        true
                    )
                }

                node(jenNode) {
                    def runPerfOnBm = {
                        unstash helpersStashName
                        if (!useOciSyncedWorkspace) {
                            def bmHelpersPre = load(resolveHelpersGroovyPath())
                            bmHelpersPre.gitCheckoutShallow()
                        } else {
                            echo "=== [${label}] OCI BM: using orchestrator gitCheckoutShallow + synced tree (skip git on agent) ==="
                        }

                        def runPerfBody = {
                            def bmHelpers = load(resolveHelpersGroovyPath())

                            // Verify GPU hardware once per node reservation
                            bmHelpers.verifyNvidiaDriver()
                            bmHelpers.verifyPreProvisionedBareMetalInfra()

                            // Resolve the base image/version once, then derive the arch-specific
                            // image for each config. This allows one perf run to mix x86, SBSA,
                            // and iGPU configs while accepting either a bare version or full ref.
                            def baseImageVersion = args.imageTag ?: bmHelpers.computeImageVersion()
                            echo "[${label}] Base image tag/version: ${baseImageVersion}"

                            // Each config gets its own stage so Jenkins tracks pass/fail individually.
                            // Configs run sequentially; a failure in one does not skip the rest
                            // (the stage is marked failed, but the loop continues).
                            cfgBatch.each { cfg ->
                                stage("${cfg.id}") {
                                    echo "Compose: ${cfg.composePath}"
                                    if (cfg.description) echo "${cfg.description}"

                                    sh "rm -rf perf/benchmark/vss-perf-report"

                                    def composeFilePath = serviceWorkspacePath(cfg.composePath)
                                    def imageArch = cfg.imageArch?.trim()
                                    if (!imageArch) {
                                        if (cfg.nodeLabel == 'DGX-SPARK') {
                                            imageArch = 'arm64-sbsa'
                                        } else if (cfg.nodeLabel == 'JETSON-THOR') {
                                            imageArch = 'arm64-igpu'
                                        } else {
                                            imageArch = 'amd64'
                                        }
                                        echo "[${label}/${cfg.id}] imageArch not set; inferred ${imageArch} from nodeLabel=${cfg.nodeLabel}"
                                    }
                                    def resolvedTag = bmHelpers.resolveImageTagForArch(baseImageVersion, imageArch, [returnFullRef: true])
                                    def releaseTag = bmHelpers.getImageVersionFromTag(resolvedTag)
                                    echo "[${label}/${cfg.id}] Image tag (${imageArch}): ${resolvedTag}"

                                    wrap([$class: 'MaskPasswordsBuildWrapper', varPasswordPairs: [
                                        [var: 'NGC_API_KEY_PERF',    password: credentials.ngcApiKey    ?: ''],
                                        [var: 'NVIDIA_API_KEY_PERF', password: credentials.nvidiaApiKey ?: ''],
                                        [var: 'OPENAI_API_KEY_PERF', password: credentials.openaiApiKey ?: ''],
                                        [var: 'HF_TOKEN_PERF',       password: credentials.hfToken      ?: ''],
                                        [var: 'ARTIFACTORY_USER_PERF',  password: credentials.artifactoryUser  ?: ''],
                                        [var: 'ARTIFACTORY_TOKEN_PERF', password: credentials.artifactoryToken ?: '']
                                    ]]) {
                                        bmHelpers.runBareMetalDockerComposePerfTest([
                                            ngcApiKey:       credentials.ngcApiKey,
                                            nvidiaApiKey:    credentials.nvidiaApiKey,
                                            openaiApiKey:    credentials.openaiApiKey,
                                            hfToken:         credentials.hfToken,
                                            artifactoryUser: credentials.artifactoryUser,
                                            artifactoryToken: credentials.artifactoryToken,
                                            builtImageTag:   resolvedTag,
                                            useSudo:         true,
                                            composeFilePath: composeFilePath,
                                            scenarioName:    scenarioName,
                                            vlmGpus:         cfg.vlmGpus ?: null,
                                            llmGpus:         cfg.llmGpus ?: null,
                                            vlmModel:        cfg.vlmModel ?: null,
                                            llmModel:        cfg.llmModel ?: null,
                                            visionInputTokens: cfg.vision_input_tokens ?: null,
                                            gpuModel:        cfg.nodeLabel ?: null,
                                            configId:        cfg.id,
                                            uploadToMinIO:   uploadToMinIO,
                                            ociDeferMinioUpload: (useOciSyncedWorkspace && uploadToMinIO),
                                            release:         releaseTag
                                        ])
                                    }

                                    sh """
                                    if [ -d perf/benchmark/vss-perf-report ]; then
                                        mkdir -p 'perf/benchmark/results/${cfg.id}'
                                        cp -r perf/benchmark/vss-perf-report/. 'perf/benchmark/results/${cfg.id}/'
                                    fi
                                    """
                                    archiveArtifacts artifacts: "perf/benchmark/results/${cfg.id}/**", allowEmptyArchive: true
                                    if (useOciSyncedWorkspace && uploadToMinIO) {
                                        stash name: ociMinioPerfStashName(label, cfg.id),
                                            includes: "perf/benchmark/results/${cfg.id}/**"
                                    }
                                    sh "rm -rf perf/benchmark/vss-perf-report"
                                }
                            }
                        }

                        if (useOciSyncedWorkspace) {
                            runPerfBody()
                        } else {
                            dir(LVS_ROOT) {
                                runPerfBody()
                            }
                        }
                    }
                    if (useOciSyncedWorkspace) {
                        ws(remoteWsDir) {
                            runPerfOnBm()
                        }
                    } else {
                        runPerfOnBm()
                    }
                }

                if (useOciSyncedWorkspace && uploadToMinIO) {
                    echo "=== [${label}] OCI: MinIO upload from orchestrator (K8s pod; BM cannot reach internal MinIO) ==="
                    uploadOciDeferredPerfJsonToMinio(label, cfgBatch)
                }
            } finally {
                echo "=== [${label}] Releasing lock: ${jenNode} ==="
                try {
                    releaseLock(lockJobUrl)
                } catch (Exception e) {
                    echo "Warning: releaseLock failed for ${label}/${jenNode}: ${e.getMessage()}"
                }
            }
        }
    }

    // Launch all node-type groups in parallel and wait for all to finish
    parallel stageMap
}

// ============================================================================
// Perf trigger (trigger-perf-on-release): tag consistent with push image
// ============================================================================

/**
 * Image tag to pass to the perf pipeline as LVS_IMAGE_TAG (version only).
 * Uses getImageTag('amd64') so it matches runPushImage on the amd64 matrix branch.
 */
def getImageTagToRunPerf() {
    def fullTag = getImageTag('amd64')
    return fullTag.contains(':') ? fullTag.tokenize(':').last() : fullTag
}

/**
 * Trigger the downstream perf pipeline. Resolves LVS_IMAGE_TAG via getImageTagToRunPerf().
 * Call from trigger-perf-on-release stage (workspace must have checkout so getImageTag() works).
 *
 * @param config Map with: jobName (required); optional parameterOverrides (List)
 */
def triggerPerfPipeline(Map config) {
    def jobName = config.jobName
    def imageTag = getImageTagToRunPerf()
    echo "Release build detected (TAG_NAME=${env.TAG_NAME}). Triggering perf pipeline: ${jobName} with LVS_IMAGE_TAG=${imageTag}"

    def parameters = [string(name: 'LVS_IMAGE_TAG', value: imageTag)]
    if (config.parameterOverrides != null && !config.parameterOverrides.isEmpty()) {
        parameters.addAll(config.parameterOverrides)
    }
    build job: jobName,
        parameters: parameters,
        wait: false
}

return this
