"""Guards for the invariants that previously had to be checked by hand.

Every failure here is something that used to reach users silently: a version that
Supervisor never offers because config.yaml lagged, an option the UI accepts that
makes validate_config() exit, or an option documented in one file and missing from
another.
"""

import pathlib
import re
import unittest

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
ADDON = ROOT / "siseli_bridge"
CONFIG_PY = ADDON / "src" / "siseli_bridge" / "config.py"


def _load_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8"))


class TestVersionConsistency(unittest.TestCase):
    """The release version lives in more than one file because Supervisor reads
    config.yaml directly and cannot import Python. These must never drift: if
    config.yaml lags, the update is never offered while the log claims otherwise."""

    def setUp(self):
        from src.siseli_bridge.version import __version__

        self.version = __version__

    def test_config_yaml_matches(self):
        self.assertEqual(_load_yaml(ADDON / "config.yaml")["version"], self.version)

    def test_readme_badge_matches(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"version-{self.version}-blue", readme)

    def test_changelog_head_matches(self):
        """The add-on copy is canonical -- it is what Supervisor renders on the add-on's
        Changelog tab, and .dockerignore already re-includes only that file."""
        heads = re.findall(
            r"^## \[([0-9][^\]]*)\]", (ADDON / "CHANGELOG.md").read_text(encoding="utf-8"), re.M
        )
        released = [h for h in heads if h.lower() != "unreleased"]
        self.assertTrue(released, "the add-on changelog has no released version heading")
        self.assertEqual(released[0], self.version)

    def test_the_root_changelog_points_at_the_canonical_one(self):
        """The two used to be maintained by hand and had already drifted apart."""
        text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
        self.assertIn("siseli_bridge/CHANGELOG.md", text)
        self.assertNotRegex(text, r"^## \[[0-9]", "the root file must not carry its own history")

    def test_run_sh_carries_no_version_literal(self):
        """run.sh printed a hardcoded banner that froze at 2.6.5 while the add-on
        shipped 2.6.7, so every log contradicted itself in its own header and a bug
        report quoted a release the user was not running. Forbidden outright rather
        than kept in sync: bumping it just moves the drift to the next release."""
        run_sh = (ADDON / "run.sh").read_text(encoding="utf-8")
        self.assertNotRegex(
            run_sh,
            r"Siseli Inverter Bridge\s+[0-9]",
            "run.sh must not print a version; core.log_startup_configuration() does",
        )

    def test_core_reports_the_single_source(self):
        from src.siseli_bridge import version as version_mod

        core_src = (ADDON / "src" / "siseli_bridge" / "core.py").read_text(encoding="utf-8")
        self.assertNotRegex(
            core_src,
            r'^VERSION\s*=\s*["\']',
            "core.py must import __version__, not re-declare a literal",
        )
        self.assertEqual(version_mod.__version__, self.version)


class TestOptionWiring(unittest.TestCase):
    """Every add-on option must exist in options, schema, translations, run.sh and
    config.py. A gap in any one of them is invisible until a user hits it."""

    def setUp(self):
        self.cfg = _load_yaml(ADDON / "config.yaml")
        self.translations = _load_yaml(ADDON / "translations" / "en.yaml")
        self.run_sh = (ADDON / "run.sh").read_text(encoding="utf-8")
        self.config_py = CONFIG_PY.read_text(encoding="utf-8")

    def test_options_and_schema_agree(self):
        self.assertEqual(set(self.cfg["options"]), set(self.cfg["schema"]))

    def test_every_option_is_documented(self):
        self.assertEqual(set(self.cfg["schema"]), set(self.translations["configuration"]))

    def test_every_option_is_exported_by_run_sh(self):
        for key in sorted(self.cfg["schema"]):
            with self.subTest(key=key):
                self.assertIn(f"bashio::config '{key}'", self.run_sh)

    def test_every_option_is_read_by_config_py(self):
        for key in sorted(self.cfg["schema"]):
            with self.subTest(key=key):
                self.assertIn(f'os.getenv("{key}"', self.config_py)


