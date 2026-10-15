# devfeed

One page for what shipped and what's waiting, across all your GitHub repos.

Built for people with a lot of side projects, or with agents opening PRs for them:
open it in the morning and see what merged, what's waiting for your review, what's
being built on a branch with no PR yet, and what moved on your project board since
you last looked.

![The Feed view](docs/feed.png)

- **Since you last looked.** Counts merges, pushes, new PRs, active branches and board
  moves since your last visit, and marks those rows. Tracked per device.
- **Waiting on you.** Open PRs with check and review state, branches with recent
  commits but no PR, and project-board cards with their own labels. Flags PRs open
  7+ days, branches quiet 3+ days, cards stuck in progress for 3+ days, and puts cards
  with your attention label (`needs-human` by default) on top.
- **Repos find themselves.** Every non-archived repo you've pushed to in the last two
  weeks shows up, most recent first, and drops off when it goes quiet. Nothing to list.
- **Ship Log.** A second, calmer view: everything shipped, by project and day.

![The Ship Log view](docs/shiplog.png)

## Try it

**[Live demo](https://rubesworld.github.io/devfeed/)**, with made-up projects. Or run the
demo yourself; no GitHub needed, just Python 3.9+:

```sh
git clone https://github.com/RubesWorld/devfeed && cd devfeed
python3 devfeed.py demo
```

Open http://127.0.0.1:8300.

## Run it on your repos

Requires the [GitHub CLI](https://cli.github.com), logged in with `gh auth login`. On a
server or in a container, set `GH_TOKEN` to a personal access token instead; `gh` picks
it up. The token needs read access to your repos, plus `read:project` if you use a board.

```sh
cp config.example.json config.json   # set "owner" to your GitHub user or org
python3 devfeed.py serve
```

`serve` fetches from GitHub every 5 minutes and serves the page. There are no
dependencies beyond Python's standard library, and nothing is stored: each run
rewrites `public/feed.json` from scratch.

To keep it running in the background, see [`examples/`](examples): a launchd agent
for macOS, a systemd unit for Linux, and a cron line if you'd rather serve
`public/` yourself and only run `devfeed.py collect`.

## Configuration

`config.json`:

| Key | Default | |
|---|---|---|
| `owner` | — | GitHub user or org whose repos to watch. `owners` takes a list. |
| `bind` | `127.0.0.1` | Address to serve on. Use a VPN address (Tailscale, say) to reach it from your phone without exposing it to the internet. |
| `port` | `8300` | |
| `interval_minutes` | `5` | How often `serve` re-fetches. The page flags itself stale after 4 missed runs. |
| `active_days` | `14` | A repo shows up if pushed within this many days. |
| `days` | `30` | How far back the shipped list goes. |
| `spark_days` | `14` | Days in each project's sparkline. |
| `hide` | `[]` | Repos never to show, as `owner/name`. |
| `ignore_branches` | `[]` | Branch names that aren't work in progress. |
| `repos` | `[]` | Per-repo overrides: `display` name, colour `slot` (1–8) and `pin: true` to show it even when quiet. |
| `board` | `null` | A GitHub Projects board, see below. |

### Project board

```json
"board": { "owner": "your-user-or-org", "number": 1 }
```

Columns come from the board's own `Status` field, in order. Optional keys: `title`
(defaults to the board's name), `done_column` (`"Done"`), `active_columns` (which
columns count as "in progress" for the stuck flag; defaults to the last two before
Done), `attention_label` (`"needs-human"`) and `hide_labels` (`[]`). Reading a board needs the
`read:project` scope: `gh auth refresh -s read:project`.

Each card shows the issue's own labels in their GitHub colours, up to three, with the
rest behind a "+N" you can hover. One label is special: the `attention_label` puts the
card at the top, marked "needs you". To keep a noisy label off the cards, list it in
`hide_labels`:

```json
"board": { "owner": "you", "number": 1, "attention_label": "blocked", "hide_labels": ["good first issue"] }
```

Only issues are shown; draft notes and PR cards on the board are skipped.

## Limits

devfeed is built for one person's or one small team's repos, not a large org:

- Up to 200 repos per owner are considered (most recently pushed first).
- Per repo: the latest 60 commits, 20 recently closed PRs, 20 open PRs and 20 branches.
- The first 100 cards on the board.

Each repo costs about three GitHub API calls per run, so 20 active repos at the default
5-minute interval use about 15% of GitHub's hourly limit.

## Security

devfeed shows whatever your `gh` login can see, private repos included, to anyone
who can reach the page. It binds to `127.0.0.1` by default. If you change `bind`,
put it on a private network, not the open internet.

## License

MIT
