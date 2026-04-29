# tsrpi7 → tsmedia-with-player Consolidation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Preserve all uncommitted work from `/home/g1tech/tsrpi7/{tsrpi5,tsrpi5-v2}` into proper commits on `genesis1tech/tsmedia-with-player`, leaving `/home/g1tech/tsmedia-with-player/` as the sole local clone, and update auto-memory.

**Architecture:** Two-branch migration. (1) In the `tsrpi5-v2` worktree, commit local working-tree edits as two commits on the existing `feat/barcode-repo-lookup-v2` branch and push. (2) In the `tsmedia-with-player` clone, branch off `master` for the manifest-upload work, commit, push, open PR. Then update auto-memory and hand off `/home/g1tech/tsrpi7/` to user for manual deletion.

**Tech Stack:** git, gh CLI, plain filesystem ops. No code changes beyond preserving existing uncommitted edits.

**Spec:** `docs/superpowers/specs/2026-04-26-tsrpi7-consolidation-design.md`

---

### Task 1: Pre-flight verification

**Files:** read-only checks. No edits.

- [ ] **Step 1: Verify tsrpi5-v2 worktree is on the right branch with the expected dirty state**

Run:
```bash
cd /home/g1tech/tsrpi7/tsrpi5-v2 && \
  git rev-parse --abbrev-ref HEAD && \
  git status --porcelain | wc -l && \
  git status --porcelain
```

Expected:
- Branch: `feat/barcode-repo-lookup-v2`
- Modified line count: 9 (7 modified + 2 untracked)
- Modified files include `pisignage/seed_playlists.py`, `pisignage/templates/layouts/custom_layout.html`, `src/tsv6/core/production_main.py`, `src/tsv6/display/pisignage_adapter.py`, `src/tsv6/display/playlist_manager.py`, `src/tsv6/display/tsv6_player/backend.py`, `tsv6-signage.service`
- Untracked files: `src/tsv6/display/qr_overlay.py`, `tests/unit/test_qr_overlay.py`

If counts differ — STOP and report to user before continuing.

- [ ] **Step 2: Verify tsmedia-with-player is on master with one expected modified file**

Run:
```bash
cd /home/g1tech/tsmedia-with-player && \
  git rev-parse --abbrev-ref HEAD && \
  git status --porcelain
```

Expected:
- Branch: `master`
- Output: ` M src/tsv6/display/playlist_manager.py` (and nothing else)

If state differs — STOP and report.

- [ ] **Step 3: Verify origin URLs match across all three trees**

Run:
```bash
for d in /home/g1tech/tsmedia-with-player /home/g1tech/tsrpi7/tsrpi5 /home/g1tech/tsrpi7/tsrpi5-v2; do
  echo "=== $d ===" && git -C "$d" remote get-url origin
done
```

Expected: all three print `https://github.com/genesis1tech/tsmedia-with-player.git`.

- [ ] **Step 4: Confirm gh CLI is authenticated for the repo**

Run: `gh auth status`
Expected: shows authenticated as `genesis1tech` (or a member with push access).

If not authenticated — STOP and ask the user to run `gh auth login`.

---

### Task 2: Commit A — qr_overlay module + tests

**Files:**
- Add: `/home/g1tech/tsrpi7/tsrpi5-v2/src/tsv6/display/qr_overlay.py` (new, 126 lines)
- Add: `/home/g1tech/tsrpi7/tsrpi5-v2/tests/unit/test_qr_overlay.py` (new, 51 lines)

- [ ] **Step 1: Stage only the qr_overlay pair**

Run:
```bash
cd /home/g1tech/tsrpi7/tsrpi5-v2 && \
  git add src/tsv6/display/qr_overlay.py tests/unit/test_qr_overlay.py
```

- [ ] **Step 2: Verify the staged set is exactly those two files**

Run: `git diff --cached --name-only`
Expected output:
```
src/tsv6/display/qr_overlay.py
tests/unit/test_qr_overlay.py
```

