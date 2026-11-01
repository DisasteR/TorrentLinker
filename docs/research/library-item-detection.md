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
2. **Disc structure.** It contains `BDMV`, `VIDEO_TS` or `AUDIO_TS`, or disc folders
   of a multi-disc album (`CD1`, `Disc 2`, `Disque 1`), so it is an item.
3. **Catalogue size.** Its total size equals the exact byte size of a Catalogue torrent,
   so it is an item. Ordinary Library comparison still verifies the files. A folder
   wrapping a single subfolder is skipped: both have the same size, and the inner name
   is the release.
4. **Season folder.** Its name parses as a season (`Saison 01`, `Season 1`, `S01`,
   `Show.S01.1080p-GRP`), so it is an item. Its parent show folder is a container, and
   can still be searched as a complete series through the existing combined manifest
   (`LocalItem.search_name`).
5. **Content files.** It directly contains files of the folder's Content Kind (video,
   audio, ebook, 3D model, game archive), so it is an item. This is the Plex
   "one folder per movie" rule. Exceptions: a video folder holding two or more distinct
   releases (different title or year) is a genre container, and a 3D model folder with
   model files anywhere below is one item, variants and parts included.
6. **Release name.** GuessIt finds a title with release evidence (year, resolution,
   source, group or season), and content files exist somewhere below, so it is an item.
   A folder whose subfolders are themselves named releases (`Les 100/The.100.S01…`,
   `The.100.S02…`) groups them instead and stays a container.
7. **Otherwise** it is a container: generic names such as `Films`, `4K`, `A-Z` or
   `Collection Nolan`.

**Episodes.** Packs stay items, and every episode inside a season or show pack (file
or release folder) is also an item, recorded with the pack it belongs to. A complete
series exposes its season folders the same way. Loose episode files are items of their
own. Outside a Series folder an explicit marker (`S01E02`, `S1.EP 01`, `1x02`) is
required, because GuessIt reads `Star.Wars.Episode.1`, `Episode.50.2011` or a bare
`1080` (S10E80) as episodes.

Hidden files and folders, such as qBittorrent's `.parts` folders, are ignored.

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

- A show folder holding loose episode files directly (no season folders) is one pack
  item by rule 5, with each episode as an item too. When the episodes come from
  different releases (`charmed/` mixes groups), the pack item will not match a torrent;
  only the episodes can.
- Sample packs and genre folders have the same shape (`Drum/Kicks/*.wav`,
  `Trance/adja/ADJA 2/*.mp3`). Detection splits both into their album-level folders.
  Only a seeded root, a Catalogue size or a user override can keep a sample pack whole.
- Files of another kind are skipped: a soundtrack in a movie Library Folder is not an
  item, while a depth scan would list it.
- Rule 3 depends on how complete the Catalogue crawl is. It only confirms; it never
  splits.
- Deeper walking costs extra directory listings only at container levels; item
  interiors are already walked to build their file lists.
- Searches stay per item, so detection must not multiply indexer queries. A wrong split
  wastes c411 quota, so the preview matters.

## Prototype results (2026-11-01)

`torrentlinker.item_detection.detect_items` implements the rules above as a read-only
function; it is not wired into scanning. `seeded_roots` turns client torrents (after
Path Mapping) into the roots rule 1 needs. `tests/test_item_detection.py` covers each
layout below with fixture trees.

`scripts/compare_item_detection.py` compares detection with the depth scan, on the
configured Library Folders or on any `--folder PATH:KIND[:DEPTH]`, using Catalogue sizes.
On the maintainer's disks:

| Folder | Files | Depth scan | Detected | Differences |
|---|---|---|---|---|
| 3D models, depth 1 | 1,795 | 65 | 65 | none |
| Movies, depth 1 | 72 | 60 | 60 | none |
| Mixed movies, depth 2 | 339 | 196 | 187 + 45 episodes | all improvements |
| Series, depth 1 | 18 | 2 | 2 + 13 episodes | none |
| Mixed series, depth 2 | 295 | 42 | 15 + 241 episodes and seasons | all improvements |
| Ebooks, depth 1 | 14,379 | 10,953 | 10,945 | 8 empty folders and audiobooks |
| Music, depth 1 | 49,089 | 80 | 204 | sample pack split, see risks |

- **Movies:** depth 2 split three release folders into their files and listed `.srt`,
  `.nfo`, `.torrent` and `.pdf` files as items. Detection keeps the releases whole,
  skips support files, and finds four Monty Python season packs with 45 episodes.
- **Series:** depth 2 split three season packs (`Les 100/The.100.S01…`) into episodes
  and listed qBittorrent's hidden `.parts` folder. Detection finds every season pack,
  `Futurama saisons 1 à 6 + films` as one pack with its seasons, and every episode,
  whether a loose file, a file in a season folder or a release folder.
- **Music:** artist folders split into albums (`Scylla/[2011] Thermocline`), and a
  four-disc soundtrack (`CD1`–`CD4`) stays one item. The `Drum` sample pack splits into
  144 kit folders.
- **Catalogue sizes** identified 967 items across the folders, including the real
  torrent root inside a wrapper folder.

### Seeded rule

A throwaway qBittorrent 5.2.4 container (the local image, listening on 127.0.0.1 only)
mounted the 3D model folder read-only at `/data/STL`. Three real items were hashed and
added stopped: a folder, a model folder, and one variant two levels inside a pack. The
app's `MappedQbit` translated the client's `/data/STL` paths back to the host view, and
`seeded_roots` gave the three roots. Detection marked them seeded and split the pack
around the seeded variant, its siblings becoming items of their own. The run also showed
that an image-only `(Images)` folder passed the release-name rule; rule 6 now requires
content below. The user's own Torrent Client was never contacted.

### Network shares and timing

No SMB or NFS share was available without root, so filesystem calls were counted
instead; each uncached metadata call is about one network round trip. Detection lists
each visited folder once with `os.scandir`, whose entries carry their type, and does not
stat files except to sum sizes for the Catalogue rule.

| Folder | Depth scan calls | Detection calls |
|---|---|---|
| Mixed movies | 495 | 122 |
| Mixed series | 370 | 68 |
| Music | 53,790 | 251 |
| Ebooks | 16,440 | 1,057 |
| 3D models | 2,192 | 61 |

The depth scan count includes walking each item's files, which Automatic mode still
needs after detection, so detection adds these calls rather than replacing them. The
Catalogue rule adds a stat per file of each candidate folder; it walked only the 3D,
ebook and wrapper folders above.

On a generated Library with 10,000 movies (16,666 files, a third loose in genre
folders) and 300 shows (9,000 episodes), detection takes 0.64 s and 1.06 s against
0.49 s and 0.21 s for the depth scan, on local disk. The first version took 72 s: GuessIt
costs milliseconds per name. It is now asked only when a cheap test cannot decide: season
names need a season word, `S01E02`-style markers settle episodes, a folder with one video
cannot hold two releases, and genre detection stops at the second release.

Still not measured: a real SMB/NFS share, and a Library from a user who reported depth
problems.

## Status

Automatic mode is wired into scanning (ADR-0015): new Library Folders default to it in
the desktop and web editors, episodes are Local Items linked to their pack, batch
searches skip episodes whose pack is searched, and the add-folder dialog previews the
result. Still to do: per-path overrides (**Treat as one item**, **Split into its
contents**), a preview in the web form, and the measurements listed above.
