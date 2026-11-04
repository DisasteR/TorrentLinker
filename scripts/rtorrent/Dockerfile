# A throwaway rTorrent for the integration tests; see scripts/rtorrent_sandbox.py.
FROM alpine:3.22
RUN apk add --no-cache rtorrent=~0.15
COPY rtorrent.rc /etc/rtorrent.rc
ENTRYPOINT ["rtorrent", "-n", "-o", "import=/etc/rtorrent.rc"]
