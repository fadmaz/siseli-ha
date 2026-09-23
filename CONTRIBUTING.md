# Contributing

This file covers development setup, test conventions, how to contribute a capture from
your own inverter, and the release checklist. `siseli_bridge/DOCS.md` covers
installation and configuration for users -- Home Assistant renders it on the add-on's
**Documentation** tab, so it must never carry a version literal or it becomes a fifth
place for the version to drift. `README.md` is the project's landing page.

Keep this file current — a stale entry here is worse than none, because it is trusted.

## Development setup

```bash
pip install -e ".[dev]"
pip install -r siseli_bridge/requirements.txt
```

`scapy` needs libpcap headers (`sudo apt-get install libpcap-dev` on Debian/Ubuntu).
Without scapy installed, `core.py` cannot be imported and its tests will error.

Note the runtime import root is `siseli_bridge/`, so the top-level package is
literally `src` and modules are imported as `src.siseli_bridge.*`. That mirrors how
the add-on starts (`python3 -m src.siseli_bridge.core` from `/app`) and is why
`pyproject.toml` declares `package-dir` explicitly — auto-discovery finds nothing,
because `siseli_bridge/` has no `__init__.py`.

## Running the checks

```bash
pytest siseli_bridge/tests -q
ruff check .
```

Coverage floors, matching CI:

```bash
pytest siseli_bridge/tests --cov=siseli_bridge/src --cov-report=
python -m coverage report --fail-under=78
python -m coverage report --include="*/siseli_bridge/mqtt.py,*/siseli_bridge/core.py" --fail-under=65
```

A smoke test starts the built image and waits for it to report a running sniffer:

```bash
bash scripts/smoke-test.sh
```

Run it before releasing. Two releases shipped broken because "the image builds" and
"the tests pass" were both true of an add-on that could not start: the startup banner
lives in the `__main__` body that no test executes, in a file carrying a ruff `F405`
exemption. Only starting the container catches that class of fault.

`ruff check` runs a deliberately narrow, defect-only rule set (pyflakes, bugbear,
syntax errors). There is no formatter and none is wanted: reformatting would rewrite
~2,500 lines in one commit and destroy `git blame` on the parser, which is where the
project's hard-won knowledge lives.

## Writing tests

Tests are `unittest.TestCase` throughout; pytest is only the runner. Shared
machinery lives in two modules:

- **`tests/helpers.py`** — behaviour: `BASE_ENV`/`patched_env`, `reload_config`,
  `patch_consts`, `isolated_state`, `capture_logs`, `FakeMqttClient`, and builders
  for MQTT packets, TCP segments and inverter payload envelopes.
- **`tests/captures.py`** — data only, no imports, no logic.

Two rules that are easy to get wrong:

1. **Patch config constants on the *consuming* module.** `core.py`, `mqtt.py` and
   `parsers.py` all do `from .config import *`, so they hold bound copies. Reloading
   `config.py` does not change them. Use
   `patch_consts("src.siseli_bridge.parsers", INVERTER_COUNT=2)`.
2. **Wrap anything that touches module globals in `isolated_state()`.** `LAST_STATE`,
   `FLOW_STATES`, `LAST_ENERGY_TS` and friends leak between tests otherwise, and the
   failure shows up as an unrelated test failing depending on run order.

3. **A reload is not undone by the test that did it.** `importlib.reload` on
   `core.py`, `mqtt.py` or `parsers.py` re-binds every config copy from whatever
   `config.py` holds at that moment, and `reload_config` leaves it holding the last
   override a config test used. Reload `config.py` under `patched_env()` first, or the
   module keeps those values for the rest of the session.

### The behavioural golden

`tests/test_device_a_golden.py` records what the reference installation produces --
every MQTT publish in order with its payload, retain flag and QoS, every log line,
`state.json`, and `LAST_STATE` in insertion order -- across five runs of the real
startup sequence. It exists because `docs/PI30_DESIGN.md` promises existing installs
stay byte-for-byte unchanged, and no other test asserts bytes.

It fails, rather than skips, on any difference: it imposes `BASE_ENV` on the three
consuming modules instead of reading the ambient environment. When a change to it is
intended:

```bash
SISELI_REGEN_GOLDEN=1 py -3.12 -m pytest siseli_bridge/tests/test_device_a_golden.py
```

Then **read the diff** and say in the commit message what changed and who sees it.
Regenerating to make a failure go away is how a golden becomes worthless.

