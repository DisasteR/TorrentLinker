# Detect Local Items without a scan depth

A single scan depth per Library Folder cannot describe mixed layouts: loose releases next to collection folders, genre folders of loose files, show folders with season packs. Users chose wrong depths and got split releases or merged genres. Comparable tools (cross-seed's `maxDataDepth`, qui's fixed depth 1, Plex and Sonarr conventions) leave this to the user. See `docs/research/library-item-detection.md`.

New Library Folders are Automatic (`depth` is `None`). `item_detection` decides, per directory, whether it is one Local Item or a container to open, using in order: a Torrent Client torrent's root, disc structures, an exact Catalogue size, a season name, content files of the folder's Content Kind, then a release name with content below. Each item records why it was detected. Existing folders keep their depth, which stays available for layouts detection gets wrong.

Season and show packs stay Local Items, and each of their episodes is also a Local Item linked to its pack (`LocalItem.within`). This is the one place Local Items overlap. The desktop Library shows them as a tree. An episode is Seeded only by a torrent rooted at or inside it: the pack's torrent leaves it an Orphan, so the same files can also be seeded as a single-episode torrent. Batch searches skip an episode when its pack is in the batch, whatever the pack's state, so c411 quota is spent once per pack; episodes selected without their pack are searched.

Detection is read-only and lists each directory once. GuessIt is asked only when cheaper tests cannot decide, because it costs milliseconds per name. Known limits: sample packs and genre folders have the same shape, and files of another Content Kind (a soundtrack in a movie folder) are not Local Items.
