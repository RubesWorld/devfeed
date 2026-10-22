# HTML digest email: a scannable layout in place of the wall of text
Branch: agent/feat-html-digest · Base: main · Spec: none

## Goal
The first real digest arrived as one long column of plain text, with every link on a line of its own and the "Next" line repeating the first waiting item. This change sends the digest as `multipart/alternative`. The new HTML part follows the design picked from the preview: an uppercase repo heading with "N waiting", colored verb tags and titles that are links. The plain-text part stays as a fallback but is cut down in the same way. Standard library only, and it works in `--demo` and `--dry-run`.

## Done criteria
- [ ] `render_digest` builds one structured model, which both a text renderer and an HTML renderer read. `render_digest(...)` still returns `(subject, text_body)`, and a new `render_digest_html(...)` returns the HTML.
- [ ] HTML layout:
  - a header reading "devfeed · N need you · <date>"
  - one block per repo with waiting items: the uppercase name and "N waiting" on the right, a "Left off" row, then one row per waiting item with a colored tag (`review`/`fix`/`merge`/`issue`/`build`) and its title as a link
  - "Next" only when it isn't already the first waiting row
  - repos with nothing waiting collapsed into one linked "Also active: A, B, C" line
  - a compact "While you were away" section
  - no raw URLs in the visible text
- [ ] The HTML is email-safe: tables and inline styles, no external CSS, fonts or images. A `color-scheme` meta and a `prefers-color-scheme` block handle dark mode. Content is at most 600px wide and readable on a phone. Every title and name is escaped, and only `https://` links become hrefs.
- [ ] The text part drops the duplicate Next and collapses quiet repos into "Also active: …". It keeps a URL under each waiting item, so it still works on its own.
- [ ] The sent message is `multipart/alternative` (text/plain, then text/html, both UTF-8), with the same headers as before. `digest --demo --html` and `digest --dry-run --html` print the HTML. `--html` on a real send exits 1 with a one-line message.

## Edge cases → tests
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
- Looks right in real clients → manual: one real send to neburai5@gmail.com, viewed in the Gmail app (light + dark) and on desktop

## Slices
- [ ] 1. Split `render_digest` into a model builder (`digest_model`) plus a text renderer. Tighten the text: no duplicate Next, "Also active: …" for quiet repos, compact away section. Update the existing render tests to the new text. | files: devfeed.py, tests/test_digest.py | done: `python3 -m py_compile devfeed.py && python3 -m unittest discover tests && python3 devfeed.py digest --demo` | [local]
- [ ] 2. `render_digest_html(model)`: escaped, https-only links, table layout, dark mode, phone width. Add the `--html` flag and its checks, and make `build_message` multipart/alternative. Add tests/test_digest_html.py and extend Send.test_message_headers. | files: devfeed.py, tests/test_digest_html.py, tests/test_digest.py | done: `python3 -m py_compile devfeed.py && python3 -m unittest discover tests && python3 devfeed.py digest --demo --html > /dev/null` | [local]
- [ ] 3. Docs and CI: the README Digest section (HTML email, `--html` preview tip) and a CI smoke step that checks `digest --demo --html` outputs `<!doctype html>` with no `http://` hrefs | files: README.md, .github/workflows/check.yml | done: `python3 -m unittest discover tests && ruby -ryaml -e 'YAML.load_file(".github/workflows/check.yml")'` | [local]

## Log
