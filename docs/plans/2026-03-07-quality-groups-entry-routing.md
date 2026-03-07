# Quality Groups Entry Routing Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Make top-level service groups actually route through quality-tier subgroups first instead of merely listing them as decorative children.

**Architecture:** Convert service-facing parent groups such as `ai组`, `币安`, `pikpak`, `Microsoft`, and `Amazon` from manual `select` aggregators into active health-checked entry groups. Keep the existing `*-专线`, `*-高倍率`, and `*-其他` subgroups as the actual quality buckets, and let the parent group automatically choose among them in priority order.

**Tech Stack:** Clash Meta YAML config, Python generator, GitHub Actions, Node.js validation script.

---

### Task 1: Add regression check for service entry groups

**Files:**
- Modify: `scripts/check_no_empty_proxy_groups.js`

**Step 1: Write the failing test**

Add a check that service entry groups are not plain `select` groups in generated config.

**Step 2: Run test to verify it fails**

Run: `node scripts/check_no_empty_proxy_groups.js dist/config.yaml`
Expected: FAIL once the new assertion is added because current service groups are still `select`.

**Step 3: Write minimal implementation**

Teach the checker to validate that `ai组`, `币安`, `pikpak`, `Microsoft`, and `Amazon` are active auto-selection groups.

**Step 4: Run test to verify it passes**

Run: `node scripts/check_no_empty_proxy_groups.js dist/config.yaml`
Expected: PASS after generated config is rebuilt.

### Task 2: Promote service groups into active entry groups

**Files:**
- Modify: `test.yaml`

**Step 1: Write the failing test**

Use the checker from Task 1 to require active service entry groups.

**Step 2: Run test to verify it fails**

Run: `node scripts/check_no_empty_proxy_groups.js dist/config.yaml`
Expected: FAIL because the current generated config still uses `select`.

**Step 3: Write minimal implementation**

Change `ai组`, `币安`, `pikpak`, `Microsoft`, and `Amazon` to active health-checked groups that prioritize `专线`, then `高倍率`, then `其他`, then existing fallbacks.

**Step 4: Run test to verify it passes**

Run: GitHub Actions build or a local generator run.
Expected: Generated config uses active entry groups and the validation script passes.

### Task 3: Verify CI catches regressions

**Files:**
- Modify: `.github/workflows/clash-config.yml`

**Step 1: Write the failing test**

Ensure workflow runs the validation step against generated config.

**Step 2: Run test to verify it fails**

Run: review workflow definition before patch.
Expected: missing service-entry validation if not already covered.

**Step 3: Write minimal implementation**

Keep the validation step so generated configs cannot ship with empty groups or inactive service entry groups.

**Step 4: Run test to verify it passes**

Run: GitHub Actions on push.
Expected: workflow succeeds and auto-commits regenerated output.
