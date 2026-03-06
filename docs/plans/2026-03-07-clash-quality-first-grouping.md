# Clash Quality-First Grouping Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Rework Clash proxy-group generation so existing rule targets stay intact while node placement becomes quality-first and `Proxy` exposes 8 curated manual picks.

**Architecture:** Move grouping from hardcoded per-group regex branches to metadata-driven group assembly. The template defines each group's quality intent and fallback structure, while the Python script parses node quality, multiplier, and region once and reuses those tags to populate all groups.

**Tech Stack:** Python 3.11+, PyYAML, existing Clash template/output pipeline.

---

### Task 1: Replace template grouping metadata

**Files:**
- Modify: `test.yaml`

**Step 1: Write the template metadata**

- Replace the existing `proxy-groups` block with quality-first entry groups and url-test quality pools.
- Keep existing rule target names unchanged.
- Add generation-only metadata fields such as `fallback-groups`, `require-quality`, `preferred-regions`, and `manual-pick-count`.

**Step 2: Sanity-check the YAML structure**

Run: `python -c "from pathlib import Path; import yaml; yaml.safe_load(Path('test.yaml').read_text(encoding='utf-8')); print('ok')"`

Expected: `ok`

**Step 3: Do not commit automatically**

- Leave git history untouched unless the user explicitly asks for a commit.

### Task 2: Refactor grouping logic in the build script

**Files:**
- Modify: `scripts/build_clash_config.py`

**Step 1: Add node tag extraction helpers**

- Add reusable helpers for parsing region, multiplier, and quality.
- Add utilities for metadata parsing and ordered node sorting.

**Step 2: Replace hardcoded business-group assembly**

- Remove the current region-first and business-specific regex packing logic.
- Build groups from template metadata instead.
- Generate `Proxy` manual picks with the fixed `4 dedicated + 3 high + 1 other` mix.

**Step 3: Strip generation-only metadata before output**

- Ensure the final emitted config does not contain template-only fields.

### Task 3: Verify syntax without building outputs

**Files:**
- Verify: `scripts/build_clash_config.py`

**Step 1: Run Python syntax compilation**

Run: `python -m compileall scripts`

Expected: compilation succeeds without syntax errors.

**Step 2: Stop short of running the build**

- Do not run `python scripts/build_clash_config.py` locally.
- Leave regeneration to GitHub Actions, per user preference.

Plan complete and saved to `docs/plans/2026-03-07-clash-quality-first-grouping.md`. In this session I will proceed with the approved implementation path directly, without committing unless you ask.