Some tests are marked `CURRENT BEHAVIOUR` in their docstring. Those pin a known
defect deliberately, so that fixing it shows up as a reviewed diff rather than a
silent change. If one fails because you fixed the underlying bug, update the test in
the same commit and say so.

## Adding a capture from a user's inverter

This is the highest-value contribution for supporting a new device.

1. Ask the reporter to set **Debug Flags** to `blocks` and `unparsed_publish`, with
   **Log Level** at `info`, for **two minutes**, then clear the flags again. Output is
   per-packet and lands on the SD card. (`LOG_LEVEL: debug` also works but floods the
   log with everything else; the flags select exactly the lines you need. The issue
   template, `captures/README.md` and the Documentation tab all say flags + `info`.)
2. Ask for the `[BLOCK RAW]` lines. Have them scrub the `topic=` values (the Siseli
   cloud topic embeds the device serial) and `firmware_info` if they prefer.
3. Take the `hex_preview` field, decode it, and paste the result into
   `tests/captures.py` verbatim as a `BLOCK_*` constant. The framing is exact: a
   leading `(` and a trailing `\r`, no closing paren.
4. Record the model and firmware in a comment, and name the constant after the block
   key plus the device state it was captured in.

A capture worth keeping as *reference* rather than as a fixture goes in
[`captures/`](captures/) instead — a full comparison of the bridge's decode against the
vendor portal, read at the same moment. [`captures/README.md`](captures/README.md) has
the procedure and a table of which device states would settle which open questions.
Fixtures prove the parser does not regress; reference captures are the only way to find
a field that is *wrong* rather than merely missing.

Hand-built blocks go under `SYNTH_*` and must never be used for value-parity
assertions — a synthetic block proves nothing about what a device actually emits.

## Releasing

The version has one source of truth and two copies of it, because Supervisor reads
`config.yaml` directly and cannot import Python:

- `siseli_bridge/src/siseli_bridge/version.py` — the source of truth
- `siseli_bridge/config.yaml` — what Supervisor reads
- the README badge and the changelog heading

Nothing else may carry a version literal. `run.sh` used to print one and it froze two
releases behind, so every add-on log contradicted the banner `core.py` prints seconds
later — and a maintainer triaging a pasted log diagnosed the wrong release.
`test_packaging.py` now forbids it there outright.

`tests/test_packaging.py` fails until those two, the README badge, and the heading at
the top of the add-on changelog all agree. The failure mode it exists to prevent is
asymmetric: if `config.yaml` lags, Supervisor never offers the update while the log
claims the new version is running.

A release is a single commit touching: `siseli_bridge/CHANGELOG.md` (the canonical
one -- the root file is just a pointer), `version.py`, `config.yaml`, and the README
badge.

**Fold `## [Unreleased]` into the new section; never insert the new heading above it.**
Rename it in place to `## [X.Y.Z] - YYYY-MM-DD`. Inserting a heading above it once
stranded two shipped entries outside the release notes (ef6c8e4), and
`test_changelog_head_matches` cannot see that -- it reads only headings that start with a
digit. Then **reread the section's opening paragraph**, not only its bullets: 2.6.25's
began "Tests and internals only. No behaviour change", carried over from when it held
test work alone, and would have shipped as the preamble of a release that changed a log
line. Nothing mechanical checks prose.

**Merging is not releasing.** Once the release PR is merged, tag its merge commit and
publish a GitHub release whose notes are that version's changelog section:

```bash
gh release create vX.Y.Z --repo fadmaz/siseli-ha \
    --target <merge commit of the release PR> \
    --title X.Y.Z --notes-file <the section, without its heading> --latest
```

Tag the release PR's merge commit, not whatever `main` is by the time you get to it --
later docs-only merges would otherwise be tagged as part of a release they are not in.

This step is easy to skip precisely because skipping it breaks nothing you can see.
Supervisor installs from the default branch, so the add-on updates without it and your
own install looks fine. But the Releases page is where everyone else looks: 2.6.25 was
merged and installable while that page still called 2.6.24 the latest, and the reporter
it had been built for concluded, reasonably, that it was not out.

Changelog entries follow Keep a Changelog: `## [X.Y.Z] - YYYY-MM-DD`, then
`### Added` / `### Changed` / `### Fixed`, with bullets shaped
`- **Bold Title Case Label**: Sentence.` and identifiers in backticks.
