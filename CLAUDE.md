# devfeed

One page for what shipped and what's waiting across your GitHub repos. One
Python file (`devfeed.py`) plus one static page (`public/index.html`).

## Invariants

- **Standard library only, Python 3.9+.** No dependencies, ever: it must run
  from a plain `git clone` on a stock Mac. No `X | Y` type unions or `match`.
- **GitHub only through the `gh` CLI.** No tokens handled by devfeed itself.
- **Stateless.** `public/feed.json` is rewritten wholesale each run; on any
  fetch failure the previous feed stays untouched.
- **Nothing personal in this repo.** Owners, repos, boards and labels come from
  `config.json`. Defaults must make sense for a stranger.
- **The page shows private repo data to anyone who can reach it.** Never widen
  the default bind (`127.0.0.1`) or serve anything beyond `public/`.
- Every new feature works in `devfeed.py demo` (made-up data, no GitHub), so
  the live demo and CI cover it.

## Workflow
Tier: lean
Base branch: main
Checks: python3 -m py_compile devfeed.py && python3 -m unittest discover tests
Preview: none
Escalate: devfeed.py serve, bind or served paths, new deps
