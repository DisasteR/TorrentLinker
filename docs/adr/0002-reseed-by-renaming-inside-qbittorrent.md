# Reseed by renaming inside qBittorrent, not by hardlinking

When a Match's paths differ from the local files, we add the torrent pointed at the existing data and rename each of its files inside qBittorrent to the local path holding that file's data, so the torrent fits the library as it is. The user's files are never moved, renamed or linked on disk. The alternative used by cross-seed — hardlinking the data into a separate folder laid out the way the torrent expects — keeps the torrent pristine but requires the link folder to share a filesystem with the data, doubles the directory trees to maintain, and isn't what the user asked for ("change the folders and the torrent names").

## Consequences

- qBittorrent's file rename takes a full path relative to the save path, so per-file renames also cover a different root folder and restructured layouts (flattened or reorganised subfolders). Hardlinking is no longer needed for those.
- A Reseed is only started once Verification shows every piece is already on disk. A Match with absent files (Partial Match, or Ignored Files missing locally) gets priority 0 on those files and is left stopped after Verification, because starting it would fetch the pieces it shares with local files and write them into the library. The user decides whether to start it. (Superseded by ADR-0018: it now seeds with every file skipped.)
