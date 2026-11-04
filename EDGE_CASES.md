# Edge cases

Bugs and traps already hit here. The spec writer and the `/ship` reviewer walk
this list.

- Python 3.9 is the floor (macOS system Python) → no `X | Y` annotations, no `match`; CI runs 3.9 and 3.13.
- Boards can belong to a user or an org → query `repositoryOwner`, not `user`.
- Boards grow past one page (100 items) → paginate; a Backlog column alone can hold dozens.
- A Backlog-style column floods the board list → `count_only_columns`.
- Squash and merge commits duplicate their PR's entry → fold them into the PR; the sparkline still counts every commit.
- Bot branches aren't work (e.g. a GIF store branch) → `ignore_branches`, never hard-coded names.
- Issue bodies can be empty or null, and sections can be missing or use `###` → parsing must tolerate all of it.
- Paths guessed from prose are noisy (`src/app/` overlaps everything, code like `router.replace('/')`) → exact-file matches only unless both cards list a Touches section.
- Long flags overflowed rows at phone width → flags wrap; check every new row type at ~400px.
- "Today" in sparklines is the viewer's local day, not UTC.
- The page is the only client for writes, but any site the viewer opens can POST to it → JSON content type + `X-Devfeed` header (forces a preflight that's never answered) and a `Host` allowlist (DNS rebinding); never add CORS headers.
- The page can be minutes stale when a card is swiped → the server re-reads the card and refuses unless it's still in the column the page saw.
- Seen marks key on timestamps → the hosted demo's time shift must stay fixed per page load, or seen rows come back every refresh.
- A drag on a link fires a click on release → swallow the click after a drag; a tap must still open the link.