- [ ] **Step 3: Commit**

Run:
```bash
git commit -m "$(cat <<'EOF'
feat(display): add QrOverlay window for product-stage QR display

Pi-side always-on-top Tk overlay that renders a QR code over the PiSignage
kiosk during the product display stage. Bounded duration with auto-hide,
thread-safe show/hide, position presets, env-tunable size.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Verify commit landed**

Run: `git log -1 --stat`
Expected: shows the new commit on `feat/barcode-repo-lookup-v2` with 2 files added.

---

### Task 3: Commit B — post-spec follow-ups (with service-file editorial step)

**Files (modified, in `/home/g1tech/tsrpi7/tsrpi5-v2/`):**
- `pisignage/seed_playlists.py` — adds `--schedule-only` CLI flag
- `pisignage/templates/layouts/custom_layout.html` — let PiSignage native ticker JS drive `#ticker`
- `src/tsv6/core/production_main.py` — wire QrOverlay, idle timer, deposit/product playlist overrides
- `src/tsv6/display/pisignage_adapter.py` — `ticker` param on `update_playlist_content`, new `update_group_schedule`
- `src/tsv6/display/playlist_manager.py` — `SCHEDULE_DEFINITIONS`, `schedule_playlists()`, multi-extension media support, per-playlist duration/ticker overrides
- `src/tsv6/display/tsv6_player/backend.py` — gate cache writes on non-empty assets, drop transient setplaylist handlers, manifest-empty fallback, accept PiSignage dict asset format
- `tsv6-signage.service` — **mixed change, see Step 2**

- [ ] **Step 1: Stage the 6 clean modified files (exclude tsv6-signage.service)**

Run:
```bash
cd /home/g1tech/tsrpi7/tsrpi5-v2 && \
  git add pisignage/seed_playlists.py \
          pisignage/templates/layouts/custom_layout.html \
          src/tsv6/core/production_main.py \
          src/tsv6/display/pisignage_adapter.py \
          src/tsv6/display/playlist_manager.py \
          src/tsv6/display/tsv6_player/backend.py
```

- [ ] **Step 2: Triage `tsv6-signage.service` — revert local path overrides in the working tree, then stage**

The working-tree diff has two kinds of changes mixed together:
1. **Path overrides (LOCAL-ONLY, do NOT commit):**
   - `WorkingDirectory=/home/g1tech/tsrpi7/tsrpi5` → `tsrpi7/tsrpi5-v2`
   - `Environment="PYTHONPATH=/home/g1tech/tsrpi7/tsrpi5/src"` → `tsrpi5-v2/src`
2. **Real config changes (KEEP, commit):**
   - `PISIGNAGE_SERVER_URL` test→prod (`http://72.60.120.25:3000` → `https://tsmedia.g1tech.cloud`)
   - `PISIGNAGE_GROUP=Test Group` → `First Group`

Approach: revert just the path lines in the working tree using `Edit` (not interactive git), then `git add` the whole file.

Use the Edit tool twice on `/home/g1tech/tsrpi7/tsrpi5-v2/tsv6-signage.service`:

Edit 1:
- `old_string`: `WorkingDirectory=/home/g1tech/tsrpi7/tsrpi5-v2`
- `new_string`: `WorkingDirectory=/home/g1tech/tsrpi7/tsrpi5`

Edit 2:
- `old_string`: `Environment="PYTHONPATH=/home/g1tech/tsrpi7/tsrpi5-v2/src"`
- `new_string`: `Environment="PYTHONPATH=/home/g1tech/tsrpi7/tsrpi5/src"`