class TestSchemaValidatorParity(unittest.TestCase):
    """A value the options UI accepts must not make validate_config() sys.exit.

    Before this test, UPDATE_INTERVAL_SEC was declared int(0,) while the validator
    rejected anything below 1 -- so a UI-legal 0 put the add-on in a restart loop
    with the options page still showing the value as valid.
    """

    def setUp(self):
        self.schema = _load_yaml(ADDON / "config.yaml")["schema"]

    def test_update_interval_lower_bound_matches_validator(self):
        low = re.match(r"int\((\d+),", self.schema["UPDATE_INTERVAL_SEC"])
        self.assertIsNotNone(low, "UPDATE_INTERVAL_SEC must declare a lower bound")
        self.assertGreaterEqual(int(low.group(1)), 1)

    def test_no_ui_legal_value_is_rejected_by_the_validator(self):
        """Drive validate_config() with the minimum each bounded int option allows."""
        from tests.helpers import reload_config

        minima = {}
        for key, decl in self.schema.items():
            m = re.match(r"int\((\d+),", str(decl))
            if m:
                minima[key] = m.group(1)
        self.assertIn("UPDATE_INTERVAL_SEC", minima, "expected at least one bounded int option")

        cfg = reload_config(**minima)
        try:
            cfg.validate_config()
        except SystemExit as exc:  # pragma: no cover - only on regression
            self.fail(f"schema minima {minima} rejected by validate_config: {exc}")


class TestPackagingMetadata(unittest.TestCase):
    def test_build_backend_is_real(self):
        """`setuptools.backends.legacy:build` does not exist -- it made every
        `pip install .` fail at the PEP 517 hook."""
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn('build-backend = "setuptools.build_meta"', text)

    def test_packages_are_declared_explicitly(self):
        """siseli_bridge/ has no __init__.py, so auto-discovery finds nothing and
        would build an empty wheel."""
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertIn("[tool.setuptools]", text)
        self.assertIn('packages = ["src", "src.siseli_bridge"]', text)

    def test_an_spdx_licence_carries_a_build_floor_that_understands_it(self):
        """PEP 639 metadata -- `license = "MIT"` as a bare string, plus `license-files`
        -- is a schema error on setuptools 61-76, raised inside the PEP 517 hook. It
        does not degrade: `pip install -e ".[dev]"` fails outright, before a single
        test runs, on every interpreter in the matrix. The two must move together."""
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        if not re.search(r"^license\s*=\s*\"", text, re.M):
            self.skipTest("no bare-string licence declared")
        self.assertRegex(text, r"setuptools>=(7[7-9]|[89]\d|\d{3,})")

    def test_the_licence_files_named_in_pyproject_exist(self):
        """`license-files` narrows setuptools' default glob rather than adding to it,
        so a typo here silently ships a wheel with no licence at all."""
        text = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        named = re.search(r"^license-files\s*=\s*\[([^\]]*)\]", text, re.M)
        self.assertIsNotNone(named, "license-files is not declared")
        for name in re.findall(r'"([^"]+)"', named.group(1)):
            with self.subTest(licence_file=name):
                self.assertTrue((ROOT / name).is_file(), f"{name} is named but absent")


class TestDocumentationSplit(unittest.TestCase):
    """The configuration reference lived in README.md until 2.6.13, and Supervisor's
    Documentation tab -- which renders siseli_bridge/DOCS.md -- was empty, so users were
    bounced to GitHub. Both halves of that are pinned here: the reference lives on the
    tab, and the README must not grow a second copy of it. The same treatment that
    stopped the root changelog re-growing its own history."""

    MOVED = {"Requirements", "Installation", "Configuration", "Network setup", "Troubleshooting"}

    def setUp(self):
        from src.siseli_bridge.version import __version__

        self.version = __version__
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")
        self.docs = (ADDON / "DOCS.md").read_text(encoding="utf-8")
        self.schema = _load_yaml(ADDON / "config.yaml")["schema"]

    def test_the_moved_sections_live_in_exactly_one_file(self):
        readme_heads = set(re.findall(r"^## (.+?)\s*$", self.readme, re.M))
        docs_heads = set(re.findall(r"^## (.+?)\s*$", self.docs, re.M))
        self.assertEqual(
            self.MOVED & readme_heads, set(), "a section that belongs on the Documentation tab is back in the README"
        )
        self.assertLessEqual(self.MOVED, docs_heads, "a section left the README without arriving in DOCS.md")

    def test_the_readme_carries_no_option_reference_table(self):
        """The duplication vector is a markdown table row whose first cell is a
        backticked option name -- README.md carried forty of them. Prose that mentions
        an option is fine and expected, so this matches the table shape, not the name."""
        rows = re.findall(r"^\|\s*`([A-Z][A-Z0-9_]*)`", self.readme, re.M)
        self.assertEqual(rows, [], f"option reference rows are back in the README: {rows}")

    def test_every_shipped_option_is_documented_on_the_tab(self):
        """The invariant a user actually feels: an option in the schema that nothing
        explains. test_every_option_is_* already ties the schema to translations, run.sh
        and config.py; this makes the Documentation tab the last required stop."""
        for key in sorted(self.schema):
            with self.subTest(option=key):
                self.assertIn(f"`{key}`", self.docs, f"{key} is shipped but undocumented")

    def test_the_readme_points_at_the_tab(self):
        self.assertIn("siseli_bridge/DOCS.md", self.readme)

    def test_docs_carries_no_current_version_literal(self):
        """DOCS.md is not in the release checklist, so a version written here goes stale
        silently -- it held a sample startup banner reading `2.6.12` when it was split
        out. References to *earlier* releases ("if you installed a version before 2.6.6")
        are deliberate history and stay; only the version being shipped is forbidden."""
        self.assertNotIn(self.version, self.docs)

    def test_the_docs_tab_uses_absolute_links(self):
        """Supervisor renders this file inside the Home Assistant frontend, where a
        relative link resolves against the HA origin and 404s. The same links are also
        wrong on GitHub, where they would resolve against siseli_bridge/ rather than the
        repository root."""
        links = re.findall(r"\]\(([^)#][^)]*)\)", self.docs)
        relative = [link for link in links if not link.startswith(("http://", "https://", "#"))]
        self.assertEqual(relative, [], f"relative links break on the Documentation tab: {relative}")


