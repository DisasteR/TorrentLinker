# Techniques from TorrentMatch

Source review of [TorrentMatch](https://git.mma.dev/TheGeeKing/TorrentMatch) at `69f7369` on 2026-11-05. It is a Go desktop and web app that matches local files with c411 torrents and seeds them through qBittorrent 5.2+. This is a reading of its code, not a run of it. Its own `docs/torrentlinker-research.md` reviews TorrentLinker 0.11.4 and flags our main gap (finding 2 below).

What we already share: the Zen Lab snapshot as a catalogue, exact per-file size pairing, renaming files inside the client instead of hardlinking, qBittorrent API keys, client recheck as the only gate to seeding, and remembered rejections.

## Worth adopting

### 1. Seed Partial Matches instead of leaving them stopped

We set the absent files' priority to 0, recheck, then leave a successful partial stopped with `torrentlinker:partial` (`reseed._accept`). A piece that straddles a present file and a missing one would otherwise download into the user's library.

TorrentMatch sets **every** file to priority 0, present ones included, reads the priorities back until all report 0, and only then starts the torrent (`internal/integrations/qbit_priorities.go`). No piece is wanted, so nothing downloads, and qBittorrent uploads the verified pieces. If it can't confirm the read-back, the torrent stays stopped. It also never uses a speed limit of 0, because qBittorrent reads 0 as unlimited.

For us, partials would earn upload instead of sitting idle. It's qBittorrent-first. Transmission (`files-unwanted`) and Deluge (file priorities) look able to do the same, but that needs checking. rTorrent and µTorrent already refuse partials. We'd need a test that a started all-skipped torrent stays at 0 bytes downloaded.

### 2. File-level reverse lookup over cached torrent layouts

We find catalogue candidates by an item's **total** size (`store.catalogue_sized(item.size)`) and fetch a `.torrent` only within 2% of that total. So a single episode never leads to the season pack that contains it.

TorrentMatch indexes every `.torrent` it has ever fetched in `layout-index.sqlite`: a `files(hash, ordinal, path, size)` table indexed on `size`. A scan looks up each local file's exact size there, with no network and no media reads. Their audit matched 3,686 local files against 28,824 cached layouts in 19 s.

For us, the same table over our `.torrent` cache would surface episodes inside packs, and items inside larger torrents, as Partial Matches. A first pass could build it from the files already cached.

### 3. Broader catalogue keys before fetching anything

When it picks which `.torrent` files to download (`internal/catalog/catalog.go`, `discovery.go`), TorrentMatch:

- matches the item's total and **every folder subtotal**, so a season folder inside a show folder finds its season pack;
- allows a near-total tolerance of `min(size/500, 2 MiB)` for content of 32 MiB or more, which absorbs an added or missing NFO or sample;
- uses a title key of title plus year, or title plus `SxxEyy`, which survives release tags and Plex renaming;
- drops candidates whose resolution or codec in the release name contradicts the local file's name before fetching, but keeps any candidate with an exact size;
- ranks candidates as TMDB identity, then release name or exact size, then TV scope, then title, then broader matches.

Our 2% aggregate tolerance is looser on totals and has no subtotals.

## Worth considering

4. **Remembering checks.** It skips a repeat recheck when the client, hash, add time, piece states and a fingerprint of the mapped files are all unchanged (`qbit_check_cache.go`). It records results with zero verified pieces, so unchanged mappings are not rechecked again. Our rejections only stop refetching a candidate.
5. **Respecting the client's check queue.** It reads `max_active_checking_torrents` and waits until earlier checks finish before submitting the next one, so a batch doesn't flood qBittorrent with rechecks.
6. **Cleaning up placeholders.** When it removes a torrent with no matched pieces, it deletes only the empty folders and zero-length files the client created at paths that didn't exist before (`qbit_cleanup.go`).
7. **Truncated files.** It maps a media file of 32 MiB or more that is at most 1,024 bytes **shorter** than the torrent's file, which covers an interrupted copy. The recheck then fails only the tail pieces.

## Low value for us

- **Recognising modified containers.** For files within 1 MiB of a torrent file's size, it hashes 3 interior pieces at the same offsets. A match means "same release, edited container", and is never ready to seed. It's off by default because it was slow: in one scan, 3,774 s of 3,807 s went to sampling.
- **MediaInfo resolution and codec probing.** It's off by default "pending evidence of matching benefit". Filename hints did the job.
- **In-app v1 piece verifier.** Clients already verify for us.
- **Rolling per-minute `.torrent` budget** (1,200 per minute, 20 per second, persisted). Our `ratelimit.py` covers the same need.
- **Native c411 title search.** It calls `GET /api/torrents?name=…&sortBy=size&palliativeTail=true` with the cookie session, walks every page, and switches the account to the flat view and back. Our JSON listing research already covers this endpoint.

## Suggested order

1 and 2 together: the reverse lookup produces more Partial Matches, and seeding partials makes them worth having. Then 3, which is cheap and confined to candidate selection. 4 to 7 are small hardening changes.

## Done

- 1 is implemented as ADR-0018. It was tested against qBittorrent 5.2.4, Deluge 2.2.0 and Transmission 4.1.3. qBittorrent leaves the stopped state a moment after the start request, not at once.
- 2 is implemented as `layout_file` in the Store and `review.layout_candidates`. A torrent is offered only when the Catalogue knows it and it is a Match.
- TorrentMatch treats each torrent as its own unit, so it reseeds an episode's torrent apart from its pack. TorrentLinker already made each episode of a pack its own Local Item, but a batch skipped them. A batch now checks a pack's episodes against the Catalogue and cached torrents, which spends no search (`library.pack_episodes`).