(Both old strings should be unique in the file. If Edit reports they're not unique, fall back to `Read` the file first to get fuller context, then re-Edit with more surrounding lines.)

Then stage:
```bash
git add tsv6-signage.service
```

Verify the cached diff shows only config changes:
```bash
git diff --cached tsv6-signage.service
```

Expected: cached diff shows only the `PISIGNAGE_SERVER_URL` and `PISIGNAGE_GROUP` lines changed. `WorkingDirectory` and `PYTHONPATH` lines should NOT appear in the cached diff.

If `WorkingDirectory` or `PYTHONPATH` does appear in the cached diff — STOP. The reverts didn't take. Unstage with `git restore --staged tsv6-signage.service`, re-Read the file, and try again.

- [ ] **Step 3: Verify the full staged set**

Run: `git diff --cached --name-only`
Expected output (7 files, in any order):
```
pisignage/seed_playlists.py
pisignage/templates/layouts/custom_layout.html
src/tsv6/core/production_main.py
src/tsv6/display/pisignage_adapter.py
src/tsv6/display/playlist_manager.py
src/tsv6/display/tsv6_player/backend.py
tsv6-signage.service
```

- [ ] **Step 4: Commit**

Run:
```bash
git commit -m "$(cat <<'EOF'
feat(v2): post-spec follow-ups — group scheduling, playlist overrides, native-player robustness

- pisignage_adapter: ticker passthrough on update_playlist_content; add
  update_group_schedule for player-group playlist deployment
- playlist_manager: SCHEDULE_DEFINITIONS + schedule_playlists(); idle loop
  accepts mp4/jpg/jpeg/png/webp; per-playlist duration/ticker overrides
- seed_playlists: --schedule-only flag for re-applying group schedules
  without touching assets or playlists
- production_main: QrOverlay wired into product stage; idle return timer;
  honor depositPlaylist/productPlaylist/noItemPlaylist overrides from cloud
- tsv6_player/backend: skip empty-asset cache writes; drop transient
  setplaylist handlers (now app-driven only); fall back to all cached MP4s
  when idle manifest is missing/empty; accept PiSignage dict asset format
- custom_layout: let PiSignage native ticker JS own #ticker, brand-style
  fallback content
- tsv6-signage.service: PiSignage server URL → prod, group → First Group

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 5: Verify**

Run: `git log -2 --oneline`
Expected: two new commits at tip on `feat/barcode-repo-lookup-v2` (qr_overlay + post-spec follow-ups), plus the previous tip `fff4f1d docs(v2): mark spec implemented + brand_playlists ops note` as third.

- [ ] **Step 6: Confirm working tree is clean except for the local-only path lines**

Run: `git status`
Expected: `tsv6-signage.service` still shows as modified (the WorkingDirectory/PYTHONPATH lines we deliberately did not commit). Nothing else dirty.

---

### Task 4: Push feat/barcode-repo-lookup-v2

- [ ] **Step 1: Push**

Run:
```bash
cd /home/g1tech/tsrpi7/tsrpi5-v2 && git push origin feat/barcode-repo-lookup-v2
```

Expected: push succeeds, two new commits transferred.

- [ ] **Step 2: Verify origin is in sync**

Run: `git log origin/feat/barcode-repo-lookup-v2 -3 --oneline`
Expected: shows the same three top commits as local.

---

### Task 5: Sync tsmedia-with-player clone with the new branch

- [ ] **Step 1: Fetch in the canonical clone**

Run:
```bash
cd /home/g1tech/tsmedia-with-player && git fetch origin
```

Expected: prints "From github.com:genesis1tech/tsmedia-with-player" with two new commits on `feat/barcode-repo-lookup-v2`.

- [ ] **Step 2: Confirm visibility**

Run: `git log origin/feat/barcode-repo-lookup-v2 -3 --oneline`
Expected: same three commits as Task 4 Step 2.

---

### Task 6: Manifest-upload feature branch + PR

**Files (modified, in `/home/g1tech/tsmedia-with-player/`):**
- `src/tsv6/display/playlist_manager.py` — adds `generate_and_upload_playlist_manifests` + `seed_all` integration (77 lines)

- [ ] **Step 1: Branch off master**

Run:
```bash
cd /home/g1tech/tsmedia-with-player && \
  git checkout -b feat/playlist-manifest-upload
```

Expected: switched to new branch, working-tree change preserved.

- [ ] **Step 2: Stage and verify**

Run:
```bash
git add src/tsv6/display/playlist_manager.py && \
  git diff --cached --stat
```

Expected: `1 file changed, 77 insertions(+)`.

- [ ] **Step 3: Commit**

Run:
```bash
git commit -m "$(cat <<'EOF'
feat(display): generate and upload __tsv6_*.json playlist manifests

Adds PlaylistManager.generate_and_upload_playlist_manifests, called from
seed_all, which writes a JSON manifest per TSV6 playlist (asset filename
list) and uploads it to PiSignage as a regular asset. The native player
backend treats __tsv6_*.json as its playlist cache target on next asset
sync, so the player ends up with valid manifest files instead of having to
infer assets from the cache directory.

For tsv6_idle_loop the manifest contains the actual MP4 filenames; all
other (state-driven) playlists get an empty list, which the backend handles
as "no manifest update needed" after a related fix on the player side.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>
EOF
)"
```

- [ ] **Step 4: Push with upstream tracking**

Run: `git push -u origin feat/playlist-manifest-upload`
Expected: push succeeds, branch tracks `origin/feat/playlist-manifest-upload`.

- [ ] **Step 5: Open PR**

Run:
```bash
gh pr create \
  --base master \
  --head feat/playlist-manifest-upload \
  --title "feat(display): generate and upload __tsv6_*.json playlist manifests" \
  --body "$(cat <<'EOF'