class TestArchitectureDocAnchors(unittest.TestCase):
    """docs/ARCHITECTURE.md cites ~185 file:line anchors, and nothing read them until
    this. Half the sampled anchors had already drifted one working day after it was
    written -- inserting thirty lines into parsers.py silently moved every citation
    below them. A map whose line numbers are wrong is worse than one without any.

    This pins the subset that can be resolved mechanically: wherever the prose names a
    backticked symbol just before a citation, that symbol must actually be defined on
    the cited line. It also refuses a citation whose file does not exist or whose line
    is past the end. It cannot pin a citation that points at a statement rather than a
    definition; the document's header says so.
    """

    DOC = ROOT / "docs" / "ARCHITECTURE.md"
    BARE = {
        "core.py": "siseli_bridge/src/siseli_bridge/core.py",
        "parsers.py": "siseli_bridge/src/siseli_bridge/parsers.py",
        "mqtt.py": "siseli_bridge/src/siseli_bridge/mqtt.py",
        "sensors.py": "siseli_bridge/src/siseli_bridge/sensors.py",
        "state.py": "siseli_bridge/src/siseli_bridge/state.py",
        "config.py": "siseli_bridge/src/siseli_bridge/config.py",
        "loggers.py": "siseli_bridge/src/siseli_bridge/loggers.py",
        "version.py": "siseli_bridge/src/siseli_bridge/version.py",
        "DOCS.md": "siseli_bridge/DOCS.md",
        "config.yaml": "siseli_bridge/config.yaml",
        "run.sh": "siseli_bridge/run.sh",
        "requirements.txt": "siseli_bridge/requirements.txt",
        "translations/en.yaml": "siseli_bridge/translations/en.yaml",
        "CHANGELOG.md": "siseli_bridge/CHANGELOG.md",
    }

    def setUp(self):
        if not self.DOC.is_file():
            self.skipTest("docs/ARCHITECTURE.md is not present")
        self.doc = self.DOC.read_text(encoding="utf-8")

    def _resolve(self, cited):
        rel = self.BARE.get(cited, cited)
        if rel.startswith("tests/"):
            rel = "siseli_bridge/" + rel
        path = ROOT / rel
        return path if path.is_file() else None

    def test_every_cited_file_exists_and_every_line_is_in_range(self):
        cites = re.findall(
            r"`([A-Za-z0-9_./-]+\.(?:py|md|yml|yaml|sh|txt|toml)):(\d+)(?:-(\d+))?`", self.doc
        )
        self.assertGreater(len(cites), 100, "the citations have vanished from the map")
        for cited, low, high in cites:
            with self.subTest(citation=f"{cited}:{low}"):
                path = self._resolve(cited)
                self.assertIsNotNone(path, f"{cited} does not resolve to a file")
                total = len(path.read_text(encoding="utf-8", errors="replace").splitlines())
                self.assertLessEqual(
                    int(high or low), total, f"{cited}:{high or low} is past the end ({total} lines)"
                )

    def test_a_cited_symbol_is_defined_on_the_line_it_cites(self):
        """The check that actually catches drift. In-range proves nothing on a 2000-line
        file; this compares the citation against the code."""
        pattern = re.compile(
            r"`([A-Za-z_]\w+)`.{0,200}?`([A-Za-z0-9_.]+\.py):(\d+)"
        )
        checked = 0
        for symbol, cited, line_no in pattern.findall(self.doc):
            path = self._resolve(cited)
            if path is None:
                continue
            lines = path.read_text(encoding="utf-8").splitlines()
            defined = {}
            for i, text in enumerate(lines, 1):
                found = re.match(r"\s*(?:def|class)\s+(\w+)", text) or re.match(
                    r"(\w+)(?::[^=]+)?\s*=", text
                )
                if found:
                    defined.setdefault(found.group(1), i)
            if symbol not in defined:
                continue
            checked += 1
            with self.subTest(symbol=symbol, citation=f"{cited}:{line_no}"):
                self.assertEqual(
                    int(line_no),
                    defined[symbol],
                    f"{cited}:{line_no} is cited for `{symbol}`, which is defined at "
                    f"line {defined[symbol]}",
                )
        self.assertGreater(checked, 15, "no citations could be resolved to a symbol")


