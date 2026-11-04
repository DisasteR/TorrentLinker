# Seedbox file access over FTP, FTPS and SFTP

Status: proposal, waiting for a decision. Nothing here is implemented.

A user's seedbox gives FTP access to its files. Today TorrentLinker only talks to a seedbox through the Torrent Client's API. Everything that reads files needs a local or mounted path: the guides say so, `docs/research/docker-web-deployment.md` left "built-in SSH/SFTP storage access" as a separate proposal, and the Docker guide states that TorrentLinker does not create SSHFS/NFS/SMB mounts. This note asks what FTP (and FTPS/SFTP) could unlock and how.

One correction to the question first. TorrentLinker does not link or hardlink files into the Library. ADR-0004 forbids writing files the app did not create, and a Reseed points the Torrent Client at the existing data. "Linking into the Library" here means Scan, Match and Reseed, which need to list files, not to create links. Hardlinks are impossible over FTP anyway: they must sit on the seedbox's own filesystem.

## Where the code assumes local files

Every path the app reads goes through `pathlib.Path` and `os` calls on this machine. The Torrent Client's paths are translated by `PathMapper` (`paths.py`, ADR-0006), which only rewrites a prefix. It assumes the translated path then exists here.

| Feature | Code | What it reads | Bytes needed |
|---|---|---|---|
| Library Scan, Local Items | `scan.py` (`os.walk`, `stat`), `item_detection.py` | names, sizes, mtimes | listing only |
| Seeded / Orphan, Candidates, Match | `reconcile.py`, `matching.py` | Scan output + client API | none beyond the Scan |
| Reseed | `reseed.py` `_missing` (`exists`), Verification by the client | file existence | listing only |
| µTorrent disk renames | `disk_rename.py` (ADR-0013) | renames on disk, undo journal | write access |
| Path Mapping detection and checks | `paths.py` `detect_mappings`, `check_mappings`; `web/setup.py` `suggest_mappings` | name + size lookups | listing only |
| Folder proof for web downloads | `folder_probe.py` `verify_folder` | creates a temporary file | write access |
| Plan free space | `plan.py` `disk_space` (`shutil.disk_usage`, `st_dev`) | disk size and free space | none, but FTP cannot report it |
| Media center scan | `media.py` `MediaLibrary._scan` (`os.walk`, `stat`), `checked_path` (symlink checks, `resolve`) | listing | listing only |
| Media center details | `media.py` `inspect`, `media_info.py` (`MediaInfo.parse(path)`) | headers of each video | a few MB per file |
| Playback | `web/media.py` (`FileResponse` with Range, `path.open("rb")`) | ranged reads | streaming |
| Provenance Evidence | `provenance.py` `verify_pieces`, `identity` (`st_ino`) | every piece | whole files |
| Repair Copy | `repair.py` | reads and writes whole files | whole files + write |
| Upload preparation | `upload.py` (torf hashing), `upload_video.py`, `audio_tags.py`, `model_rendering.py` | every byte for the hash | whole files |

Two groups fall out of this table. Scan, Match, Reseed, mapping detection and the media center listing only need names, sizes and mtimes. FTP gives those cheaply. Playback and MediaInfo need ranged reads, which FTP can do (`REST` before `RETR`) and SFTP does natively. Evidence, Repair and Upload hashing read every byte. Over FTP that means downloading the whole release to hash it once, which works on a fast line and hurts on a slow one.

The "file verification" in the task is mostly the Torrent Client's hash check (Verification), which runs on the seedbox and already works without file access. The only app-side checks that read bytes are Provenance Evidence and Upload hashing.

## Approaches

### A. Recommend a mount: rclone, sshfs, curlftpfs

The user mounts the seedbox (`rclone mount seedbox: /mnt/seedbox --vfs-cache-mode full`), then adds a Path Mapping from `/mnt/seedbox` to the client's path. ADR-0006 already covers this case, and every feature works today with no code change.

