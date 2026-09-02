// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import {
  VSS_UI_ARTIFACT_CLOSE,
  VSS_UI_ARTIFACT_MAX_LENGTH,
  VSS_UI_ARTIFACT_OPEN,
  extractVssUiArtifacts,
} from "../../lib-src/utils/vssUiArtifacts";

const wrap = (value: unknown) =>
  `${VSS_UI_ARTIFACT_OPEN}${JSON.stringify(value)}${VSS_UI_ARTIFACT_CLOSE}`;

describe("extractVssUiArtifacts", () => {
  it("extracts multiple valid artifacts from surrounding prose", () => {
    const search = {
      version: "1.0",
      kind: "vss.search.results",
      payload: { data: [] },
    };
    const alerts = {
      version: "1.0",
      kind: "vss.alert.incidents",
      payload: { incidents: [{ category: "fire" }] },
    };

    expect(
      extractVssUiArtifacts(`before${wrap(search)}middle${wrap(alerts)}after`)
    ).toEqual([search, alerts]);
  });

  it("ignores malformed, incompatible, and non-VSS envelopes", () => {
    const text = [
      `${VSS_UI_ARTIFACT_OPEN}not-json${VSS_UI_ARTIFACT_CLOSE}`,
      wrap({ version: "2.0", kind: "vss.search.results", payload: {} }),
      wrap({ version: "1.0", kind: "search.results", payload: {} }),
      wrap({ version: "1.0", kind: "vss.search.results", payload: [] }),
    ].join("");
    expect(extractVssUiArtifacts(text)).toEqual([]);
  });

  it("rejects an oversized artifact without blocking later valid artifacts", () => {
    const oversized = `${VSS_UI_ARTIFACT_OPEN}${"x".repeat(
      VSS_UI_ARTIFACT_MAX_LENGTH + 1
    )}${VSS_UI_ARTIFACT_CLOSE}`;
    const valid = wrap({
      version: "1.0",
      kind: "vss.search.results",
      payload: { data: [] },
    });

    expect(extractVssUiArtifacts(`${oversized}${valid}`)).toEqual([
      { version: "1.0", kind: "vss.search.results", payload: { data: [] } },
    ]);
  });
});