class TestDiagnosticVocabularyIsDocumented(unittest.TestCase):
    """The diagnostic's own words are the user's only handle on an unsupported device,
    and 2.6.17 shipped a value the docs then contradicted: body="binary" was called the
    strongest signal of a foreign protocol, which mis-triaged a plainly-textual device
    in issue #32. Every shape the code can emit must be explained where the user reads."""

    def test_every_body_shape_appears_in_the_docs(self):
        docs = (ADDON / "DOCS.md").read_text(encoding="utf-8")
        source = (ADDON / "src" / "siseli_bridge" / "parsers.py").read_text(encoding="utf-8")
        shapes = set(re.findall(r'return "(ascii\+binary_tail|ascii|binary)"', source))
        self.assertEqual(shapes, {"ascii", "ascii+binary_tail", "binary"})
        for shape in sorted(shapes):
            with self.subTest(shape=shape):
                self.assertIn(f"`{shape}`", docs, f"DOCS.md does not explain body={shape}")


class TestDocumentedCountsAreCurrent(unittest.TestCase):
    """Three counts in the README are derived from the sensor registry and were updated
    by hand. "Around 38 sensors read Unknown" was written when the registry held 38 such
    keys; it held 45 by 2.6.12, and nothing noticed for eleven releases."""

    def setUp(self):
        self.readme = (ROOT / "README.md").read_text(encoding="utf-8")

    def test_the_undecoded_count_is_current(self):
        """Both numbers, because the ratio is the point. Issue #30 reported every
        sensor Unknown, read the bare "45 sensors read Unknown" line as describing a
        normal condition, and had no reason to think anything was wrong."""
        from src.siseli_bridge.sensors import SENSORS, UNDECODED_SENSOR_KEYS

        self.assertIn(
            f"{len(UNDECODED_SENSOR_KEYS)} of the {len(SENSORS)} sensors read", self.readme
        )

    def test_the_sensor_totals_are_current(self):
        from src.siseli_bridge.sensors import SENSORS

        enabled = sum(1 for spec in SENSORS.values() if spec.get("enabled_by_default", True))
        self.assertIn(f"{len(SENSORS)} sensors", self.readme)
        self.assertIn(f"{enabled} enabled", self.readme)


class TestRequiredStatusChecks(unittest.TestCase):
    """Branch protection on main pins a list of required check names, and GitHub derives
    those names from ci.yml -- `<job id>` for a plain job, `<job id> (<matrix values>)`
    for a matrix one. The two are coupled with nothing connecting them: rename a job or
    change a matrix axis and every PR blocks forever, waiting on a check that no longer
    exists, with no error that points at the cause.

    Keep this list in step with the rule at
    Settings -> Branches -> main -> Require status checks. Changing one without the
    other is the failure this exists to catch.
    """

    REQUIRED = {
        "test (3.9)",
        "test (3.11)",
        "test (3.12)",
        "lint",
        "addon-lint",
        "smoke (amd64, ubuntu-24.04)",
        "smoke (aarch64, ubuntu-24.04-arm)",
    }

    def test_ci_produces_exactly_the_protected_check_names(self):
        jobs = _load_yaml(ROOT / ".github" / "workflows" / "ci.yml")["jobs"]
        produced = set()
        for job_id, job in jobs.items():
            matrix = (job.get("strategy") or {}).get("matrix")
            if not matrix:
                produced.add(job_id)
                continue
            if "include" in matrix:
                combos = [tuple(str(v) for v in entry.values()) for entry in matrix["include"]]
            else:
                axis = next(iter(matrix.values()))
                combos = [(str(value),) for value in axis]
            produced.update(f"{job_id} ({', '.join(combo)})" for combo in combos)

        self.assertEqual(
            produced,
            self.REQUIRED,
            "ci.yml no longer produces the checks branch protection requires -- update the "
            "protection rule and this list together",
        )


