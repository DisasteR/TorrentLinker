# Native download-queue performance, 2026-10-31

Workstation continuation for [#49](https://github.com/Vietnoirien/TorrentLinker/issues/49).
The baseline uses archived source at `9cdd729`; the final measurements use the queue
changes committed with this report. [Raw measurements](queue-performance-2026-10-31.json).

## Reproduction and method

`scripts/benchmark_queue.py` creates a temporary SQLite queue with synthetic c411
metadata. It never contacts an Indexer or Torrent Client. Each size runs in a fresh
process with isolated Qt preferences and the offscreen platform, using Python 3.13.11
and Qt 6.12.0 on the same Linux workstation as the [Torrents baseline](torrents-performance.md).

The first-use measurement includes SearchTab construction and its first display.
Polling and estimate timings are medians of three operations. Selection chooses one
exact queue key; each subsequent status update must preserve it. Every measurement
includes a 20 ms event-loop drain so heartbeat delays include the final GUI stall.
Numbers near 20 ms therefore mostly measure that drain, not application work.

| Queue entries | First usable, before → after | Idle poll, before → after | One status change, before → after | Selection, before → after |
| --- | --- | --- | --- | --- |
| 1,000 | 339 → 304 ms | 33 → 21 ms | 171 → 40 ms | 42 → 21 ms |
| 12,000 | 3,519 → 2,841 ms | 217 → 21 ms | 2,176 → 361 ms | 384 → 22 ms |
| 50,000 | 16,678 → 12,841 ms | 1,356 → 21 ms | 10,310 → 1,705 ms | 1,995 → 26 ms |

Peak RSS was 154 → 153 MiB, 288 → 282 MiB and 749 → 709 MiB respectively.
Estimate refreshes remain expensive: final medians were 55 ms, 436 ms and 1,804 ms.

## Changes

A selection change previously decoded the full queue twice just to enable buttons.
Buttons now use the displayed snapshot. Action handlers retain their persisted-state
checks; this does not authorize or dispatch anything from cached UI state.

A status-only poll previously decoded the queue four times and recreated every table
cell and estimate. It now loads the changed snapshot once and updates status, reason
and client-label cells in place. Metadata, row membership/order and destination changes
still use the full refresh path. Status sorting and exact selection survive updates,
and retry buttons follow the new `retry_safe` value.

Unchanged queues previously incurred a full read and JSON decode every second while
the view was visible. A lightweight Store change token now combines connection identity,
client scope, own-connection changes and commits from other connections. The token is
captured before reading rows, so a concurrent write causes another poll instead of
being marked already displayed. This follows SQLite's distinction between
[`PRAGMA data_version`](https://www.sqlite.org/pragma.html#pragma_data_version), which
observes other connections' commits, and
[`Connection.total_changes`](https://docs.python.org/3/library/sqlite3.html#sqlite3.Connection.total_changes),
which counts changes on the current connection. Writes to unrelated tables may cause
an extra queue read; they cannot suppress a required one.

## Validation and remaining work

The new regressions first failed on repeated store reads and unrelated cell updates.
Eighty queue, Search, destination, Zen and browser queue-action tests then passed.
Coverage includes external writers, same-connection writes, client switches, replacement
databases, writes arriving during a displayed snapshot, status sorting, retry safety,
exact removal scope and unchanged idle polls. Lint, formatting and typechecking passed.

Before this queue change, the full local suite passed 1,874 tests with 148 optional
skips, and [CI for `9cdd729`](https://github.com/Vietnoirien/TorrentLinker/actions/runs/41185543502)
passed every job. Those results validate the preceding assistant shutdown fix; they
are not a full-suite result for this queue patch.

The 50,000-entry queue remains slow on first display, full rebuilds and estimate
updates. A single status update still stalls for over a second at that size. Moving
large reads off the GUI thread and avoiding per-cell table construction need further
work. The table above measures queue polling, not end-to-end dispatch. Native progress
was measured separately in the follow-up below. Chat-history and cancellation
baselines, and installed Windows measurements, also remain open. These workstation
numbers are observations, not universal CI timing limits or a completed #49 audit.

## Native progress and Pause follow-up

A regression against `55e83f9` confirmed that native send notifications still replaced
all queue rows, including unchanged retry-wait notifications. Native progress and
completion now use the same database-change and status-only refresh logic as polling.
They update hidden pages too. Completion and worker failure refresh buttons even when
the last progress event already displayed the database change.

At 12,000 entries, the median status-changing native notification fell from 1,580 to
274 ms. An unchanged retry-wait notification fell from 1,537 to 21 ms, again mostly the
20 ms event-loop drain. Both measurements use three notifications in the same isolated
benchmark as above. [Raw native-progress measurements](queue-native-progress-2026-10-31.json).
Successful sends now remove their rows in place instead of rebuilding the table. At
12,000 entries, the poll after a successful send fell from about 1,450 to 350 ms,
the same cost as a status change (`sent_progress` in `scripts/benchmark_queue.py`,
2026-11-01). Adding entries still rebuilds the table. These measurements do not include an Indexer request or a Torrent Client transfer.

The native worker test starts a controlled two-minute rate-limit wait, clicks Pause,
and requires the worker to finish within two seconds. The item remains queued, the
exact selection survives, and Resume sends it once. A worker-failure regression checks
that interrupted sends remain uncertain and cannot be retried automatically. The final
focused suite passes 83 tests; lint, formatting and typechecking also pass.
