// SPDX-License-Identifier: MIT
/**
 * @nv-metropolis-bp-vss-ui/chat
 *
 * VSS-owned agent chat. Replaces the vendored `@nemo-agent-toolkit/ui` chat so
 * the UI is not coupled to a single agent toolkit and carries no third-party
 * UI source. Backends are selected by protocol (OpenAI-compatible HTTP/SSE,
 * WebSocket, OpenClaw gateway) rather than by vendor.
 *
 * Behaviour is specified by the test suite under `__tests__/`, ported from the
 * outgoing implementation so parity is verifiable rather than assumed.
 */

export * from './utils/queryProcessing';