## Summary
- New `PlaylistManager.generate_and_upload_playlist_manifests`, invoked from `seed_all`, that writes a JSON manifest per TSV6 playlist (asset filename list) and uploads it to PiSignage as a regular asset.
- Native player picks up `__tsv6_*.json` on next asset sync as its playlist cache target — no more inferring assets from the cache directory.
- `tsv6_idle_loop` manifest contains the actual MP4 filenames; state-driven playlists get an empty list (handled as "no manifest update needed" on the player side).

## Test plan
- [ ] Run `seed_playlists.py` against staging; confirm `__tsv6_idle_loop.json` etc. appear under PiSignage assets.
- [ ] Trigger an asset sync on the player; confirm `__tsv6_*.json` files land in the cache dir with the correct contents.
- [ ] Restart player; confirm idle loop plays only the manifested MP4s (no stray cache fallback).

🤖 Generated with [Claude Code](https://claude.com/claude-code)
EOF
)"
```

Expected: prints the new PR URL.

- [ ] **Step 6: Verify PR is open**

Run: `gh pr view feat/playlist-manifest-upload --json state,url,title`
Expected: `state: OPEN`, URL printed.

- [ ] **Step 7: Return clone to a clean state on master**

Run:
```bash
git checkout master && git status
```

Expected: branch `master`, working tree clean.

---

### Task 7: Update auto-memory

**Files:**
- Create: `/home/g1tech/.claude/projects/-home-g1tech/memory/project_tsmedia_layout.md`
- Create: `/home/g1tech/.claude/projects/-home-g1tech/memory/project_tsmedia_sim7600_diag.md`
- Modify: `/home/g1tech/.claude/projects/-home-g1tech/memory/MEMORY.md`
- Delete: `/home/g1tech/.claude/projects/-home-g1tech/memory/project_tsrpi7_layout.md`
- Delete: `/home/g1tech/.claude/projects/-home-g1tech/memory/project_tsrpi7_sim7600_diag.md`

- [ ] **Step 1: Read current memory contents**

Run:
```bash
cat /home/g1tech/.claude/projects/-home-g1tech/memory/MEMORY.md
echo "---"
cat /home/g1tech/.claude/projects/-home-g1tech/memory/project_tsrpi7_layout.md
echo "---"
cat /home/g1tech/.claude/projects/-home-g1tech/memory/project_tsrpi7_sim7600_diag.md
```

(This is so you write replacements with the same level of detail and frontmatter style.)

- [ ] **Step 2: Write `project_tsmedia_layout.md`**

Use the Write tool. Path: `/home/g1tech/.claude/projects/-home-g1tech/memory/project_tsmedia_layout.md`

Content (frontmatter + body):
```markdown
---
name: tsmedia-with-player project layout
description: device code lives at /home/g1tech/tsmedia-with-player, cloned from genesis1tech/tsmedia-with-player (renamed from tsrpi7 in 2026-04)
type: project
---

