# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

Feature: Overlay-aware video URL cache partitioning
  The /url endpoint cache key is partitioned by a hash of the
  caller-supplied configuration (overlay block + transcode/audio/container
  switches). Distinct configurations must produce distinct cached files;
  identical configurations must hit the cache; non-meaningful differences
  (key order, list order, whitespace) must NOT fragment the cache.

  Background:
    Given the VST API is configured for overlay cache partition test
    When the list of available replay streams is fetched for overlay test
    And the recording timelines are fetched for overlay test
    And a valid video time range is selected for overlay test

  # ---------------------------------------------------------------------
  # Headline scenarios: the original three regression cases
  # ---------------------------------------------------------------------

  Scenario: Two distinct overlay configurations produce distinct cached files
    Then a blocking video URL is requested with overlay configuration A
    And a blocking video URL is requested with overlay configuration B for the same time range
    And the two responses return distinct cached files

  Scenario: Identical overlay configuration reuses the cached file
    Then a blocking video URL is requested with overlay configuration A
    And the same blocking video URL with overlay configuration A is requested again
    And the two overlay-A responses reuse the same cached file

  Scenario: Adding a configuration does not collide with no-config cache
    Then a blocking video URL is requested with no configuration
    And a blocking video URL is requested with overlay configuration A for the same time range
    And the no-config response and the overlay-A response are distinct cached files

  Scenario: Debug font size partitions debug-overlay cache entries
    Then a blocking video URL is requested with debug font size 8
    And a blocking video URL is requested with debug font size 24 for the same time range
    And the two responses return distinct cached files

  Scenario: Close proximity area factors produce distinct cached files
    Then a blocking video URL is requested with proximity area factor 1.0001
    And a blocking video URL is requested with proximity area factor 1.0004 for the same time range
    And the two responses return distinct cached files

  # ---------------------------------------------------------------------
  # Per-field permutations: vary ONE field of an overlay config at a time.
  # Each row asserts the variant produces a different cached file from the
  # baseline AND that the variant itself caches on a repeat request.
  # ---------------------------------------------------------------------

  Scenario Outline: Varying overlay field <field> partitions the cache
    Then a blocking video URL is requested with the baseline overlay configuration
    And a blocking video URL is requested with the baseline configuration but <field> = <variant>
    And the variant response is a different cached file from the baseline
    And the variant cache is reused on a repeat request

    Examples:
      | field             | variant     |
      | color             | green       |
      | color             | white       |
      | thickness         | 1           |
      | thickness         | 8           |
      | opacity           | 64          |
      | opacity           | 200         |
      | debug             | true        |
      | pose              | true        |
      | bbox.showAll      | true        |
      | bbox.showObjId    | false       |
      | bbox.objectId     | ["9999"]    |
      | bbox.objectId     | ["7","8","9"] |
      | bbox.classType    | ["Person"]  |
      | bbox.classType    | ["Car","Truck"] |
      | bbox.objIdPosition  | 1         |
      | bbox.objIdPosition  | 2         |
      | bbox.objIdTextColor | yellow    |
      | bbox.objIdTextBGColor | green   |

  # ---------------------------------------------------------------------
  # Negative permutations: differences that MUST NOT fragment the cache.
  # ---------------------------------------------------------------------

  Scenario: Reordered objectId list produces the same cached file
    Then a blocking video URL is requested with the baseline overlay configuration
    And a blocking video URL is requested with the baseline configuration but bbox.objectId reordered
    And the reordered response reuses the baseline cached file

  Scenario: Reordered top-level JSON keys produce the same cached file
    Then a blocking video URL is requested with the baseline overlay configuration
    And a blocking video URL is requested with the baseline overlay configuration with keys reordered
    And the key-reordered response reuses the baseline cached file

  Scenario: Whitespace-only JSON differences produce the same cached file
    Then a blocking video URL is requested with the baseline overlay configuration
    And a blocking video URL is requested with whitespace-padded JSON of the baseline configuration
    And the whitespace-padded response reuses the baseline cached file

  # ---------------------------------------------------------------------
  # Matrix: 10 mutually-distinct configurations must produce 10 distinct
  # cached files, and each must hit cache on a repeat request.
  # ---------------------------------------------------------------------

  Scenario: Ten distinct overlay configurations all produce distinct cached files
    Then ten distinct overlay configurations are each requested as blocking video URLs
    And all ten responses return mutually-distinct cached files
    And each of the ten configurations reuses its cached file on a repeat request

  # ---------------------------------------------------------------------
  # Cross-axis: container & disableAudio are NOT part of the overlay block
  # but DO contribute to the configuration hash. Validate both axes.
  # ---------------------------------------------------------------------

  Scenario: Changing disableAudio while keeping overlay constant partitions the cache
    Then a blocking video URL is requested with overlay configuration A and disableAudio=false
    And a blocking video URL is requested with overlay configuration A and disableAudio=true
    And the disableAudio variants return distinct cached files

  # ---------------------------------------------------------------------
  # Exact file-bounds + overlay: the request boundaries would engage the
  # full-file fast path (tryFindFullFileMatch + generateFullFileUrl) if
  # no transformations were asked for. With overlay set, the gate must
  # fall through to the standard remux + overlay-aware cache path so
  # different overlay variants do not collapse onto the raw recording's
  # symlink. This pins down the contract that "exact bounds" alone does
  # not bypass the overlay partitioning.
  # ---------------------------------------------------------------------

  Scenario: Exact file-bounds overlay variants produce distinct cached files
    Then the selected time range is replaced with the recording's exact file boundaries
    And a blocking video URL is requested with overlay configuration A
    And a blocking video URL is requested with overlay configuration B for the same time range
    And the two responses return distinct cached files
