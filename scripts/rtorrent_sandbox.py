"""Make a throwaway rTorrent in Docker ready for the integration tests.

    docker build -t tl-rtorrent scripts/rtorrent
    data=$(mktemp -d)
    docker run -d --rm --name tl-rtorrent -u $(id -u):$(id -g) \\
        -p 127.0.0.1:15000:5000 -v "$data:$data" tl-rtorrent
    python scripts/rtorrent_sandbox.py scgi://127.0.0.1:15000
    TL_RTORRENT_SANDBOX=scgi://127.0.0.1:15000 TL_RTORRENT_DATA="$data" \\
        pytest tests/test_rtorrent_integration.py
    docker stop tl-rtorrent

Waits until rTorrent answers on its SCGI port.
"""

import sys
import time

from torrentlinker.rtorrent import RtorrentApi

api = RtorrentApi(sys.argv[1])
for _ in range(60):
    try:
        print("rTorrent", api.check(), "ready")
        break
    except OSError:
        time.sleep(1)
else:
    raise SystemExit("rTorrent's SCGI port did not answer")