The canonical local clone is `/home/g1tech/tsmedia-with-player`, tracking
`https://github.com/genesis1tech/tsmedia-with-player.git` (renamed from
`tsrpi7` in April 2026; old `~/tsrpi7/` worktrees were retired on 2026-04-26).

Key paths inside the clone:
- `src/tsv6/` — Python application code (display, core, utils, …)
- `pisignage/` — server-seed scripts and Chromium kiosk templates
- `tests/unit/` — pytest suite
- `tsv6-signage.service` and siblings — systemd units; **note** the committed
  WorkingDirectory still points at the old `~/tsrpi7/tsrpi5` path; needs a
  follow-up edit to point at `~/tsmedia-with-player` before redeploying.

Convention: changes land via feature branch + PR (see merge history). master
is protected by review.

**Why:** The repo was renamed and three local trees (1 separate clone + 2
worktrees) were consolidated. Future work should happen exclusively in the
canonical clone path above.

**How to apply:** When asked about "the device repo" or "tsrpi7" or "tsrpi5",
read/write under `/home/g1tech/tsmedia-with-player`. Treat any reference to
`~/tsrpi7/` as historical.
```

- [ ] **Step 3: Write `project_tsmedia_sim7600_diag.md`**

Use the Write tool. Path: `/home/g1tech/.claude/projects/-home-g1tech/memory/project_tsmedia_sim7600_diag.md`

Open `/home/g1tech/.claude/projects/-home-g1tech/memory/project_tsrpi7_sim7600_diag.md` first; copy its body verbatim into the new file but rewrite every occurrence of `/home/g1tech/tsrpi7/tsrpi5` to `/home/g1tech/tsmedia-with-player`. Update the frontmatter `name` and `description` to drop the `tsrpi7` prefix and reflect the new path. Keep all the SIM7600 / Hologram diagnostic content intact (port mapping, AT command sequence, antenna gotcha, CEREG state).

- [ ] **Step 4: Update MEMORY.md index**

Read the file first. For the two existing lines:
```
- [tsrpi7 project layout](project_tsrpi7_layout.md) — device code lives at /home/g1tech/tsrpi7/tsrpi5, cloned from genesis1tech/tsrpi5
- [tsrpi7 SIM7600 diagnosis + port recipe](project_tsrpi7_sim7600_diag.md) — AT port is ttyUSB3 (USB if 04), udev symlink /dev/ttySIM7600, Hologram LTE denied by network (CEREG 0,3)
```

Replace with:
```
- [tsmedia-with-player project layout](project_tsmedia_layout.md) — device code at /home/g1tech/tsmedia-with-player; renamed from tsrpi7/tsrpi5 in 2026-04
- [tsmedia SIM7600 diagnosis + port recipe](project_tsmedia_sim7600_diag.md) — AT port is ttyUSB3 (USB if 04), udev symlink /dev/ttySIM7600, Hologram LTE denied by network (CEREG 0,3)
```

Use the Edit tool with `replace_all=false` and the old block as `old_string`.

- [ ] **Step 5: Delete the old memory files**

Run:
```bash
rm /home/g1tech/.claude/projects/-home-g1tech/memory/project_tsrpi7_layout.md \
   /home/g1tech/.claude/projects/-home-g1tech/memory/project_tsrpi7_sim7600_diag.md
