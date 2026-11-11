# Train viewport and isolated test website implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Single-screen desktop Train page and separately launchable synthetic preview.
**Architecture:** CSS-only desktop Train layout; dedicated read-only HTTP handler; launcher `test` command.
**Tech Stack:** Python 3.12+, stdlib HTTP server, existing self-contained HTML/CSS, pytest.
**Spec:** `docs/superpowers/specs/2026-11-11-train-preview.md`

## Global Constraints
- No call into production training, live prediction, AutoTrade, credential vault, or official settlement sync.
- Port 8900 for preview, refuse port 8899 and other collisions, bind loopback only.
- POST always forbidden; all dynamic numbers unambiguously synthetic.
- Preserve narrow viewport readability, all actual Train stage DOM IDs, and existing styling.
- No training shutdown, restart, or implicit promotion.

## Task 1: Train viewport
**Files:** modify `src/kalshi_edge/bootstrap/site_pages.py`; add `tests/bootstrap/test_train_viewport.py`
- [ ] Write failing tests for desktop no-page-scroll two-column layout and short viewport fallback.
- [ ] Confirm regression fails before CSS implementation.
- [ ] Implement desktop min-height 761px layout and compact spacing; retain all progress labels/controls.
- [ ] Verify focused tests.

## Task 2: Independent test server
**Files:** create `src/kalshi_edge/bootstrap/preview_site.py`; add `tests/bootstrap/test_preview_site.py`
- [ ] Write failing endpoint, security, mock-data and asset tests; confirm tests fail.
- [ ] Build a standalone localhost handler, no shared handler or services; POST 403 and no writes.
- [ ] Verify focused tests.

## Task 3: CLI integration
**Files:** modify `src/kalshi_edge/launcher.py` and `tests/bootstrap/test_launcher.py`
- [ ] Write failing CLI parser and isolation tests, confirm red.
- [ ] Add `kalshiedge test [--port] [--no-browser]`, reserved-port check and preview banner.
- [ ] Run full CI, review, and merge only on green.
