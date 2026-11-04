# Multiple Torrent Clients

Use **Clients…** beside the desktop's target selector to add a connection, edit its settings or choose the default. Give each connection a short name, such as NAS or Seedbox. Several qBittorrent instances and mixed qBittorrent, Deluge, Transmission, µTorrent Classic and BitTorrent Classic connections are supported.

![Named Torrent Clients](../images/en/clients.png)

Your existing connection is kept automatically. Each client has its own credentials, Download Folders and Path Mappings. Open **Edit settings**, test the connection, then configure its folders. Library Folders and the c411 account are shared by the application.

Category folder choices show parents followed by indented subcategories. The WebUI picker shows the full category path after selection, so repeated names stay distinguishable. A parent can have its own destination; children inherit it unless they have an override. Empty Path Mappings can be checked automatically with a temporary file and stopped test torrent, both removed afterward. The mounted folder must be writable by TorrentLinker for this check.

The **Target client** selector controls Library checks and Reseeds, Upload seeding, Search downloads and the selected client's queue. Queue entries show their client. Changing the default or renaming a profile does not redirect queued work.

In **Torrents**, select **All clients** for a combined view. Filter by name and use the context menu on selected rows to act on the corresponding clients. Removal keeps files; deletion asks before deleting torrent data. Combined Zen estimates count each torrent hash once.

## Plans

Open **Plan destinations…**. Select one client for a dedicated Plan, or several for automatic distribution. Set each client's budget in GiB. A value of zero uses available space after your free-space margin.

![Choose Plan destinations](../images/en/plan-clients.png)

Review the Client and Download Folder columns before sending. Distributed Plans place each torrent on one selected client and share the free-space budget when clients use the same mounted disk. They use the existing Zen heuristic and do not promise a mathematically optimal placement.

An offline destination produces a visible failure. TorrentLinker does not substitute another client. Finish or remove pending queue entries, Plans and Verifications before replacing or removing their connection.

## Web interface

Use the target selector at the top of the page. Settings has **Add client**, **Make default** and **Remove client** controls, with a name field on the connection form. Folder setup applies to the selected client.

**All clients** shows a combined overview. Select a specific client before changing torrents or starting a queue. In the planner, expand **Plan destinations**, select clients and budgets, then review the result. Each row has a destination selector before it is queued. Switching that selector uses the new destination's configured Download Folder.

![Plan destinations in the web interface](../images/en/web-clients.png)

The server needs access to the files for scanning, Reseeds and Upload preparation. Web downloads still require a verified mounted Download Folder. API-only seedboxes can use torrent management.

## Seedbox files over SFTP, FTPS or FTP

Without access to its files, a seedbox supports torrent management, Zen and Search. To scan, compare, Reseed, use the media center or prepare an Upload, mount the seedbox's download folder on this computer with [rclone](https://rclone.org/), then add a Path Mapping. TorrentLinker does not create the mount itself.

Prefer SFTP, then FTPS. Plain FTP sends your password unencrypted. Run `rclone config` and create a remote named `seedbox`: type `sftp`, or type `ftp` with explicit TLS for FTPS. Enter the host, user and password your provider gives you. Then mount it read-only:

```bash
rclone mount seedbox:/home/user/downloads /mnt/seedbox --read-only --vfs-cache-mode full --vfs-cache-max-size 20G
```

Replace `/home/user/downloads` with the folder your Torrent Client downloads into, as the seedbox shows it. On Windows, rclone needs [WinFsp](https://winfsp.dev/) and mounts to a drive letter such as `S:`. On macOS, it needs macFUSE.

In the client's Path Mappings, map `/mnt/seedbox` on this computer to `/home/user/downloads` on the client, or use **Detect** (**Detect matching folders** in the Web UI) once the mount is up. Use the mounted folders as Library Folders and Download Folders.

Expect these limits:

- The first scan lists every folder over the network and can take minutes on a large library.
- Upload preparation reads every byte to hash the files, which downloads the whole release through the mount. The media center reads only what it plays; the cache above keeps up to 20 GB on this computer.
- An FTP mount cannot report the seedbox's free space, so rclone shows an invented size. Set this client's budget in **Plan destinations…** instead of zero. SFTP reports free space when the provider allows it.
- A read-only mount cannot be checked with a temporary file. The check uses a file of an existing torrent instead, so keep at least one torrent in the mapped folder.
- If the mount stops, its folder becomes empty or unavailable, and a scan then finds nothing in it. Check that the mount is running before you scan.