class TestBrandAssets(unittest.TestCase):
    """Supervisor finds icon.png and logo.png by filename convention -- nothing in
    config.yaml names them, and until 2.6.14 nothing in the test suite did either. Both
    were JPEG files carrying a .png extension, and byte-identical to each other, for the
    project's whole history. Content sniffing meant they rendered anyway, which is
    exactly why nobody noticed.

    These read the PNG header directly rather than going through Pillow. Pillow is not
    in the dev extras, so a Pillow-based test would skipTest on CI and pass vacuously --
    which is how the mislabelling survived in the first place.

    One constraint here is NOT machine-checkable and has already been got wrong once.
    The frontend caps the logo at `max-height: 40px`, so a 200px-tall source renders at
    0.2x and every text height in it is multiplied by that. 2.6.14 shipped a wordmark
    set at 33px and a tagline at 16px, which rendered at 6.6 and 3.2 CSS px -- the
    tagline was not small, it was invisible. Cap height must be at least ~30% of the
    canvas height. Check any new logo by resizing it to 40px tall and looking at it.
    """

    MAGIC = bytes((0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A))
    ASSETS = ("icon.png", "logo.png")

    def _header(self, name):
        """Return (width, height, colour_type) from the IHDR chunk, which a valid PNG
        is required to place first: 8 bytes of magic, a 4-byte length, the 'IHDR' tag,
        then width and height as big-endian uint32."""
        raw = (ADDON / name).read_bytes()
        self.assertEqual(raw[:8], self.MAGIC, f"{name} is not a PNG")
        self.assertEqual(raw[12:16], b"IHDR", f"{name} has no leading IHDR chunk")
        width = int.from_bytes(raw[16:20], "big")
        height = int.from_bytes(raw[20:24], "big")
        return width, height, raw[25]

    def test_both_assets_are_actually_png(self):
        """The extension was a lie about the container for the project's whole history."""
        for name in self.ASSETS:
            with self.subTest(asset=name):
                self._header(name)

    def test_the_icon_is_square_and_the_logo_is_not(self):
        """Home Assistant renders the icon as a square badge in the add-on list and the
        logo as a wider brand image on the add-on's own page. A square logo is what you
        get when one file has been copied over the other."""
        width, height, _ = self._header("icon.png")
        self.assertEqual(width, height, f"the icon must be square, got {width}x{height}")
        width, height, _ = self._header("logo.png")
        self.assertGreater(width, height, f"the logo must be landscape, got {width}x{height}")

    def test_the_two_assets_are_not_the_same_file(self):
        self.assertNotEqual(
            (ADDON / "icon.png").read_bytes(),
            (ADDON / "logo.png").read_bytes(),
            "logo.png is a copy of icon.png again",
        )

    def test_the_icon_carries_transparency(self):
        """The badge has rounded corners. Without transparency behind them the corners
        are filled with the old off-white, which reads as a pale notch on Home
        Assistant's dark theme -- and there is no dark-mode variant to escape to, since
        one file serves both themes.

        Colour types 4 and 6 carry an alpha channel outright. Type 3 is a palette, which
        carries transparency through a tRNS chunk instead -- accepted here because a
        palette build of this icon is several times smaller, and rejecting it would
        forbid that optimisation for no reason.
        """
        raw = (ADDON / "icon.png").read_bytes()
        colour_type = self._header("icon.png")[2]
        if colour_type == 3:
            self.assertIn(b"tRNS", raw, "palette icon.png has no tRNS chunk, so it is fully opaque")
        else:
            self.assertIn(
                colour_type, (4, 6), f"icon.png is PNG colour type {colour_type}, which cannot be transparent"
            )


