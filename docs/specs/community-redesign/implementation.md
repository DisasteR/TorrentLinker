# Community interface implementation

Approved layout: compact searchable torrent library with an action/detail panel, separate chat/draft preparation, queue and operation history. Seed discovery starts automatically and traverses all pages through the paired companion. No manual next-page step. Preserve unsaved editor contents during background refresh.

History retains operation text and state transitions. Search, action filters, all-time/today/7-day/30-day/custom inclusive dates and a navigable monthly timeline make older operations retrievable.

Upcoming operations have a calendar, week/month navigation and day details. Show manual versus automatic origins and estimated local times. Operations can be inspected, paused, cancelled and edited/rescheduled. Actual dispatch must retain the existing account checks, live site eligibility, quotas, cooldowns and uncertain-send protection.

Automatic mode is opt-in, with separate comment, prepared-vote and chat toggles, editable comment template, selected chat messages/channels, publication hours/timezone and existing per-action daily limits/intervals. Plan at most seven days ahead, reserve capacity alongside manual operations, do not repeat known actions. Votes keep the owner's prepared choice. Disabling automatic actions preserves manual operations. Settings and journal survive restart.

Desktop and Web UI must expose these capabilities. Uploads remain excluded. No live c411 posts in tests. Test seams approved by the user: CommunityActions, public desktop/Web interfaces, browser acceptance. Implement in the canonical checkout and refresh the unpacked companion at dist/torrentlinker-companion. Commit on current branch; no publication requested.

Approved interactive prototype archived on `prototype/community-redesign-20261117` at `f3efc41fba65ecdcc44280af72985f8e2aa937f8`. The production implementation uses the shared persistent journal and native Qt/Web interfaces rather than the prototype simulation.
