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