```

- [ ] **Step 6: Verify**

Run:
```bash
ls /home/g1tech/.claude/projects/-home-g1tech/memory/ && \
  grep tsrpi7 /home/g1tech/.claude/projects/-home-g1tech/memory/MEMORY.md
```

Expected:
- New files `project_tsmedia_layout.md` and `project_tsmedia_sim7600_diag.md` are listed.
- Old `project_tsrpi7_*.md` files are absent.
- `grep tsrpi7 MEMORY.md` returns no output (exit code 1).

---

### Task 8: Final verification + handoff

- [ ] **Step 1: Confirm origin has both new branches with the new commits**

Run:
```bash
gh pr list --repo genesis1tech/tsmedia-with-player --state open --limit 10
```

Expected: shows the open `feat/playlist-manifest-upload` PR.

Run:
```bash
git -C /home/g1tech/tsmedia-with-player log origin/feat/barcode-repo-lookup-v2 -3 --oneline
```

Expected: top of branch is the post-spec follow-ups commit, second is qr_overlay, third is `fff4f1d docs(v2): mark spec implemented + brand_playlists ops note`.

- [ ] **Step 2: Confirm canonical clone is clean and on master**

Run:
```bash
cd /home/g1tech/tsmedia-with-player && \
  git rev-parse --abbrev-ref HEAD && \
  git status
```

Expected: branch `master`, "nothing to commit, working tree clean".

- [ ] **Step 3: Print the handoff message**

Tell the user, verbatim:

> Migration complete. All work is preserved on origin:
> - `feat/barcode-repo-lookup-v2`: 2 new commits (qr_overlay module, post-spec follow-ups). Branch was already pushed; not yet merged.
> - `feat/playlist-manifest-upload`: new branch + PR opened (link above).
>
> The `/home/g1tech/tsrpi7/tsrpi5-v2` working tree still has the deliberately-uncommitted local path overrides in `tsv6-signage.service` (WorkingDirectory + PYTHONPATH pointing at `tsrpi5-v2`). That dirt is expected and will go away when you delete the directory.
>
> Recommended manual cleanup, when you're ready:
> ```
> cd /home/g1tech/tsrpi7/tsrpi5 && git worktree remove ../tsrpi5-v2
> rm -rf /home/g1tech/tsrpi7/
> ```
> The `git worktree remove` will refuse to delete if it sees unexpected dirty state (a final safety check). The `rm -rf` is yours to run when you're satisfied.
>
> Auto-memory has been updated to point at `/home/g1tech/tsmedia-with-player`.
>
> Note for follow-up (out of scope here): the committed `tsv6-signage.service` still has `WorkingDirectory=/home/g1tech/tsrpi7/tsrpi5` from before the rename. After you delete `~/tsrpi7/`, that path won't exist — you'll want a small PR to repoint it at `/home/g1tech/tsmedia-with-player` before the next deploy.

---

## Out of scope

- Fixing the committed `tsv6-signage.service` WorkingDirectory (pre-existing tech debt, flagged in handoff).
- Reviewing or merging the two open feature branches (`feat/barcode-repo-lookup-v2`, `feat/playlist-manifest-upload`).
- Cleanup of stale local branches in the canonical clone (e.g. `minor/pisignage-hostinger-integration` shows `[gone]`).
- Actual deletion of `/home/g1tech/tsrpi7/` (user runs manually).

## Self-review notes

- Spec coverage: every numbered step in the spec maps to a task here (Step 1 → Tasks 2–4, Step 2 → Task 6, Step 3 → no-op as agreed, Step 4 → Task 8 handoff, Step 5 → Task 7).
- New decision baked in: service-file editorial split in Task 3 Step 2 (strip path-only lines, keep config). User was warned in conversation; will see again in the plan.
- No placeholders. All commit messages, gh commands, expected outputs are concrete.
- Type/method consistency: I refer to `generate_and_upload_playlist_manifests`, `schedule_playlists`, `update_group_schedule`, `QrOverlay` — all match the actual code in the diffs.
