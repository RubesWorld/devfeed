# HTML digest email: a scannable layout in place of the wall of text
Branch: agent/feat-html-digest · Base: main · Spec: none

## Goal
The first real digest arrived as one long column of plain text, with every link on a line of its own and the "Next" line repeating the first waiting item. This change sends the digest as `multipart/alternative`. The new HTML part follows the design picked from the preview: an uppercase repo heading with "N waiting", colored verb tags and titles that are links. The plain-text part stays as a fallback but is cut down in the same way. Standard library only, and it works in `--demo` and `--dry-run`.

## Done criteria
- [ ] `render_digest` builds one structured model, which both a text renderer and an HTML renderer read. `render_digest(...)` still returns `(subject, text_body)`, and a new `render_digest_html(...)` returns the HTML.
- [ ] HTML layout matches the page's dark Feed view, using the same tokens as `public/index.html`:
  - colors: page `#0d0d0d`, surface `#1a1a19`, border `rgba(255,255,255,.10)`, ink `#fff` / `#c3c2b7`, muted `#898781`
  - fonts: Space Grotesk then the system stack; IBM Plex Mono then `ui-monospace` for the meta text and tags
  - each repo's slot color, from the page's `SLOT_DARK`
- [ ] HTML content:
  - a header with the ◆ mark, "Dev Feed", the date and a "N need you" pill (red when N > 0, green when all clear)
  - one surface card per repo with waiting items: a 3px left bar in the slot color, the repo name with "N waiting" on the right, a "Left off" row, then one row per waiting item
  - each waiting row has a bordered mono tag like the page's pills (`review` blue `#3987e5`, `fix` red `#d03b3b`, `merge` green `#0ca30c`, `needs you` amber `#c98500`, `build` muted), the title as a link, and a `#ref` badge tinted with the slot color
  - "Next" only when it isn't already the first waiting row
  - repos with nothing waiting collapsed into one "Also active: A, B, C" line, with the names linked
  - a compact "While you were away" section
  - no raw URLs in the visible text
  - optional: when `digest.page_url` is set, an "open devfeed ↗" link in the header; it's left out when unset, and `config.example.json` leaves it out
- [ ] The HTML is email-safe:
  - tables, inline styles and `bgcolor`, so the dark background survives in Gmail
  - no external CSS, fonts or images
  - `color-scheme: dark` meta, since the email is always dark like the Feed view
  - content at most 600px wide and readable on a phone
  - every title and name escaped, and only `https://` links become hrefs
- [ ] The text part drops the duplicate Next and collapses quiet repos into "Also active: …". It keeps a URL under each waiting item, so it still works on its own.
- [ ] The sent message is `multipart/alternative` (text/plain, then text/html, both UTF-8), with the same headers as before. `digest --demo --html` and `digest --dry-run --html` print the HTML. `--html` on a real send exits 1 with a one-line message.

## Edge cases → tests
- `gh repo list` returns [] for any owner → collect fails, previous feed kept → tests/test_discover.py::Discover.test_empty_listing_raises, test_empty_listing_for_one_owner_raises, test_empty_listing_keeps_previous_feed
- Titles with `<script>`, `&`, quotes and an `"onmouseover` attempt are escaped in text and attributes → tests/test_digest_html.py::Html.test_escapes_titles_and_attrs
- A non-https URL in the feed is shown as plain text with no href → tests/test_digest_html.py::Html.test_only_https_links
- One-line case (nothing waiting) gives a short HTML note, and the stale line comes first → tests/test_digest_html.py::Html.test_one_line_and_stale
- Next duplicates the first waiting row → omitted in text and HTML; a build-next card is still shown → tests/test_digest.py::Render.test_next_not_repeated + Html.test_next_not_repeated
- Quiet active repos are collapsed and linked; quiet inactive repos are left out → tests/test_digest.py::Render.test_quiet_repos_collapsed + Html.test_also_active
- Caps and `+N more` match the text part → tests/test_digest_html.py::Html.test_caps
- Size stays well under Gmail's 102KB clip on a worst-case feed (15 repos × 5 waiting, long titles) → tests/test_digest_html.py::Html.test_size_under_gmail_clip
- Unicode titles and names come through in the HTML and the MIME part → tests/test_digest_html.py::Html.test_unicode
- No board, or no `next_up` → tests/test_digest_html.py::Html.test_no_board
- MIME structure: alternative with plain then html, both UTF-8, the text part decodes to render_digest's body → tests/test_digest.py::Send.test_message_headers (extended)
- `--html` without `--demo`/`--dry-run` → exit 1 → tests/test_digest_html.py::Command.test_html_needs_preview
- Demo HTML renders without config or GitHub → tests/test_digest_html.py::Command.test_demo_html
- `digest.page_url` unset → no header link; set → escaped https link → tests/test_digest_html.py::Html.test_page_url
- Looks right in real clients → manual: one real send to neburai5@gmail.com, viewed in the Gmail app (light + dark) and on desktop

