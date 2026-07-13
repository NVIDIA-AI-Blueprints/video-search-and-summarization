# SPDX-FileCopyrightText: Copyright (c) 2026 NVIDIA CORPORATION & AFFILIATES. All rights reserved.
# SPDX-License-Identifier: Apache-2.0
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0

Feature: Overlay-aware picture URL cache partitioning
  The /picture/url endpoint cache key is partitioned by a hash of the
  caller-supplied options that affect rendered bytes (overlay JSON,
  resize width/height, debug flag). Distinct configurations must
  produce distinct cached JPEGs; identical configurations must hit
  the cache; non-meaningful differences (key order, list order,
  whitespace) must NOT fragment the cache.

  Background:
    Given the VST API is configured for picture overlay cache test
    When the list of available replay streams is fetched for picture overlay test
    And the recording timelines are fetched for picture overlay test
    And a valid picture timestamp is selected for picture overlay test

  # ---------------------------------------------------------------------
  # Headline scenarios
  # ---------------------------------------------------------------------

  Scenario: Two distinct picture overlay configurations produce distinct cached files
    Then a picture URL is requested with overlay configuration A
    And a picture URL is requested with overlay configuration B for the same timestamp
    And the two picture responses return distinct cached files

  Scenario: Identical picture overlay configuration reuses the cached file
    Then a picture URL is requested with overlay configuration A
    And the same picture URL with overlay configuration A is requested again
    And the two overlay-A picture responses reuse the same cached file

  Scenario: No-overlay request and overlay-A request produce distinct cached files
    Then a picture URL is requested with no overlay
    And a picture URL is requested with overlay configuration A for the same timestamp
    And the no-overlay picture and overlay-A picture are distinct cached files

  Scenario: Two identical no-overlay picture requests reuse the cached file
    Then a picture URL is requested with no overlay
    And the same picture URL with no overlay is requested again
    And the two no-overlay picture responses reuse the same cached file

  Scenario: Positional RGBA values partition picture cache entries
    Then a picture URL is requested with a red positional color code
    And a picture URL is requested with a blue positional color code for the same timestamp
    And the two picture responses return distinct cached files

  # ---------------------------------------------------------------------
  # Per-field permutations
  # ---------------------------------------------------------------------

  Scenario Outline: Varying picture overlay field <field> partitions the cache
    Then a picture URL is requested with the baseline picture overlay
    And a picture URL is requested with the baseline overlay but <field> = <variant>
    And the picture variant response is a different cached file from the baseline
    And the picture variant cache is reused on a repeat request

    Examples:
      | field             | variant     |
      | color             | green       |
      | color             | white       |
      | thickness         | 1           |
      | thickness         | 8           |
      | opacity           | 64          |
      | debug             | true        |
      | bbox.showAll      | true        |
      | bbox.showObjId    | false       |
      | bbox.objectId     | ["9999"]    |
      | bbox.classType    | ["Person"]  |
      | bbox.objIdPosition  | 1         |
      | bbox.objIdTextColor | yellow    |

  # ---------------------------------------------------------------------
  # Cross-axis: resize hints (width/height) are hashed too
  # ---------------------------------------------------------------------

  Scenario: Changing width while keeping overlay constant partitions the cache
    Then a picture URL is requested with overlay configuration A and width 640
    And a picture URL is requested with overlay configuration A and width 320
    And the two width-varied picture responses are distinct cached files

  Scenario: Changing height while keeping overlay constant partitions the cache
    Then a picture URL is requested with overlay configuration A and height 480
    And a picture URL is requested with overlay configuration A and height 240
    And the two height-varied picture responses are distinct cached files

  # The picture API treats `debug` as a top-level URL param distinct from
  # the overlay JSON's `debug` field. Both contribute to the cache key
  # (the former via computePictureConfigHash's explicit pickup, the latter
  # via the canonicalized overlay JSON), so we cover them independently -
  # the per-field outline already covers overlay.debug.
  Scenario: Top-level debug query parameter partitions the cache
    Then a picture URL is requested with the baseline picture overlay
    And a picture URL is requested with the baseline overlay and top-level debug=true
    And the top-level-debug picture response is a different cached file from the baseline
    And the top-level-debug picture cache is reused on a repeat request

  # ---------------------------------------------------------------------
  # Negative scenarios: non-meaningful differences must hit cache
  # ---------------------------------------------------------------------

  Scenario: Reordered objectId list produces the same cached picture
    Then a picture URL is requested with the baseline picture overlay
    And a picture URL is requested with the baseline overlay but bbox.objectId reordered
    And the reordered picture response reuses the baseline cached file

  Scenario: Whitespace-padded overlay JSON produces the same cached picture
    Then a picture URL is requested with the baseline picture overlay
    And a picture URL is requested with whitespace-padded baseline overlay JSON
    And the whitespace-padded picture response reuses the baseline cached file

  # ---------------------------------------------------------------------
  # Matrix: 6 distinct overlay configs all produce distinct cached pics
  # ---------------------------------------------------------------------

  Scenario: Six distinct picture overlay configurations all produce distinct cached files
    Then six distinct picture overlay configurations are each requested
    And all six picture responses return mutually-distinct cached files
    And each of the six picture configurations reuses its cached file on a repeat request