For: zero code, every feature including Upload and playback, rclone speaks FTP, FTPS and SFTP and runs on Linux, macOS and Windows (WinFsp). The Docker image can bind the mount.
Against: the user sets it up and keeps it running. A dropped mount looks like an empty folder (the code already reports unavailable storage through `StorageUnavailableError`). Over FTP, `os.walk` on a FUSE mount is slow on large libraries. curlftpfs is unmaintained, so rclone should be the one we document. `folder_probe` writes a file, so the mount needs write access for the web download check.

### B. A file-access port with local and remote backends

Add a small read-only interface (list a folder, stat a file, open a byte range) with a local backend wrapping today's `os`/`Path` calls and remote backends: `ftplib.FTP_TLS` from the standard library for FTP/FTPS (using `MLSD` for sizes and mtimes) and paramiko or asyncssh for SFTP. A Library Folder, or a whole Torrent Client profile, gets a storage setting: local, or a remote URL with credentials in the existing secrets store.

For: no setup outside the app, works in the Docker image as-is, and the app controls caching and timeouts.
Against: this is the expensive option. `Path` objects run through `scan.py`, `LocalItem`, `media.py`, `provenance.py`, `upload.py`, the GUI and the web routes. The safety checks rely on local semantics: symlink rejection, `resolve(strict=True)` under storage roots, `st_dev`/`st_ino` identity for evidence staleness. Each needs a remote equivalent or a rule that the feature is local-only. SFTP adds a dependency. FTP cannot report free space; SFTP can only with the OpenSSH `statvfs` extension.

### C. Fetch completed files to this machine

Download a completed torrent's files over FTP into a local folder, then work on the copy.

This duplicates the data and defeats the point of a seedbox. It has one sensible use: a "download to watch" or "fetch to prepare Upload" action on one item, closer to a Repair Copy than to a storage model. Not a general answer.

### D. Ask the Torrent Client instead, where it already knows

Not FTP, but it removes one blocker. qBittorrent (`free_space_on_disk` in sync data), Transmission (`free-space` RPC) and Deluge (`core.get_free_space`) report the seedbox's free space. `plan.disk_space` uses `shutil.disk_usage` today. Reading free space from the client would let the Planner budget a seedbox with no file access at all. The deployment research asked for exactly this: do not use the container's free space for the seedbox's.

## Recommendation

I would not start with B. The table shows that most of the value comes from listing, and a mount already gives listing plus everything else. Building a port across the whole app buys convenience for the users who cannot or will not run rclone, at the cost of re-proving every local safety check.

Suggested order:

1. **Document rclone as the supported way** (guide pages in `en` and `fr`, Docker example with a bind mount). Include FTP, FTPS and SFTP remotes, the Path Mapping to add, and the write access `folder_probe` needs. Cost: docs only. Unlocks every feature for this user today.
2. **Client-reported free space** for Plans (approach D), so seedbox users without any mount can plan. Small, contained in `plan.py`, the client ports and their tests against Docker clients.
3. **Only if users still ask for no-mount access:** a read-only remote listing backend (approach B, scoped). Use it first for Library Scan and media center listing of a remote Library Folder, which unlocks Orphans, Match, Reseed and the media center catalogue. Playback and MediaInfo through ranged reads come next. Evidence, Repair, Upload and µTorrent disk renames stay local-only and say so in the UI. This would need an ADR and a domain term for the new storage kind (CONTEXT.md says to avoid "remote path").

Security notes for any built-in backend: plain FTP sends the password in clear text, so offer it only with a warning and prefer FTPS or SFTP. SFTP needs host-key pinning on first use. Credentials go in the existing secrets store, never in settings files. Tests must use a throwaway FTP/SFTP container, never the user's real seedbox.

## Open questions for the user

- Does the reporter's seedbox offer SFTP as well, or only FTP/FTPS? Most do offer SFTP.
- Is the reporter on the desktop app or the Docker web image? A mount is easy on Linux and Docker, more work on Windows.
- Is "documented rclone + client free space" enough, or is no-mount access a goal in itself?