## Slices
- [x] 0. Fix found mid-plan: an empty `gh repo list` for an owner raises, so `collect` keeps the previous feed (on 2026-10-22 13:03 it replaced the live feed with pins only) | files: devfeed.py, tests/test_discover.py | done: `python3 -m py_compile devfeed.py && python3 -m unittest discover tests` | [local]
- [x] 1. Split `render_digest` into a model builder (`digest_model`) plus a text renderer. Tighten the text: no duplicate Next, "Also active: …" for quiet repos, compact away section. Update the existing render tests to the new text. | files: devfeed.py, tests/test_digest.py | done: `python3 -m py_compile devfeed.py && python3 -m unittest discover tests && python3 devfeed.py digest --demo` | [local]
- [x] 2. `render_digest_html(model)`: escaped, https-only links, table layout, dark mode, phone width. Add the `--html` flag and its checks, and make `build_message` multipart/alternative. Add tests/test_digest_html.py and extend Send.test_message_headers. | files: devfeed.py, tests/test_digest_html.py, tests/test_digest.py | done: `python3 -m py_compile devfeed.py && python3 -m unittest discover tests && python3 devfeed.py digest --demo --html > /dev/null` | [local]
- [x] 3. Docs and CI: the README Digest section (HTML email, `--html` preview tip, optional `digest.page_url`) and a CI smoke step that checks `digest --demo --html` outputs `<!doctype html>` with no `http://` hrefs | files: README.md, .github/workflows/check.yml | done: `python3 -m unittest discover tests && ruby -ryaml -e 'YAML.load_file(".github/workflows/check.yml")'` | [local]

## Log
- Slice 0 added at the user's request after the live feed dropped to Nao only (13:03): an empty repo list from GitHub slipped past the fetch-failure check. Built and committed before the HTML slices.
- local-candidate: slice 1 | first-try pass: no | ~200 lines
- Slice 1 choices: a repo with no waiting items still gets a full block when it has a build-next card, because that's an action. A waiting PR is always the first waiting row, so Next now only names a build-next card. The text part keeps URLs on waiting rows and Next only; Left off and away lines drop them (the HTML links them).
- Slice 2 stopped for review: `Html.test_size_under_gmail_clip` fails, 88,809 bytes against my 80,000 limit, on a synthetic worst case (15 repos x 5 waiting rows with 80-char titles, 25 away lines). Tried: (1) a 25-line total cap on the away section, the sans stack set once on the wrapper, and rows laid out flat in the card table (210KB to 103KB); (2) a shorter chip font stack, nowrap attributes, lighter chips (103KB to 89KB). Gmail clips at 102KB. Everything else in slice 2 passes (89 tests, 1 failing). Uncommitted. Touched tests/test_digest_hostile.py (outside the slice's files): its stale-email test now reads the plain part of the now-multipart message.
- User chose: accept the size; the test limit is now 95,000 bytes (Gmail clips at 102KB). Real digest from the live config: 22,267 bytes.
- local-candidate: slice 2 | first-try pass: no | ~410 lines
- Slice 2 choices: `render_digest_html` takes the same arguments as `render_digest` plus `page_url`, not a model. `digest.page_url` may be http:// because the owner's own page is often on a LAN/Tailscale address; feed links stay https-only. A total cap of 25 away lines (DIGEST_AWAY_TOTAL) applies to text and HTML alike. Chip labels use &nbsp; so they don't wrap.
- local-candidate: slice 3 | first-try pass: yes | ~20 lines
