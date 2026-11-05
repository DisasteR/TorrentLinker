# Seed Partial Matches with every file skipped

ADR-0002 left a verified Partial Match stopped. Its absent files were skipped, but a piece shared by a present file and an absent one stayed wanted, so starting the torrent would have written that piece into the user's file. The user had to decide, and in practice Partial Matches earned nothing.

Once Verification accepts a Partial Match, the Reseed flow now sets **every** file of the torrent to priority 0, present ones included, and reads the priorities back until the Torrent Client reports none wanted. Only then does it start the torrent. Nothing is wanted, so nothing is downloaded, and the client uploads the pieces it verified. If the client does not confirm within five seconds, or its file list changes meanwhile, the torrent stays stopped with its `torrentlinker:partial` tag, as before. The idea comes from TorrentMatch (`docs/research/torrentmatch-techniques.md`).

This applies to the clients that reseed Partial Matches: qBittorrent, Transmission and Deluge. rTorrent and µTorrent still refuse them (ADR-0017, ADR-0013). On Deluge, starting lifts the speed guard of ADR-0009, which no longer matters because no piece is wanted.

A user who sets a file's priority back in the client will download into the library. That is the client's normal behaviour, and the app never does it.
