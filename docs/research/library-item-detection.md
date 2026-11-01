# Detecting Local Items without a scan depth

Users struggle with the Library Folder scan depth. Today each Library Folder has one
depth: every entry exactly that many levels down is a Local Item, and files met earlier
count as items too (`scan._entries_at_depth`). A single number cannot describe common
mixed layouts:

```text
Films/
  Film.2019.mkv                       item at depth 1
  Film.2020.MULTi.1080p-GRP/          item at depth 1
  Collection Nolan/                   container
    Inception.2010.1080p-GRP/         item at depth 2
Séries/
  Show/                               container (or complete-series item)
    Saison 01/                        item at depth 2 (season pack)
```

## How comparable tools handle it (2026-11-01)

- **cross-seed** (data-based matching): `dataDirs` plus one global `maxDataDepth`,
  default 2. Its tutorial gives a value per layout (1 for flat movies, 2 for
  Radarr/Sonarr layouts, 3 to reach episodes) and tells users to take the highest value
  when layouts are mixed. Deeper values only add searches and indexer load. There is no
  automatic detection. [Options](https://www.cross-seed.org/docs/basics/options),
  [data-based matching tutorial](https://www.cross-seed.org/docs/tutorials/data-based-matching).
- **qui** (autobrr) Dir Scan: depth is fixed at 1. Each immediate child of the
  configured path is one item and files inside stay together. Disc folders (`BDMV`,
  `VIDEO_TS`, `AUDIO_TS`) are one unit. Users must add one entry per category folder
  rather than a parent folder. Episodes can optionally be grouped into season packs.
  [Dir Scan](https://getqui.com/docs/features/cross-seed/dir-scan).
- **Plex/Jellyfin**: rely on the user following a naming and folder convention (one
  folder per movie; show, then season folders). Category folders nested inside a library
  are flattened or missed, and the forums' fix is one library per category.
  [Plex forum](https://forums.plex.tv/t/scan-library-ignoring-nested-directories/660815),
  [Jellyfin forum](https://forum.jellyfin.org/printthread.php?tid=13543).
- **Sonarr/Radarr Library Import**: each child of a root folder is a series or movie
  candidate. Deep or non-standard folders end up as unmapped folders the user resolves
  by hand. [Sonarr forum](https://forums.sonarr.tv/t/sonarr-not-automatically-monitoring-imported-libraries-failed-to-map-folders/33906).
- **GuessIt**, already used by `queries.parse_name`, parses release names into title,
  year, season, episode, resolution, source and group.
  [Docs](https://guessit.readthedocs.io/).

None of these tools detects item boundaries automatically. The common answer is a
convention the user must follow. TorrentLinker can do better because it already holds
signals these tools don't use: the Torrent Client's file lists, the c411 Catalogue's
exact sizes, and the release-name parser.

## Proposal: automatic structure, depth as an override

Walk each Library Folder from its root (with a safety limit of about 8 levels). Each
loose file is an item, as today. For each directory, the first rule that applies
decides whether it is an **item** (stop descending) or a **container** (look at its
children):

1. **Seeded anchor.** A client torrent's root is this directory, so it is an item. A
   child is a torrent root, so this is a container. `build_library` already maps every
   client file path to its torrent hashes.
2. **Disc structure.** It contains `BDMV`, `VIDEO_TS` or `AUDIO_TS`, so it is an item.
3. **Catalogue size.** Its total size equals the exact byte size of a Catalogue torrent,
   so it is an item. Ordinary Library comparison still verifies the files.
4. **Season folder.** Its name parses as a season (`Saison 01`, `Season 1`, `S01`,
   `Show.S01.1080p-GRP`), so it is an item. Its parent show folder is a container, and
   can still be searched as a complete series through the existing combined manifest
   (`LocalItem.search_name`).
5. **Content files.** It directly contains files of the folder's Content Kind (video,
   audio, ebook, 3D model, game archive), so it is an item. This is the Plex
   "one folder per movie" rule. Exceptions: a video folder holding two or more distinct
   releases (different title or year) is a genre container, and a 3D model folder with
   model files anywhere below is one item, variants and parts included.
6. **Release name.** GuessIt finds release evidence in the name (a year with a title,
   resolution, source, group, or season or episode), so it is an item.
7. **Otherwise** it is a container: generic names such as `Films`, `4K`, `A-Z` or
   `Collection Nolan`.

**Episodes.** Packs stay items, and every episode inside a season or show pack (file
or release folder) is also an item, recorded with the pack it belongs to. A complete
series exposes its season folders the same way. Loose episode files are items of their
own. Outside a Series folder an explicit marker (`S01E02`, `S1.EP 01`, `1x02`) is
required, because GuessIt reads `Star.Wars.Episode.1`, `Episode.50.2011` or a bare
`1080` (S10E80) as episodes.

Apart from episodes, items never overlap: descent stops at the first item. Each detected item records its
reason, so the interface can explain it ("seeded in qBittorrent", "release name",
"contains video").

### Interface

- Add-folder dialog: mode **Automatic** (default) or **Fixed depth** (advanced). In
  Automatic mode, show a preview of the detected items with counts per reason, and the
  ambiguous directories.
- Library context menu: **Treat as one item** and **Split into its contents**. These
  store per-path overrides on the Library Folder and survive rescans.
- Existing folders keep their depth on upgrade. Offer Automatic with a before/after
  preview so nothing changes silently.

### Risks and open questions

- A show folder holding loose episode files directly (no season folders) is detected
  as one item by rule 5, which matches a complete-series or single-season pack. Episode
  splitting stays out of scope, as in qui.
- Music: an artist folder of album folders is a container; an album folder with audio
  files is an item (rule 5). Multi-disc albums (`CD1/`, `CD2/`) need a rule like the
  disc structures.
- Rule 3 depends on how complete the Catalogue crawl is. It only confirms; it never
  splits.
- Deeper walking costs extra directory listings only at container levels; item
  interiors are already walked to build their file lists.
- Searches stay per item, so detection must not multiply indexer queries. A wrong split
  wastes c411 quota, so the preview matters.

## Prototype results (2026-11-01)

`torrentlinker.item_detection.detect_items` implements the rules above as a read-only
function; it is not wired into scanning. `tests/test_item_detection.py` covers mixed
movie layouts, season packs with episodes, complete series, music, seeded and Catalogue
anchors, genre folders and 3D variant packs.

`scripts/compare_item_detection.py` compares it with the depth scan on the configured
Library Folders, using Catalogue sizes but no Torrent Client (so rule 1 was not
exercised). On the maintainer's Library:

| Library Folder | Depth scan | Detected | Differences |
|---|---|---|---|
| 3D models, depth 1 | 65 | 65 | none |
| Movies, depth 1 | 60 | 60 | none |
| Mixed movies, depth 2 | 196 | 188 + 45 episodes | 14, all improvements |

On the mixed folder, depth 2 split three release folders into their files and listed
`.srt`, `.nfo`, `.torrent` and `.pdf` files as items; detection keeps the releases whole
and skips support files. It also found four Monty Python season packs and their 45
episodes inside the movie folder. Catalogue sizes identified 24 items. The first runs
exposed the genre-folder, 3D-variant, slice-format and movie-title-episode cases, now
fixed and covered by tests.

Not yet measured: libraries with Series, music or ebooks, network shares, and timing on
large trees (the Catalogue rule walks each candidate folder once to sum its size).

## Suggested next step

Run the comparison script on Libraries from users who reported depth problems
(it prints paths, so they review it first), with seeded roots from their Torrent
Client. Then wire Automatic mode into `scan`, with episodes as Local Items linked to
their pack, and add the preview to the add-folder dialog.
