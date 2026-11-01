# Changes since 1.0.0-beta.28

Draft notes for the next beta. These changes are on `main`; they are not in the
published beta.28 packages.

## Library

- New Library Folders are **Automatic**: TorrentLinker detects Local Items at any depth
  instead of using one scan depth. It keeps release folders whole, opens genre and
  collection folders, and recognises disc structures, multi-disc albums and 3D model
  packs. Torrents already in the Torrent Client and Catalogue sizes help it find
  release folders. Existing folders keep their depth; a fixed depth remains available.
- Season and show packs stay one Local Item, and each episode is also a Local Item
  under its pack: the desktop Library is now a tree where packs expand to their
  episodes. Batch searches search a pack once and skip its episodes; select episodes
  to search them.
- An episode is Seeded only by a torrent of its own. Inside a seeded pack it shows
  **Orphan · in seeded pack**, so it can also be reseeded as a single episode.
- The add-folder dialog previews how many Local Items and episodes Automatic finds.

## Responsiveness

- At 12,000 queue entries, each successful send now blocks the window for about
  0.35 s instead of 1.45 s: sent rows are removed in place.
- The RSS inbox loads in 0.05 s instead of 1.15 s with 20,000 client torrents.
- Web queue sends probe each Download Folder once instead of once per entry.

## Validation

Automatic detection was compared with the depth scan on movie, series, music, ebook
and 3D model folders, a generated Library of 10,000 movies and 300 shows, and a
sandbox qBittorrent seeding real folders through a Path Mapping. See
`docs/research/library-item-detection.md`. A real SMB/NFS share and Libraries from
users who reported depth problems are not yet tested.
