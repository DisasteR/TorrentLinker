# Ship an encrypted Catalogue with the app

The maintainers' Catalogue has grown into an almost full copy of c411: 227,942 torrents on 2026-11-05. It is built with Zen Lab, our partner TheGeeKing01's project, and with c411's own listing. New users waited for Zen Lab's snapshot at their first Plan, and depended on zen.mma.dev being up. We want our own Catalogue in every package, without publishing it where bots harvest torrent metadata: the public repository, GitHub release files, the Docker image.

## Decision

- **Never in the repository.** A maintainer exports the Catalogue from their desktop app (`scripts/export_catalogue.py`) and attaches the xz-compressed file to the `catalogue` release of a private repository (`Vietnoirien/torrentlinker-catalogue` by default, or the `CATALOGUE_REPO` variable).
- **Sealed per build.** Each release job downloads that file with the `CATALOGUE_TOKEN` secret. It then seals the file with a new AES-256-GCM key (`scripts/bundle_catalogue.py`) into `assets/catalogue.tlcat`, and writes the key into the generated module `torrentlinker/_catalogue_key.py`. Git ignores both files. The header (format, creation time, row count) is authenticated with the rows. The download links are not shipped: they are rebuilt from each info hash, with the API-key placeholder.
- **Imported once.** At each c411 Catalogue refresh, the app compares the bundle's creation time with the last import it recorded (feed `bundled`). It imports a newer bundle in batches, in about 3 s for the full Catalogue. Bundled rows never replace live c411 rows, as Zen Lab's never did. Zen Lab's snapshot is downloaded only by a build that ships no Catalogue.
- **Kept current.** Every four hours while it runs, the desktop app and the web server read every category newest-first, down to the rows they already hold (`catalogue_sync.refresh_catalogue`). With the bundle as the baseline, that is a few requests per category, made in the background lane so the user's searches go first.
- **Without the secret** (forks, or runs from source), the build warns and ships no Catalogue. The app then behaves as before.

## Consequences

- Each package grows by about 9 MB.
- The encryption keeps titles and info hashes away from crawlers and code search. It is not a secret from a person: the key ships inside the same package, so someone who takes the app apart can read the Catalogue. Never rely on it for anything else.
- A release ships the Catalogue as of its last export. Every release starts with a fresh export (`scripts/export_catalogue.py` exports and uploads in one step), and a tagged build refuses an export older than 48 h. The app's live c411 refresh fills in what came after.
- `cryptography` becomes a dependency of the main app, not only of the web server.