class TestDockerContext(unittest.TestCase):
    """Docker matches .dockerignore with filepath.Match semantics, where `*` does not
    cross `/`. A bare `__pycache__/` therefore matched only the context root, and
    src/__pycache__, src/siseli_bridge/__pycache__, .coverage and siseli_bridge.egg-info
    shipped in every locally built image -- changing the layer hash, so two developers
    building the same commit got different images.

    These assert the patterns rather than inspecting a built image on purpose. All four
    artefacts are in .gitignore, so a clean CI checkout never has them and a behavioural
    test would pass vacuously while proving nothing.
    """

    def setUp(self):
        self.lines = [
            line.strip()
            for line in (ADDON / ".dockerignore").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]

    def test_residue_patterns_are_recursive(self):
        for pattern in (
            "**/__pycache__/",
            "**/*.pyc",
            "**/*.egg-info/",
            "**/.coverage",
            "**/.pytest_cache/",
            "**/.ruff_cache/",
        ):
            with self.subTest(pattern=pattern):
                self.assertIn(pattern, self.lines)

    def test_no_residue_pattern_is_root_only(self):
        """The regression is a pattern losing its `**/` prefix, not a pattern going
        missing -- the root-only form looks correct and silently covers one directory."""
        for bare in ("__pycache__/", "*.pyc", "*.egg-info/", ".coverage"):
            with self.subTest(pattern=bare):
                self.assertNotIn(bare, self.lines, f"{bare} matches only the context root")

    def test_the_changelog_is_re_included(self):
        """Supervisor renders the add-on's own CHANGELOG.md on the Changelog tab, so
        the blanket *.md exclusion has to make an exception for it."""
        self.assertIn("*.md", self.lines)
        self.assertIn("!CHANGELOG.md", self.lines)

    def test_no_build_yaml(self):
        """Supervisor logs `uses build.yaml which is deprecated`. Without one, the
        Dockerfile's ARG BUILD_FROM default wins -- and that default is a multi-arch
        manifest, which is what makes a local aarch64 build work at all."""
        self.assertFalse((ADDON / "build.yaml").exists())
        self.assertFalse((ADDON / "build.json").exists())


class TestPinsAgree(unittest.TestCase):
    """Every pin in this project is written down twice, and nothing used to check that
    the copies agreed. Dependabot raises one PR per location, so a half-landed bump is
    the realistic failure -- and in both cases below it is the *second* copy that ships."""

    def test_the_runtime_pins_live_in_one_file(self):
        """requirements.txt is the only place the runtime pins are written.

        They used to be listed in pyproject.toml as well, with a test asserting the two
        were equal. That test was correct and still unmergeable: Dependabot raises one
        PR per ecosystem and directory, so it could only ever bump one copy, and every
        dependency PR it opened failed by construction against a branch-protected main.
        Single-sourcing removes the class rather than relaxing the check.
        """
        pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        self.assertRegex(
            pyproject,
            r'dynamic\s*=\s*\[[^\]]*"dependencies"',
            "pyproject.toml must take its dependencies dynamically",
        )
        self.assertRegex(
            pyproject,
            r'dependencies\s*=\s*\{\s*file\s*=\s*\[\s*"siseli_bridge/requirements.txt"',
            "the dynamic source must be the file that ships in the image",
        )
        self.assertNotRegex(
            pyproject,
            r"(?m)^dependencies\s*=\s*\[",
            "a static dependency list is back, and Dependabot cannot keep two copies in step",
        )
        runtime = [
            line.strip()
            for line in (ADDON / "requirements.txt").read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        ]
        self.assertTrue(runtime, "requirements.txt is now the sole source and must not be empty")

    def test_the_base_image_pin_lives_in_one_file(self):
        """Same shape, same reason: Dependabot's docker ecosystem bumps the Dockerfile
        and cannot see a shell script, so smoke-test.sh reads the pin rather than
        restating it."""
        dockerfile = re.search(
            r"^ARG BUILD_FROM=(\S+)", (ADDON / "Dockerfile").read_text(encoding="utf-8"), re.M
        )
        self.assertIsNotNone(dockerfile, "Dockerfile declares no ARG BUILD_FROM default")

        smoke = (ROOT / "scripts" / "smoke-test.sh").read_text(encoding="utf-8")
        self.assertIn(
            "ARG BUILD_FROM=", smoke, "smoke-test.sh must read the pin out of the Dockerfile"
        )
        self.assertNotIn(
            dockerfile.group(1),
            smoke,
            "smoke-test.sh restates the base image pin instead of reading it",
        )


