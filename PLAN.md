# #3 Morning digest: a re-entry email built from the feed
Branch: agent/feat-3-morning-digest · Base: main · Spec: https://github.com/RubesWorld/devfeed/issues/3

## Done criteria
- [x] `devfeed.py` accepts `digest` with `--dry-run` and `--demo` flags. The argparse choices and the module docstring list it.
- [x] A pure function, e.g. `render_digest(feed, since, now)` → `(subject, body)`, holds all the formatting, with no I/O, so tests can call it directly.
- [x] Active-repo filter, ordering, Left off / Next / Waiting rules and the fix/merge/review verbs all behave as described above.
- [x] The "while you were away" window starts at the stored `last_window_end`, or 24h before the feed's `generated_at` when there is no usable state. It ends at the feed's `generated_at`.
- [x] The one-line body applies when nothing is waiting on you.
- [x] The message is built with `email.message.EmailMessage`, `text/plain; charset=utf-8`, with `From`, `To`, `Subject`, `Date` and `Message-ID` set.
- [x] SMTP: port 465 uses `smtplib.SMTP_SSL`, any other port uses `SMTP` + `starttls()`, then `login`, with a 30s timeout. Defaults are `port` 587 and `from` = `user`. `to` is a string or a list.
- [x] Password comes from env `DEVFEED_SMTP_PASSWORD` first, then `digest.smtp.password`. It is never logged or printed, including in exception messages.
- [x] `digest-state.json` lives in the same directory as the config file in use (never in `public/`). It is written atomically (tmp + `os.replace`), and only after `send_message` returns without error. It holds `{"last_window_end": "<feed generated_at>", "last_sent_at": "<UTC now>"}`.
- [x] `.gitignore` gains `digest-state.json` and `digest-state.json.tmp`.
- [x] `config.example.json` gains a `digest` block with placeholder values (`to`, `smtp.host` `smtp.gmail.com`, `port` 587, `user`), no password and `active_days` 7.
- [x] Running `digest` (without `--dry-run` or `--demo`) when there is no `digest` block, or `to`, `smtp.host` or `smtp.user` is missing, or no password is available: exit 1 with a one-line message naming the missing key.
- [x] `examples/crontab.txt` gains a daily `30 7 * * *` digest line, with a note on setting `DEVFEED_SMTP_PASSWORD` in the crontab. New `examples/com.example.devfeed-digest.plist` uses `StartCalendarInterval` Hour 7 Minute 30, with no Weekday key, so it runs daily.
- [x] README gets a Digest section: config, Gmail app password, scheduling, and a warning that the email contains private repo titles.
- [x] `tests/test_digest.py` covers: render from `demo_feed()`, the one-line case, the window filter, the verb choice, no-board feed, unicode titles, truncation, the stale-feed first line and state-file read fallbacks.
- [ ] `python3 -m py_compile devfeed.py && python3 -m unittest discover tests` passes on 3.9 and 3.13. (3.9.6 and 3.14 pass locally; 3.13 is confirmed by CI on the PR)

