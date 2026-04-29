# tsrpi7 → tsmedia-with-player Consolidation

**Date:** 2026-04-26
**Type:** Local-environment refactor / migration
**Owner:** genesis1.tech.us@gmail.com

## Background

The GitHub repo was renamed `tsrpi7` → `tsmedia-with-player`. Locally, three working copies of the same remote (`https://github.com/genesis1tech/tsmedia-with-player.git`) coexist:

| Path | Type | Branch | Uncommitted |
|---|---|---|---|
| `/home/g1tech/tsmedia-with-player/` | separate clone (canonical going forward) | `master` | 77 lines added to `src/tsv6/display/playlist_manager.py` (manifest upload feature) |
| `/home/g1tech/tsrpi7/tsrpi5/` | git worktree (shared `.git` with sibling) | `master` | none real; 3 untracked scratch scripts under `scripts/` |
| `/home/g1tech/tsrpi7/tsrpi5-v2/` | git worktree (shared `.git` with sibling) | `feat/barcode-repo-lookup-v2` | 7 modified files (+267/-54) + 2 new files (`qr_overlay.py`, `test_qr_overlay.py`, 177 lines) |

`feat/barcode-repo-lookup-v2` is already pushed to origin and **not yet merged**. Only working-tree changes are at risk.

## Goal

1. Preserve all uncommitted work in proper commits on origin.
2. Leave `/home/g1tech/tsmedia-with-player/` as the sole local working copy.
3. User will manually `rm -rf /home/g1tech/tsrpi7/` after migration is verified.
4. Update auto-memory entries to repoint at `tsmedia-with-player`.

## Plan

### Step 1 — Preserve `tsrpi5-v2` work (2 commits on existing branch)

Working dir: `/home/g1tech/tsrpi7/tsrpi5-v2/` (already on `feat/barcode-repo-lookup-v2`).

Read each diff first; refine commit messages to reflect actual content. Default split:

- **Commit A (qr_overlay)** — adds `src/tsv6/display/qr_overlay.py` + `tests/unit/test_qr_overlay.py` only.
- **Commit B (post-spec follow-ups)** — the 7 modified files: `pisignage/seed_playlists.py`, `pisignage/templates/layouts/custom_layout.html`, `src/tsv6/core/production_main.py`, `src/tsv6/display/pisignage_adapter.py`, `src/tsv6/display/playlist_manager.py`, `src/tsv6/display/tsv6_player/backend.py`, `tsv6-signage.service`.

Push: `git push origin feat/barcode-repo-lookup-v2`.

### Step 2 — Preserve `tsmedia-with-player/master` manifest work (PR-ready branch)

Working dir: `/home/g1tech/tsmedia-with-player/`.

1. `git checkout -b feat/playlist-manifest-upload` off `master`.
2. Commit the 77-line `playlist_manager.py` change as `feat(display): generate and upload __tsv6_*.json playlist manifests`.
3. `git push -u origin feat/playlist-manifest-upload`.
4. `gh pr create` — title and body summarizing the manifest seeding behavior, matching the repo's existing PR style.
5. `git fetch origin` so this clone now has the v2 commits from Step 1.

### Step 3 — Drop the 3 untracked `scripts/` files

`scripts/scan_publish_open.py`, `scripts/scan_then_move.py`, `scripts/servo_back_and_forth.py` live only in `tsrpi5/` and will die with the directory. No action.

### Step 4 — Hand off `/home/g1tech/tsrpi7/` for manual deletion

**Do not delete.** When all of Step 1 and Step 2 are pushed and verified:

1. Run `git fetch origin` in `tsmedia-with-player/` and confirm `feat/barcode-repo-lookup-v2` is at the new tip.
2. Report back to user with the safe-to-delete confirmation and the recommended command:
   ```
   cd /home/g1tech/tsrpi7/tsrpi5 && git worktree remove ../tsrpi5-v2
   rm -rf /home/g1tech/tsrpi7/
   ```
   The worktree-remove step is a final dirty-state check; the `rm -rf` is the user's call to run.

### Step 5 — Update auto-memory

Two memory files reference `/home/g1tech/tsrpi7/tsrpi5`:

- `~/.claude/projects/-home-g1tech/memory/project_tsrpi7_layout.md` → rename concept to `project_tsmedia_layout.md`, repoint paths and repo name.
- `~/.claude/projects/-home-g1tech/memory/project_tsrpi7_sim7600_diag.md` → rename to `project_tsmedia_sim7600_diag.md`, repoint paths.
- Update `MEMORY.md` index lines for both.

Old files: delete after the new ones are in place.

## Verification

- `git log --oneline -5` on `feat/barcode-repo-lookup-v2` shows the 2 new commits at tip and matches origin.
- `gh pr view` for `feat/playlist-manifest-upload` returns an open PR.
- `git status` in `tsmedia-with-player/` is clean.
- Memory files in step 5 are present, old ones removed, `MEMORY.md` index has no `tsrpi7` paths left.

## Out of scope

- Merging or reviewing `feat/barcode-repo-lookup-v2` and `feat/playlist-manifest-upload` PRs (user decides).
- Cleanup of stale local branches in `tsmedia-with-player/` (e.g. `minor/pisignage-hostinger-integration` shows `[gone]`). Separate task.
- Any code changes beyond preserving the existing uncommitted edits verbatim.
