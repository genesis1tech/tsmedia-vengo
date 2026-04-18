# tsv6.ads — Device Ad Player

Ad playback module for Topper Stopper Pi 5 devices.
Fills idle time between recycling events with server-delivered video ads,
reports signed proof-of-play, and queues events locally when offline.

## Architecture

```
ProductionVideoPlayer  (Tk main thread)
  └── AdPlayer              ← start() / stop() / on_recycling_state_change()
        ├── asyncio loop    (dedicated daemon thread)
        │     ├── AdScheduler    — pre-fetches next pod 60 s ahead
        │     ├── AssetCache     — LRU disk cache, 2 GB limit
        │     └── ImpressionReporter — SQLite queue, 30 s flush
        └── PlayerBridge    ← dispatches all VLC calls via root.after()
```

The ad player runs its own `asyncio` event loop in a background daemon thread.
All VLC mutations go through `root.after(0, ...)` to stay on the Tk main thread.
The recycling state machine in `production_main.py` never blocks ad code;
calls to `on_recycling_state_change()` are fire-and-forget.

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `TSV6_AD_ENABLED` | `0` | Set to `1` to activate ad playback |
| `TSV6_AD_ENDPOINT` | _(empty)_ | Base URL of ts-ssp API (e.g. `https://api.tsssp.com`) |
| `TSV6_AD_NETWORK_ID` | `topperstopper` | Network ID sent in every ad request |
| `TSV6_AD_API_KEY` | _(empty)_ | Value of `X-Device-Key` header |
| `TSV6_AD_CACHE_DIR` | `/var/lib/tsv6/ads` | Local asset cache directory |
| `TSV6_AD_CACHE_MAX_BYTES` | `2000000000` | Maximum cache size (2 GB) |
| `TSV6_AD_OFFLINE_DB` | `/var/lib/tsv6/ads/impressions.db` | SQLite queue path |
| `TSV6_AD_OFFLINE_MAX_ROWS` | `10000` | Maximum queued impressions |
| `TSV6_AD_PREFETCH_LEAD_SECONDS` | `60` | Seconds before ad end to fetch the next pod |
| `TSV6_AD_DISPLAY_WIDTH` | `1280` | Display width sent to server |
| `TSV6_AD_DISPLAY_HEIGHT` | `800` | Display height sent to server |
| `TSV6_AD_MIN_DURATION` | `10` | Minimum creative duration (seconds) |
| `TSV6_AD_MAX_DURATION` | `30` | Maximum creative duration (seconds) |

All variables default to safe no-op values. The device boots and operates
normally when `TSV6_AD_ENABLED=0`.

## File Layout

```
src/tsv6/ads/
├── __init__.py       — exports AdPlayer
├── config.py         — AdConfig dataclass (from_env())
├── state.py          — AdPlayerState enum (for heartbeat shadow)
├── client.py         — async httpx wrapper, X-Device-Key auth, tenacity retry
├── cache.py          — AssetCache: SHA-256 filename, LRU eviction
├── scheduler.py      — AdScheduler: prefetch deque, wake-up timing
├── player_bridge.py  — PlayerBridge: root.after() VLC dispatch
├── reporter.py       — ImpressionReporter: aiosqlite queue, 30 s flush
└── player.py         — AdPlayer: top-level orchestrator
```

## State Machine

```
DISABLED ──(TSV6_AD_ENABLED=1)──► IDLE
  IDLE ──(next_ad available)──► PREFETCHING
  PREFETCHING ──(asset cached)──► PLAYING
  PLAYING ──(playback ends)──► REPORTING
  PLAYING ──(recycling starts)──► IDLE  [expiration queued if < 50% played]
  REPORTING ──(flush done)──► IDLE
```

## Playback Contention

The recycling state machine always takes precedence.

When `ProductionVideoPlayer._on_product_image_display()` is called
(a barcode was scanned and the door is about to open), it calls
`ad_player.on_recycling_state_change(is_recycling=True)`.
`PlayerBridge.preempt()` schedules `list_player.pause()` on the Tk thread
and enqueues an expiration event if less than 50% of the ad has played.

When `_handle_recycle_success()` or `_handle_recycle_failure()` completes,
`ad_player.on_recycling_state_change(is_recycling=False)` is called and
the ad loop resumes from the next queued item.

## Offline Queue

- SQLite database at `TSV6_AD_OFFLINE_DB` (survives process restart).
- `UNIQUE (play_id, event_type)` index ensures idempotent enqueue.
- Bounded at `TSV6_AD_OFFLINE_MAX_ROWS` rows; oldest row is dropped when full.
- Background task flushes in batches of 50 every 30 seconds.
- Server tolerates `received_at - played_at` up to 72 h before marking
  `valid=false` in the analytics hypertable.

## Enabling Ads (Quick Start)

1. Edit `/home/<user>/tsrpi5/tsv6.service` (or a per-device override):

```ini
Environment="TSV6_AD_ENABLED=1"
Environment="TSV6_AD_ENDPOINT=https://api.tsssp.com"
Environment="TSV6_AD_API_KEY=<device-api-key>"
```

2. Reload:

```bash
sudo systemctl daemon-reload
sudo systemctl restart tsv6@<user>.service
```

3. Verify in logs:

```bash
journalctl -u tsv6@<user>.service -f | grep -i "ad player"
```

Expected output: `Ad player started (endpoint=https://api.tsssp.com)`

## Troubleshooting

| Symptom | Check |
|---|---|
| No ads playing | Verify `TSV6_AD_ENABLED=1` and `TSV6_AD_ENDPOINT` is set |
| `AdPlayer: TSV6_AD_ENDPOINT not configured` log | Set `TSV6_AD_ENDPOINT` env var |
| Cache fills disk | Lower `TSV6_AD_CACHE_MAX_BYTES` or clear `/var/lib/tsv6/ads/` |
| Impressions not flushing | Check connectivity; queue drains on reconnect |
| `PermissionError` on `/var/lib/tsv6/ads` | Run `first-boot.sh` or manually `sudo chown <user> /var/lib/tsv6/ads` |
| Recycling broken | Check `TSV6_AD_ENABLED=0` to rule out ad player; it should be a no-op when disabled |

## Running Tests

```bash
# From tsrpi5/ root
uv run pytest tests/test_ads/ -v
```

Dependencies used in tests: `respx`, `aiosqlite`, `pytest-asyncio`.
