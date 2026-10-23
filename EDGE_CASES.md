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

- An empty `gh repo list` wiped the feed (#8) → an empty answer from an upstream fetch counts as a failed fetch; keep the previous data.
- A config `slot` of "3" or 2.5 crashed the HTML renderer (#8) → coerce or validate every config value read from JSON, and expect strings, floats and nulls.
- An HTML render error stopped the whole send (#8) → if an extra output part fails, log it and fall back to the main path (send text only).
- Titles and URLs from GitHub go into HTML (#8) → escape text and attributes; only https URLs get an href.
- Email HTML has limits (#8) → tables and inline styles, no external assets, and stay under Gmail's 102KB clip.
- Digest state could advance without a sent mail (#7) → write state atomically, and only after the server accepts the mail.
- Overlapping runs shared a tmp file and could move the window back (#7) → give each run its own tmp file; an older run never moves the window backwards.
- Stored timestamps can be missing, malformed or in the future (#7) → fall back to a safe default window; never crash or skip a window.
