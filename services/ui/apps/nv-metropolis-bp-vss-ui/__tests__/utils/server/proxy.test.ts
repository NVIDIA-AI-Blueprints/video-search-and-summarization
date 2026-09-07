// SPDX-FileCopyrightText: Copyright (c) 2026, NVIDIA CORPORATION & AFFILIATES. All rights reserved.
// SPDX-License-Identifier: Apache-2.0

import { getVssProxyBaseUrl } from "../../../pages/api/proxy/[...path]";

describe("VSS media proxy configuration", () => {
  it("uses an explicit deployment target without a trailing slash", () => {
    expect(
      getVssProxyBaseUrl({
        VSS_PROXY_BASE_URL: "http://release-vss-vios-ingress:30888/",
      } as NodeJS.ProcessEnv),
    ).toBe("http://release-vss-vios-ingress:30888");
  });

  it("keeps the Compose ingress as the backwards-compatible default", () => {
    expect(
      getVssProxyBaseUrl({ HAPROXY_PORT: "8080" } as NodeJS.ProcessEnv),
    ).toBe("http://vss-haproxy-ingress:8080");
  });

  it.each([
    "file:///tmp/vss.sock",
    "http://user:password@vss-ingress", // pragma: allowlist secret
    "http://vss-ingress?target=other",
  ])("rejects an unsafe target: %s", (target) => {
    expect(() =>
      getVssProxyBaseUrl({ VSS_PROXY_BASE_URL: target } as NodeJS.ProcessEnv),
    ).toThrow("VSS_PROXY_BASE_URL");
  });
});