class TestDeprecatedOptions(unittest.TestCase):
    """LISTEN_PORT was exported, validated and described in the UI as the port the
    add-on listens on. Nothing ever bound a socket -- its only consumer was a startup
    log line.

    It is nevertheless kept in the schema. Supervisor validates the *stored* options
    before installing an update, so deleting a key that existing installations still
    have on disk blocks the upgrade for all of them. It is removed in 2.7.0, by which
    point stored copies have been rewritten.
    """

    def setUp(self):
        self.cfg = _load_yaml(ADDON / "config.yaml")
        self.config_py = CONFIG_PY.read_text(encoding="utf-8")

    def test_listen_port_is_retained_but_optional(self):
        self.assertTrue(str(self.cfg["schema"]["LISTEN_PORT"]).endswith("?"))

    def test_listen_port_is_read_only_to_warn(self):
        self.assertIn("LISTEN_PORT_DEPRECATED", self.config_py)
        self.assertNotIn("LISTEN_PORT,", self.config_py)  # not in the port-range checks

    def test_nothing_claims_to_listen_on_a_socket(self):
        src_dir = ADDON / "src"
        for path in src_dir.rglob("*.py"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                for token in ("socket.socket", ".bind(", ".listen("):
                    self.assertNotIn(token, text)


class TestDebugFlagWiring(unittest.TestCase):
    def setUp(self):
        self.cfg = _load_yaml(ADDON / "config.yaml")

    def test_every_declared_flag_is_offered_in_the_schema(self):
        from src.siseli_bridge.config import DEBUG_FLAG_NAMES

        declared = self.cfg["schema"]["DEBUG_FLAGS"][0]
        for flag in DEBUG_FLAG_NAMES:
            with self.subTest(flag=flag):
                self.assertIn(flag, declared)

    def test_verbose_logging_is_off_by_default(self):
        """It shipped as true, so a fresh install wrote a line per captured frame."""
        self.assertIs(self.cfg["options"]["LOG_VERBOSE"], False)
        self.assertEqual(self.cfg["options"]["DEBUG_FLAGS"], [])


def _validates(value, declaration):
    """Approximate Home Assistant's add-on option validation.

    Only the declaration forms this add-on uses are handled. The rule that matters and
    is easy to get wrong: a trailing `?` means the option may be *absent*, not that an
    empty string is acceptable. An empty string is a present value and must satisfy the
    declaration on its own.
    """
    if isinstance(declaration, list):
        if not isinstance(value, list):
            return False
        inner = re.match(r"list\((.+)\)$", declaration[0])
        allowed = set(inner.group(1).split("|")) if inner else set()
        return all(item in allowed for item in value)

    declaration = str(declaration)
    if declaration.endswith("?"):
        declaration = declaration[:-1]
        if value is None:
            return True

    if declaration == "bool":
        return isinstance(value, bool)
    if declaration == "str" or declaration == "password":
        return isinstance(value, str)
    if declaration.startswith("list("):
        allowed = set(declaration[len("list(") : -1].split("|"))
        return value in allowed
    if declaration.startswith("match("):
        pattern = declaration[len("match(") : -1]
        return isinstance(value, str) and re.match(pattern, value) is not None
    if declaration.startswith("float"):
        return isinstance(value, (int, float))
    if declaration.startswith("int"):
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        bounds = re.match(r"int\((-?\d*),(-?\d*)\)$", declaration)
        if bounds:
            low, high = bounds.groups()
            if low and value < int(low):
                return False
            if high and value > int(high):
                return False
        return True
    raise AssertionError(f"unhandled schema declaration: {declaration!r}")


class TestShippedDefaultsSatisfyTheSchema(unittest.TestCase):
    """Home Assistant validates the *stored* options against the schema before it will
    install an update. A default that its own schema rejects therefore blocks the
    upgrade for every existing installation, with an error that names the schema rather
    than the option -- which is exactly what happened when a MAC address pattern was
    added to two options that both default to an empty string.
    """

    def setUp(self):
        self.cfg = _load_yaml(ADDON / "config.yaml")

    def test_every_default_satisfies_its_own_declaration(self):
        for key, declaration in sorted(self.cfg["schema"].items()):
            with self.subTest(option=key):
                self.assertIn(key, self.cfg["options"], "option has a schema but no default")
                value = self.cfg["options"][key]
                self.assertTrue(
                    _validates(value, declaration),
                    f"the shipped default {value!r} does not satisfy {declaration!r}",
                )

    def test_an_optional_pattern_still_accepts_an_empty_string(self):
        """`?` marks the option optional; it does not exempt an empty string from the
        pattern. Any pattern on an option that can be left blank has to allow it."""
        for key in ("INVERTER_MAC", "ROUTER_MAC"):
            with self.subTest(option=key):
                declaration = self.cfg["schema"][key]
                self.assertTrue(_validates("", declaration), "a blank value must be accepted")
                self.assertTrue(_validates("74-E9-D8-A3-41-2A", declaration))
                self.assertTrue(_validates("aa:bb:cc:dd:ee:ff", declaration))
                self.assertFalse(_validates("not-a-mac", declaration))

    def test_a_previously_valid_stored_configuration_still_installs(self):
        """A real configuration from a running installation, as Supervisor would
        present it on upgrade. Every key it carries must still validate."""
        stored = {
            "MQTT_HOST": "192.168.0.134",
            "MQTT_PORT": 1883,
            "MQTT_USER": "frigate",
            "MQTT_PASSWORD": "frigate",
            "TARGET_HOST": "8.212.18.157",
            "TARGET_PORT": 1883,
            "LISTEN_PORT": 18899,
            "INVERTER_IP": "192.168.0.152",
            "ROUTER_IP": "192.168.0.1",
            "INVERTER_MAC": "74-E9-D8-A3-41-2A",
            "ROUTER_MAC": "",
            "AUTO_INTERCEPT": True,
            "MQTT_DISCOVERY_PREFIX": "homeassistant",
            "DEVICE_ID": "siseli_inverter_1",
            "DEVICE_NAME": "Siseli Inverter 1",
            "MODEL_NAME": "Siseli Inverter 1",
            "MANUFACTURER": "Siseli Compatible",
            "ENTITY_PREFIX": "Siseli",
            "INVERTER_COUNT": 2,
            "BATTERY_COUNT": 2,
            "BATTERY_CAPACITY_PER_BATTERY_AH": 300.0,
            "STATE_TOPIC": "siseli/siseli_inverter_1/state",
            "AVAILABILITY_TOPIC": "siseli/siseli_inverter_1/availability",
            "SNIFF_IFACE": "",
            "LOG_VERBOSE": True,
            "LOG_LEVEL": "info",
            "UPDATE_INTERVAL_SEC": 10,
            "MQTT_RETAIN": True,
        }
        schema = self.cfg["schema"]
        for key, value in sorted(stored.items()):
            with self.subTest(option=key):
                self.assertIn(key, schema, "a stored option lost its schema entry")
                self.assertTrue(
                    _validates(value, schema[key]),
                    f"stored value {value!r} rejected by {schema[key]!r}",
                )


class TestTestEnvironmentMatchesShippedDefaults(unittest.TestCase):
    """tests/helpers.py BASE_ENV stands in for the options Supervisor supplies. When
    it drifts from config.yaml, any test that reloads config silently runs against a
    value nobody ships -- which made a genuine availability-flapping bug look fixed
    depending on test order."""

    def test_base_env_matches_config_yaml(self):
        from tests.helpers import BASE_ENV

        options = _load_yaml(ADDON / "config.yaml")["options"]
        for key, shipped in sorted(options.items()):
            if key not in BASE_ENV:
                continue  # list-valued options are represented differently
            with self.subTest(option=key):
                expected = shipped
                if isinstance(shipped, bool):
                    expected = str(shipped).lower()
                elif isinstance(shipped, list):
                    continue
                self.assertEqual(
                    BASE_ENV[key],
                    str(expected),
                    f"BASE_ENV has {BASE_ENV[key]!r} but the add-on ships {shipped!r}",
                )

    def test_every_shipped_option_is_represented(self):
        from tests.helpers import BASE_ENV

        options = set(_load_yaml(ADDON / "config.yaml")["options"])
        self.assertEqual(
            options - set(BASE_ENV), set(), "an option the add-on ships is missing from BASE_ENV"
        )


class TestCiTokenIsReadOnly(unittest.TestCase):
    """The repository's default workflow token was write-scoped until 2026-09-15, and
    actions/checkout stores the token in .git/config for the rest of the job, so any step
    -- a third-party action included -- could have pushed to main. The repository default
    is read-only now, but a default is not a ceiling, and it lives in settings no checkout
    can see, so the workflow keeps asking only to read. A job-level block would quietly
    widen it again for that one job, so that is refused too: a job that genuinely needs
    more should have to change this test to get it."""

    def setUp(self):
        self.workflow = _load_yaml(ROOT / ".github" / "workflows" / "ci.yml")

    def test_the_workflow_asks_only_to_read(self):
        self.assertEqual(self.workflow.get("permissions"), {"contents": "read"})

    def test_no_job_widens_the_token(self):
        for name, job in self.workflow["jobs"].items():
            with self.subTest(job=name):
                self.assertNotIn("permissions", job, f"job {name} declares its own permissions")


if __name__ == "__main__":
    unittest.main()