## Edge cases → tests
- First run / state file missing → window = generated_at − 24h → tests/test_digest.py::State.test_missing_state_falls_back_24h
- State malformed / unreadable / unparsable timestamp → treated as missing, one log line → tests/test_digest.py::State.test_malformed_state_falls_back
- State timestamp in the future → treated as missing → tests/test_digest.py::State.test_future_state_falls_back
- Long gap → heading shows the real "since" date, window covers all feed items since then → tests/test_digest.py::Render.test_long_window_heading_uses_real_since
- Send fails (auth, network, refused recipient, timeout) → exit 1, no password in log, state untouched → tests/test_digest.py::Send.test_send_failure_leaves_state_and_hides_password (smtplib mocked)
- GitHub fetch fails, feed.json exists → "Couldn't refresh from GitHub; this is Xh old" first line → tests/test_digest.py::Render.test_stale_first_line
- No feed.json and fetch fails → nothing sent, exit 1 → tests/test_digest.py::Command.test_no_feed_exits_1
- Overlapping runs → atomic state write → tests/test_digest.py::State.test_write_is_atomic (tmp + os.replace, no stray tmp)
- PR opened and merged in window → only a merge (falls out of data shape) → manual: covered by window-filter test data
- Card moved several times → shown once "#N → Status" → tests/test_digest.py::Render.test_board_move_shown_once
- `board` null / no `next_up` → skip board parts → tests/test_digest.py::Render.test_no_board
- Attention card in a quiet repo → repo listed → tests/test_digest.py::Render.test_attention_card_lists_quiet_repo
- Draft PRs → never waiting, Left off falls back to newest item → tests/test_digest.py::Render.test_draft_not_waiting
- Empty feed → one-line body → tests/test_digest.py::Render.test_empty_feed_one_line
- Many items → 5 waiting / 10 away per repo, 15 repos, `+N more` → tests/test_digest.py::Render.test_caps
- Long titles → 80 chars with `…`, URLs never cut → tests/test_digest.py::Render.test_truncation
- Unicode titles / display names → UTF-8 body, non-ASCII subject OK → tests/test_digest.py::Render.test_unicode + Send.test_message_headers
- Malformed `to` → send-failure path → covered by Send.test_send_failure_leaves_state_and_hides_password (SMTPRecipientsRefused)
- Time: UTC comparisons, local display → tests/test_digest.py::Render.test_window_filter (aware datetimes)
- Demo mode → fixed 24h window, no I/O → tests/test_digest.py::Command.test_demo_prints_without_config
- Missing config keys / password → exit 1 naming the key → tests/test_digest.py::Command.test_missing_key_messages
- State file never served → manual: `curl http://127.0.0.1:8300/digest-state.json` → 404 (state lives next to config, not in public/)

## Slices
- [x] 1. `render_digest(feed, since, now, stale_hours=None)` pure renderer + `digest --demo` + argparse/docstring + tests (render from demo, one-line, window filter, verbs, no board, unicode, truncation, caps, stale line, drafts, attention card) | files: devfeed.py, tests/test_digest.py | done: `python3 -m py_compile devfeed.py && python3 -m unittest discover tests && python3 devfeed.py digest --demo` | [local]
- [x] 2. `digest` and `digest --dry-run`: config validation, state read (fallbacks) / atomic write, `EmailMessage` build, SMTP send (465→SMTP_SSL, else STARTTLS, 30s, password env-first, scrubbed from errors), stale-feed fallback, exit codes; tests with smtplib mocked | files: devfeed.py, tests/test_digest.py | done: `python3 -m py_compile devfeed.py && python3 -m unittest discover tests` | [local] ⚠️ email
- [x] 3. Docs and wiring: `config.example.json` digest block, `.gitignore`, `examples/crontab.txt` line, new `examples/com.example.devfeed-digest.plist`, README Digest section, CI smoke step `python devfeed.py digest --demo` | files: config.example.json, .gitignore, examples/crontab.txt, examples/com.example.devfeed-digest.plist, README.md, .github/workflows/check.yml | done: `python3 -m unittest discover tests && plutil -lint examples/com.example.devfeed-digest.plist && python3 -c "import json; assert json.load(open('config.example.json'))['digest']['active_days'] == 7"` | [local]

## Log
- Spec checked against 9477289: 0 commits since the spec, none conflict.
- Spec mismatch to resolve in slice 1: Verify step 1 says Docs is not listed in `--demo`, but demo Docs' last activity is 120h (5 days), inside the default 7-day window. Plan: move the demo Docs push to ~10 days old (240h) so it's quiet in the digest; the page still shows it (page `active_days` is 14).
- local-candidate: slice 1 | first-try pass: no | ~375 lines
- Slice 1 choices: the 80-char cut applies to titles only, not the "verb #N" prefix. When several PRs are waiting, Next is the first non-draft PR in feed order. A stale-feed first line goes above the one-line body too, so that email has two lines.
- local-candidate: slice 2 | first-try pass: yes | ~360 lines
- Slice 2 choices: `digest` checks the config before collecting, so a missing key fails fast. `--dry-run` reads the state file (for a realistic window) but never writes it. Send errors are logged as `<ExceptionType>: <message>` with the password replaced by `***`.
- local-candidate: slice 3 | first-try pass: yes | ~95 lines
- Done criteria verified locally on Python 3.9.6 and 3.14.7; CI runs the 3.9/3.13 matrix on the PR.
