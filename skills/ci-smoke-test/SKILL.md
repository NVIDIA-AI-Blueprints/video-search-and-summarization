---
name: "ci-smoke-test"
description: "Dummy skill used only to verify that downstream CI detects and validates changes under the repository skills/ folder. Use only for CI smoke testing, not for product workflows."
version: "0.0.2"
license: "Apache License 2.0"
metadata:
  author: "ci <test@nvidia.com>"
  tags:
    - ci
    - downstream
    - smoke-test
  domain: ci
---

# CI Smoke Test

## Purpose

This dummy skill exists to exercise the external downstream CI path for skill
content changes in this repository. It is intentionally small so test commits
can focus on validation, signing, and reporting behavior.

## Expected Behavior

When this file changes, the downstream pipeline should detect
`skills/ci-smoke-test` as changed content and run the standard skill validation
and report flow against it.

## Usage

Use this skill only as a CI smoke-test fixture. It does not provide product
functionality and should not be invoked for user workflows.
