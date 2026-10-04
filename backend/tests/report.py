"""Write TEST_REPORT.md from the last test runs.

    python -m pytest          (writes tests/results/results.json)
    npx playwright test       (in frontend/; writes frontend/e2e/results/results.json)
    python tests/report.py    (writes tests/TEST_REPORT.md from both)

Every status, actual output and count below is read from those two files. The
only things written by hand are the fixed sections (strategy, criteria, risks),
FINDINGS, which says what was found to be the cause of a failing test, FIXED,
which lists the application bugs the tests found and that were then fixed, and
SCENARIOS, which says which tests cover each scenario asked for.
"""

import json
import re
from collections import Counter
from datetime import datetime
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
RESULTS_FILE = TESTS_DIR / "results" / "results.json"
REPORT_FILE = TESTS_DIR / "TEST_REPORT.md"
FRONTEND_DIR = TESTS_DIR.parents[1] / "frontend"
UI_RESULTS_FILE = FRONTEND_DIR / "e2e" / "results" / "results.json"
# Screenshot paths are recorded relative to frontend/; this report sits in backend/tests/.
FROM_REPORT_TO_FRONTEND = "../../frontend/"

LEVELS = ("unit", "graph", "integration", "ui")
PRIORITIES = ("Critical", "High", "Medium")
ID_ORDER = ("S", "U", "G", "I", "UI")

# The suite as it stood before this round's tests were written, run once.
BASELINE = (
    "Before any test of this round was written, the 115 tests of the previous round were run once: "
    "115 passed, 0 failed. None of them was removed or changed; all are still in section 4 with the "
    "output of the run reported here."
)

# Application bugs a test found, that were then fixed in a small function.
# Each stays covered by the test that found it.
FIXED = [
    {
        "tests": "I-50",
        "title": "A file moved into a folder and checked out with CRLF line endings read as removed and added",
        "found": "The update listed Auth.java as removed and code/Auth.java as added, instead of renamed: "
                 "the only rename detection without GitHub's word was an exact byte hash, and CRLF changes "
                 "the bytes. The links still came out valid (the element diff paired the methods), but the "
                 "change list was wrong.",
        "fix": "`api/changes.py`, `compare_file_sets`, with the new helper `same_text_moved`: a removed file "
               "and an added file whose text is the same once whitespace is normalised (the same rule as "
               "`element_hash`) are paired as a rename.",
    },
    {
        "tests": "I-50",
        "title": "A repository's .gitignore was listed as an added file of the code side",
        "found": "Files the side's kind never reads (.gitignore, README.md and the like) were compared and "
                 "listed as added, removed or modified, although no element can come from them.",
        "fix": "`api/changes.py`, `compare_file_sets`: only files with an extension the side's kind reads "
               "(from `ARTIFACT_KINDS`) are compared.",
    },
    {
        "tests": "I-58, I-59, I-60, I-61",
        "title": "Deleting a version's only run left the Trace Links page with an error and no links",
        "found": "In manual testing: the only run of v1 was deleted (allowed), v2 was then made by an update, and "
                 "the default v1 -> v2 report failed with 'Version 1 has no run left to report on.', showing zero "
                 "counts and none of v2's 8 links; the Changes tab was empty.",
        "fix": "`api/project_routes.py`, `delete_analysis`: a version's only run is refused (409). "
               "`core/projects/service.py`, `version_report`: the default earlier version is the nearest one with a "
               "run, and a version without a run is named in `no_run_version` instead of failing; the later "
               "version's links are then returned uncompared. The Trace Links page shows them with a short note "
               "and no counts, and marks such versions '(no run)'.",
    },
]

# Which tests cover each scenario of this round's request. Phase 1 is the
# API level, phase 2 the browser.
SCENARIOS = [
    ("1.1", "Demo story: v1 New Analysis, v2 cosmetic edit, v3 semantic edit, v4 file removed; report v1 -> v4", ["I-49"], ""),
    ("1.2", "Code moved from flat paths into code/, CRLF and LF mixed, .gitignore present", ["I-50"], ""),
    ("1.3", "Update equals a fresh analysis: modified sentence, inserted sentence, removed file, modified method", ["I-51"], ""),
    ("1.4", "Sentence inserted in v2, removed in v3: v1 -> v3 keeps its links valid", ["I-43", "I-45"], "Already covered; no new test"),
    ("1.5", "Classifier fails in the middle of an update", ["I-52", "I-13"], "I-13 covered a run that fails before classifying"),
    ("1.6", "Bad uploads: empty, wrong extension, bad zip, too large", ["I-17", "I-53"], "I-17 covers New Analysis uploads, I-53 the Update"),
    ("1.7", "A second user and anonymous callers on every analysis endpoint", ["I-20", "I-54"], "I-54 adds the endpoints I-20 did not reach"),
    ("1.8", "Data integrity: versions have runs, file rows have blobs, deleting a run keeps shared blobs", ["I-55"], ""),
    ("1.9", "Two analyses on the same requirement file: updating one leaves the other", ["I-09"], "Already covered; no new test"),
    ("1.10", "UML: changing one component keeps the other links; names, not ids, in the API and exports", ["I-56", "I-48", "UI-08", "UI-10"],
     "The app has no JSON export; Excel is not downloaded by a test (see section 1)"),
    ("1.11", "GitHub: new commit, no new commit, whitespace-only commit, rejected token, account vs side token",
     ["I-14", "I-10", "I-57", "I-15", "I-41", "I-42"], "I-57 is new; the rest already covered"),
    ("1.12", "Migration with data: users and caches survive, project tables recreated empty", ["G-28"], ""),
    ("2.1", "New Analysis in the browser: choose files, run with progress, save, listed as v1", ["UI-01"], ""),
    ("2.2", "Analysis page: both sides, files, Update each; GitHub only for code", ["UI-02"], ""),
    ("2.3", "Update with identical files, then with a cosmetic edit", ["UI-03"], ""),
    ("2.4", "Inserted sentence: Trace Links v1 -> v2 all Valid, none Broken", ["UI-04"], ""),
    ("2.5", "Trace Links page: tabs, five groups, two versions, reasons, Show more", ["UI-05"], ""),
    ("2.6", "Removed requirement file shows under Broken", ["UI-06"], ""),
    ("2.7", "GitHub connect dialog: connected, not connected, needs reconnect, access token", ["UI-07"], ""),
    ("2.8", "UML analysis: graph and link list show component names", ["UI-08"], ""),
    ("2.9", "Update whose classifier fails: error shown, no version", ["UI-09"], ""),
    ("2.10", "Export triggers a download that is not empty", ["UI-10"], ""),
]

CANNOT_COVER = [
    "How the real models behave: Groq's answers, Ollama's embeddings, their speed, their failures and their "
    "rate limits. Every test uses the deterministic fakes, so a test can pass while the real pipeline links differently.",
    "The quality of the recovered links (precision and recall). That depends on the real models and is measured "
    "by the `evaluate_*.py` scripts, not by pass/fail tests.",
    "Real GitHub: the OAuth sign-in on github.com, real tokens expiring, the archive download, the compare API, "
    "403 and rate-limit answers. GitHub is a fake in the API tests and route-mocked in UI-07.",
    "Browsers other than Chromium, small screens, and how long the real pipeline takes on a large repository.",
    "Pages no scenario asked for: landing, sign-in, dashboard, profile and the results page's panels beyond the "
    "export. Sign-in and project creation are only used as set-up, through the API.",
]

# The diagnosis of each failing test: where the fault is and what it means for
# the demo. Only shown for tests that actually failed in the run being reported.
# Empty: every fault the first run found has been fixed or designed away, and
# the diagnoses of that run are kept in TEST_REPORT_before_fixes.md.
FINDINGS: dict[str, dict] = {}

# Remarks worth carrying with a test even when it passes.
NOTES = {
    "S-02": "The same guard runs for the whole session; any blocked attempt fails the test that made it.",
    "U-16": "The aggregator keeps the first result it sees for a pair. Candidates arrive most-similar first, so in practice that is the strongest one.",
    "U-26": "An expanded link is kept only if similarity x edge confidence (0.7 for a call) x 0.85 per step reaches 0.35, so the link it grows from needs a similarity of about 0.59 or more.",
    "I-26": "Failed in the first run of this suite: the pairs were excluded from the result sent to the client, so saving stored none. Fixed by sending them with the result (api/schemas.py).",
    "I-31": "Companion to I-26: the same scenario when a re-run, rather than a New Analysis, stored the pins.",
    "U-19": "Failed in the first run of this suite. Fixed: a reply without the tag is read as whole words, and counts as a link only if it says yes and not no (core/classification/base.py).",
    "I-29": "Failed in the first run of this suite. Fixed: the route now needs a signed-in user. A signed-in user can still name any folder the server can read, so the API must stay on localhost.",
    "I-13": "Failed in the first run of this suite, when a sync made its version before running. An update now makes its version only after its run has been saved.",
    "I-30": "Failed in the first run of this suite. The save route now returns the version it created.",
    "I-14": "The fake repository writes exact bytes. Written in text mode, Windows turned its newlines into CRLF, and the app - correctly - read the files as changed only in whitespace.",
    "I-20": "This closes the 'second user never tested' gap for every endpoint that takes an id.",
    "I-50": "Failed when first written: Auth.java (CRLF) was listed as removed and code/Auth.java as added, and .gitignore as added. Fixed in api/changes.py; see section 5.",
    "I-51": "The four changes are compared in one test, each with an analysis of its own; the record lists each one's result.",
    "UI-01": "Each job poll is held back 1.2 s by a route in the test, so the run is still going when the progress bar is checked; the fake models finish in well under a second.",
    "UI-07": "The account state is set by mocking /github/connection and /github/repositories in the browser, as asked; the backend is not involved in it.",
}


def natural(test) -> tuple:
    prefix, number = test["case"]["id"].split("-")
    return ID_ORDER.index(prefix), int(number)


def read_ui_results() -> tuple[list[dict], dict]:
    """The Playwright run, in the same shape as a pytest row."""
    if not UI_RESULTS_FILE.exists():
        return [], {}
    data = json.loads(UI_RESULTS_FILE.read_text(encoding="utf-8"))
    rows = []

    def walk(suite):
        for spec in suite.get("specs", []):
            for test in spec["tests"]:
                notes = {}
                for note in test["annotations"]:
                    notes.setdefault(note["type"], []).append(note.get("description", ""))
                if "case" not in notes:
                    continue
                last = test["results"][-1]
                errors = last.get("errors") or []
                rows.append({
                    "nodeid": f"frontend/e2e/{spec['file']} - {spec['title']}",
                    "case": {"preconditions": "None", **json.loads(notes["case"][0]), "level": "ui"},
                    "description": spec["title"].split(" ", 1)[1],
                    "outcome": "passed" if last["status"] == "passed" else "failed",
                    "failure": re.sub(r"\x1b\[[0-9;]*m", "", errors[0]["message"]).splitlines()[0] if errors else None,
                    "duration": last["duration"] / 1000,
                    "observed": notes.get("actual", []),
                    "screenshots": notes.get("screenshot", []),
                })
        for child in suite.get("suites", []):
            walk(child)

    for suite in data["suites"]:
        walk(suite)
    # Playwright stamps UTC; shown in local time, like the pytest run.
    started = datetime.fromisoformat(data["stats"]["startTime"].replace("Z", "+00:00")).astimezone()
    return rows, {"version": data["config"]["version"], "date": started.strftime("%Y-%m-%d %H:%M")}


def prose(text) -> str:
    """Text for the body of the report, with tags such as <trace> kept visible.

    A Markdown viewer treats a bare tag as HTML and hides it, so tags are put
    in code spans.
    """
    text = re.sub(r"<([a-z]+)>([^<`]*)</\1>", r"`<\1>\2</\1>`", str(text))
    return re.sub(r"(?<!`)<(/?[a-z]+)>(?!`)", r"`<\1>`", text)


def cell(text) -> str:
    """Text made safe for a Markdown table cell."""
    return prose(text).replace("|", "\\|").replace("\n", " ")


def status(test) -> str:
    return {"passed": "PASS", "failed": "FAIL"}.get(test["outcome"], test["outcome"].upper())


def tally(tests, key) -> dict:
    """Passed and failed counts, grouped by one field of the test case."""
    groups = {}
    for test in tests:
        group = groups.setdefault(test["case"][key], Counter())
        group[status(test)] += 1
    return groups


def counts_table(groups: dict, order, heading: str) -> list[str]:
    lines = [f"| {heading} | Total | Passed | Failed |", "|---|---|---|---|"]
    for name in order:
        group = groups.get(name, Counter())
        lines.append(f"| {name} | {sum(group.values())} | {group['PASS']} | {group['FAIL']} |")
    return lines


def strategy(environment: dict, ui_environment: dict) -> list[str]:
    return [
        "## 1. Testing approach/strategy",
        "",
        "**Aim.** Find real faults and regressions in TraceRAG before the final demonstration, "
        "and keep a repeatable check that what works today still works after a change.",
        "",
        "**Test levels.**",
        "",
        "| Level | What it exercises | What it needs |",
        "|---|---|---|",
        "| Unit | One function or class on its own: the element diff and id map, the change report, identifier "
        "handling, upload safety, preprocessors, response parsing, OAuth state, and the pipeline run on files with "
        "fake models | No database |",
        "| Service / database (`graph` in the tables) | The service layer (`core/projects/service.py`, caches, jobs, "
        "artifact store) called directly: analysis identity, versions, sides, file sets, pinning store, garbage "
        "collection | The test database |",
        "| Integration | The whole application through its HTTP API with FastAPI's `TestClient`: new analysis, "
        "re-run, update per side, change report, GitHub sides, access control, error responses, and the demo "
        "scenarios end to end | The test database |",
        "| UI (Playwright) | The React frontend in Chromium, against a real backend that uses the same fakes: "
        "New Analysis, the analysis page, the Update and Connect dialogs, the Trace Links page, exports | Its own "
        "database, a running backend and the Vite dev server, all started by Playwright |",
        "",
        "Three extra checks (S-01 to S-03) test the test environment itself: that it is isolated, that the "
        "network is blocked, and that the fake models are deterministic.",
        "",
        "**How the scope was chosen (risk-based).** There is no coverage target. Tests were written where a "
        "fault would do the most damage, in this order:",
        "",
        "1. Orchestration code, where functions that are each correct can still fit together wrongly "
        "(update, save, claim files, pin translation). The project's first known bug was of this kind: in the "
        "old project-wide sync, saving the first configuration deleted the working folder the next one still "
        "had to read. That code is gone - each analysis is now updated on its own - and I-13 checks that a "
        "failed update leaves nothing half-written.",
        "2. Domain logic where a silent error corrupts what users see: the element diff and id map (sentences, "
        "chunks and UML components are named by position, so one insertion renames everything after it), the "
        "change report's states and reasons, link pinning, analysis identity, caches.",
        "3. Safety code: path traversal, zip-slip, size limits, garbage collection refusals, OAuth redirect and state.",
        "4. The API surface: every main flow, error responses, and access control between two users.",
        "",
        "Each test has a priority: **Critical** (the demo breaks or data is silently corrupted if it fails), "
        "**High** (a core feature behaves wrongly), **Medium** (an edge case, a safety check or a helper).",
        "",
        "**Tools.** pytest " + environment["pytest"] + " and FastAPI `TestClient` (which runs background jobs "
        "inside the request, so a test can read a finished job directly), listed in `backend/requirements-dev.txt`. "
        "For the UI tests, Playwright " + (ui_environment.get("version") or "(not run)") + " with Chromium, "
        "the only dependency added to the frontend (`@playwright/test`, a devDependency).",
        "",
        "**Fake models.** No test calls Ollama, Groq or GitHub.",
        "",
        "- `FakeEmbedder` (`tests/fakes.py`) turns text into a fixed bag-of-words vector, so texts that share "
        "words are close together and the same text always gets the same vector.",
        "- `FakeClassifier` subclasses the application's own `Classifier` and replaces only the model call: two "
        "elements are linked when they share a keyword. The caching around the call is the real code.",
        "- `FakeChatProvider` answers summary requests in the expected format.",
        "- `FakeGitHub` stands in for a repository: a head commit, the files at it, and a rename list.",
        "- `TracePipeline` takes its embedder and classifier as constructor arguments, so unit tests pass the "
        "fakes in directly. API tests replace them where `api/pipeline_factory.py` builds them, using pytest's "
        "`monkeypatch`; no application code is changed.",
        "- A network guard refuses every connection that would leave the machine or reach Ollama's port, and "
        "fails the test that tried (S-02).",
        "",
        "- The UI tests' backend is `backend/tests/e2e_server.py`, a test-only launcher: it makes a separate "
        "database `tracerag_e2e`, puts stored files and vector indexes in a temp folder, patches the same fakes "
        "in before the app starts, and serves it on a free port. Playwright starts it and the Vite dev server, and "
        "drops the database and the folder at the end. The application has no test mode. A requirement containing "
        "`CLASSIFIER-OUTAGE` makes this launcher's fake classifier fail (UI-09).",
        "",
        "Why fakes: the same answer on every run, no cost, no network, and a full run in under a minute. "
        "What they cannot show is how accurate the real models are; that is measured separately.",
        "",
        "**Test database.** PostgreSQL is required (the schema uses `ARRAY(Float)` and `ON CONFLICT`, so SQLite "
        "cannot stand in). `tests/conftest.py` reads the server address and login from `backend/.env`, creates "
        "a separate database named `tracerag_test` if it does not exist, builds it with the application's own "
        "Alembic migrations, and empties every table at the start of each run. It is **not** dropped afterwards, "
        "so the data the last run produced can be inspected. The run refuses to start if `.env` itself points at "
        "`tracerag_test`. Uploaded files, stored blobs and vector indexes go to `backend/tests/.work/`, never to "
        "`app_data/` or `chroma_data/` (S-01).",
        "",
        "**Test data.** A small corpus in `tests/data/`: four use cases, two Java files with five methods, one "
        "architecture document and one UML model, written so the correct links are known in advance.",
        "",
        "**How to run.** From `backend/`, with the project's Python environment:",
        "",
        "```",
        "pip install -r requirements-dev.txt",
        "python -m pytest                      # everything",
        "python -m pytest -m unit              # one level: unit, graph or integration",
        "python -m pytest tests/integration/test_sync.py",
        "python tests/report.py                # rewrite this report from the last runs",
        "```",
        "",
        "The UI tests, from `frontend/` (`E2E_PYTHON` is the backend's Python; `python` if not set):",
        "",
        "```",
        "npm install",
        "npx playwright install chromium",
        "set E2E_PYTHON=D:\\anaconda3\\envs\\tracerag\\python.exe",
        "npx playwright test",
        "```",
        "",
        "Screenshots of every key state go to `frontend/e2e/screenshots/`.",
        "",
        "**How the report is produced.** Each test describes itself with a `@case(...)` decorator and writes "
        "down what it saw with `record(...)`. pytest stores one row per test in `tests/results/results.json`, "
        "and `tests/report.py` writes this file from it. Every status and every 'Actual output' below comes "
        "from that file.",
        "",
        "**Deliberately out of scope.**",
        "",
        "| Not tested | Why |",
        "|---|---|",
        "| Accuracy of the recovered links (precision, recall) | Depends on the real models; measured by the `evaluate_*.py` scripts, not by pass/fail tests |",
        "| Real Ollama, Groq and GitHub calls | Slow, costly, non-deterministic and need a network; replaced by fakes |",
        "| The pages no scenario covers (landing, sign-in, dashboard, profile) | Used only as set-up or not at all; checked by hand |",
        "| The chain feature (requirements -> design -> code) | Not implemented |",
        "| Performance and load | Not a goal for this release |",
        "| PDF and DOCX reading; Python, JavaScript and TypeScript parsing; the code-chunk preprocessor | The demo corpus is .txt and Java; listed as a risk in section 3 |",
        "",
        "**What these tests cannot cover.**",
        "",
        *[f"- {item}" for item in CANNOT_COVER],
        "",
    ]


def criteria(tests) -> list[str]:
    failed_critical = [t["case"]["id"] for t in tests if t["case"]["priority"] == "Critical" and status(t) == "FAIL"]
    failed_high = [t["case"]["id"] for t in tests if t["case"]["priority"] == "High" and status(t) == "FAIL"]
    met = not failed_critical
    return [
        "## 2. Item pass/fail criteria",
        "",
        "**A single test** passes when every assertion in it holds and it made no attempt to reach the network. "
        "It fails on the first assertion that does not hold, on any unexpected exception, or on a blocked network "
        "attempt. There are no partial passes, and no test is skipped or marked as an expected failure.",
        "",
        "| Level | Passes when |",
        "|---|---|",
        "| Unit | The function returns exactly the expected value, or raises exactly the expected error, for every input listed in the case |",
        "| Service / database | After the service calls, the rows in the test database (analyses, versions, sides, file sets, pins) and the files in the test store match the expected state exactly |",
        "| Integration | Each HTTP response has the expected status code and body, the background job finishes in the expected state, the database and files are left as expected, and no working folder is left behind |",
        "| UI | Every element the test looks for is shown with the expected text or count within 10 seconds, every download arrives, and the values read from the page equal the expected ones |",
        "",
        "**Rules for a failing test.** A test is never weakened or changed to make it pass. A failure is first "
        "diagnosed as a fault in the test or in the application. A test fault is fixed in the test. An "
        "application fault is fixed only when the fix is one small function (about 20 lines), and is then "
        "listed in section 5 with the test that found it; any other application fault is left failing and "
        "listed in section 5.",
        "",
        "**Acceptance criterion for the suite.**",
        "",
        "1. Every Critical test passes.",
        "2. Every failing High or Medium test has a diagnosed cause and a stated impact in section 5.",
        "3. No test depends on another test, on the order of running, or on anything outside the machine.",
        "",
        f"**Result for this run: {'met' if met else 'NOT met'}.** "
        + (f"Critical tests failing: {', '.join(failed_critical)}. " if failed_critical else "All Critical tests pass. ")
        + (f"High-priority tests failing: {', '.join(failed_high)}. " if failed_high else "")
        + "Every failure is diagnosed in section 5.",
        "",
    ]


def risks(tests) -> list[str]:
    failing_critical = [t["case"]["id"] for t in tests if t["case"]["priority"] == "Critical" and status(t) == "FAIL"]
    rows = [
        ("LLM non-determinism: the same pair can be judged differently on two runs",
         "Links appear and disappear between runs with no change to the artifacts; tests would be flaky",
         "Tests use deterministic fakes. In the application, verdicts are cached by content so unchanged pairs are not asked again (G-21), and pinned links are re-offered (U-27, I-31). Accuracy is measured separately with the evaluate scripts"),
        ("Ollama, Groq or GitHub unavailable",
         "No embeddings, no classification, or no fetch; an update fails part-way",
         "Tests never need them (S-02). A failed update leaves the analysis exactly as it was, with no new version (I-13). For the demo: run everything once beforehand so the caches are warm, and keep updating by upload (I-08) as the fallback for GitHub"),
        ("PostgreSQL is required to run the tests",
         "The suite cannot run on a machine without a local Postgres; SQLite is not an option",
         "The suite creates its own database from the credentials in `.env` and refuses to run against the development one (S-01). A CI setup would need a Postgres service"),
        ("OAuth tokens expiring or being revoked",
         "An update from a private repository fails; the app could still claim to be connected",
         "A rejected token is detected, cleared and reported as 'reconnect' (I-15). Why tokens died overnight in development was never identified, so reconnect GitHub shortly before the demo"),
        ("The fakes do not behave like the real models",
         "A test can pass while the real pipeline links differently; ranking and thresholds differ",
         "The tests verify plumbing and rules, not accuracy. Before the demo, do one full manual run with the real models on the demo project"),
        ("Untested: a personal access token pasted for one repository",
         "A per-repository token could be stored or used wrongly",
         "Not covered. Encryption of stored tokens is covered (U-24); the paste path is not. Use the OAuth connection in the demo"),
        ("Untested: GitHub's 403 and rate-limit answers",
         "The error shown to the user in that case is unverified",
         "Not covered; the client code that maps GitHub status codes is replaced by the fake. Connect an account so requests are authenticated"),
        ("Untested: the real size caps (30 MB upload, 200 MB repository)",
         "The limits are only proven at lowered values",
         "The limit logic is covered with small limits (U-06, U-07, U-09, I-17). The real values are constants and were not exercised"),
        ("The element diff pairs the wrong elements",
         "A link is reported against the wrong sentence or component, or a pin is offered for the wrong element",
         "Covered for insertion, removal, edit, swap, rename, whitespace, UML counters and method names (U-29 to U-38) and end to end (I-38, I-39). Two elements edited beyond recognition in the same place are paired by identifier, which can be wrong for positional identifiers"),
        ("Previously untested: a second user",
         "One user could read or change another's project",
         "Now covered: every cross-user request in I-20 is refused, including an analysis reached through another project's id, and anonymous jobs need their token (I-21, G-24)"),
        ("Untested: PDF and DOCX input; Python, JavaScript and TypeScript parsing; code chunks",
         "A fault there would show only with those inputs",
         "Not covered. The demo uses .txt requirements and Java code, which are"),
        ("Concurrency: two requests at once",
         "Two updates of one analysis could both make a version from the same starting point",
         "A second update of the same analysis is refused while one is unfinished (I-11), and stale jobs are cleared (G-24). Parallel requests themselves were not simulated"),
        ("The server contacts the internet when it starts",
         "`nltk.download('punkt_tab')` runs on every import of the sentence preprocessor; offline, startup waits on it",
         "Observed by the network guard, see section 5. The tokenizer data is already on the demo machine"),
    ]
    if failing_critical:
        rows.append((
            f"A Critical test is failing ({', '.join(failing_critical)})",
            "The behaviour it covers cannot be relied on in the demo",
            "See the workaround given for it in section 5",
        ))
    lines = ["## 3. Risks and contingencies", "", "| Risk | Impact | Contingency |", "|---|---|---|"]
    lines += [f"| {cell(risk)} | {cell(impact)} | {cell(plan)} |" for risk, impact, plan in rows]
    return lines + [""]


def test_cases(tests) -> list[str]:
    lines = [
        "## 4. Test cases with detailed outcomes",
        "",
        "### 4.1 Summary",
        "",
        "| ID | Feature | Type | Priority | Status |",
        "|---|---|---|---|---|",
    ]
    lines += [
        f"| {t['case']['id']} | {cell(t['case']['feature'])} | {t['case']['level']} | {t['case']['priority']} | {status(t)} |"
        for t in tests
    ]
    backend = [t for t in tests if t["case"]["level"] != "ui"]
    browser = [t for t in tests if t["case"]["level"] == "ui"]
    lines += ["", "### 4.2 Details: backend tests", ""]
    lines += details(backend)
    lines += [
        "### 4.3 Details: UI tests (Playwright)",
        "",
        "Run in Chromium against the test-only backend described in section 1. Every screenshot is a full page, "
        "taken by the test at the moment named in its file name.",
        "",
    ]
    lines += details(browser)
    lines += scenarios(tests)
    return lines


def details(tests) -> list[str]:
    lines = []
    for test in tests:
        case = test["case"]
        observed = test["observed"] or ["(nothing was captured for this test)"]
        notes = []
        if status(test) == "FAIL":
            notes.append(f"Failed with: {test['failure']}")
            notes.append("Diagnosed as an application fault; see section 5." if case["id"] in FINDINGS
                         else "Cause not yet analysed.")
        if case["id"] in NOTES:
            notes.append(NOTES[case["id"]])

        lines += [
            f"#### {case['id']} - {prose(test['description'])}",
            "",
            f"- **Test ID:** {case['id']}",
            f"- **Feature/module:** {case['feature']}",
            f"- **Type:** {case['level']}",
            f"- **Priority:** {case['priority']}. {prose(case['why'])}",
            f"- **Description:** {prose(test['description'])}",
            f"- **Preconditions:** {prose(case['preconditions'])}",
            f"- **Input:** {prose(case['input'])}",
            f"- **Expected output:** {prose(case['expected'])}",
            "- **Actual output:**",
            "",
            "```text",
            *observed,
            "```",
            "",
            f"- **Status:** {status(test)}",
            f"- **Notes:** {prose(' '.join(notes)) if notes else 'None.'}",
            f"- **Test function:** `{test['nodeid']}`",
            "",
        ]
        if test.get("screenshots"):
            lines[-1:-1] = [
                "- **Screenshots:** " + ", ".join(
                    f"[{Path(shot).stem}]({FROM_REPORT_TO_FRONTEND}{shot})" for shot in test["screenshots"]
                ),
            ]
    return lines


def scenarios(tests) -> list[str]:
    by_id = {t["case"]["id"]: status(t) for t in tests}
    lines = [
        "### 4.4 The scenarios of this round and the tests that cover them",
        "",
        "Scenarios 1.x were asked for at the API level, 2.x in the browser. Where an existing test already "
        "covered a scenario, no new test was written and the existing one is named.",
        "",
        "| Scenario | What | Tests | Status | Note |",
        "|---|---|---|---|---|",
    ]
    for number, what, ids, note in SCENARIOS:
        statuses = {by_id.get(test_id, "NOT RUN") for test_id in ids}
        lines.append(f"| {number} | {cell(what)} | {', '.join(ids)} | {' / '.join(sorted(statuses))} | {cell(note)} |")
    return lines + [""]


def bugs(tests, environment) -> list[str]:
    failed = [t for t in tests if status(t) == "FAIL"]
    lines = ["## 5. Bugs found", ""]
    if not failed:
        lines += ["No test failed in this run.", ""]
    else:
        lines += [
            f"{len(failed)} test(s) failed. Each was checked to see whether the fault was in the test or in the "
            "application; all of the ones below are application faults, left unfixed as agreed. "
            "Ordered by priority.",
            "",
        ]
    for test in sorted(failed, key=lambda t: (PRIORITIES.index(t["case"]["priority"]), natural(t))):
        case = test["case"]
        finding = FINDINGS.get(case["id"])
        title = finding["title"] if finding else test["description"]
        lines += [f"### {case['id']} ({case['priority']}) - {prose(title)}", ""]
        lines += [
            f"- **What failed:** `{test['nodeid']}`",
            f"- **Expected:** {prose(case['expected'])}",
            "- **Observed:**",
            "",
            "```text",
            *(test["observed"] or ["(nothing captured)"]),
            f"assertion: {test['failure']}",
            "```",
            "",
        ]
        if finding:
            lines += [
                f"- **Likely cause:** {finding['cause']}",
                f"- **Impact:** {finding['impact']}",
                f"- **Severity for the demo:** {finding['severity']}",
                f"- **Workaround:** {finding['workaround']}",
                "",
            ]
        else:
            lines += ["- **Likely cause:** not yet analysed.", ""]

    lines += [
        "### Application bugs found and fixed in this round",
        "",
        "Each was found by a test that failed when first written, or in manual testing where it says so, "
        "fixed in a small change, and is covered by the tests named, which pass.",
        "",
    ]
    for bug in FIXED:
        lines += [
            f"#### {bug['tests']} - {prose(bug['title'])}",
            "",
            f"- **Found:** {prose(bug['found'])}",
            f"- **Fixed in:** {bug['fix']}",
            f"- **Covered by:** {bug['tests']} ({by_id_status(tests, bug['tests'])} in this run)",
            "",
        ]

    lines += ["### Observations that are not failing tests", ""]
    if environment["blocked_at_import"]:
        lines += [
            f"- **The application tries to reach the internet when it is imported.** The network guard refused "
            f"{len(environment['blocked_at_import'])} connection(s) during import, to "
            f"{', '.join(sorted(set(environment['blocked_at_import'])))}. The cause is "
            "`nltk.download('punkt_tab', quiet=True)` at the top of `core/preprocessing/sentence_preprocessor.py`, "
            "which runs on every server start. The application still starts when the call is refused.",
        ]
    lines += [
        "- **Line endings count as whitespace.** A repository whose files differ from an upload only in CRLF and "
        "LF line endings reads as 'no meaningful change', so it makes no new version (seen while writing I-14).",
        "- **The only pytest warning is from the application:** `config/settings.py` uses pydantic's class-based "
        "`Config`, which pydantic 2 marks as deprecated.",
        "",
    ]
    return lines


def by_id_status(tests, test_id) -> str:
    return next((status(t) for t in tests if t["case"]["id"] == test_id), "NOT RUN")


def summary(tests, environment, ui_environment) -> list[str]:
    total = len(tests)
    passed = sum(status(t) == "PASS" for t in tests)
    seconds = sum(t["duration"] for t in tests)
    lines = [
        "## 6. Summary",
        "",
        f"- **Old tests at the start of this round:** {BASELINE}",
        f"- **Date of run:** pytest {environment['date']}; Playwright {ui_environment.get('date', 'not run')}",
        f"- **Operating system:** {environment['os']}",
        f"- **Python:** {environment['python']}; **pytest:** {environment['pytest']}; "
        f"**Playwright:** {ui_environment.get('version', 'not run')} (Chromium)",
        f"- **Database:** {environment['database']}; the UI tests use 'tracerag_e2e', dropped after the run",
        f"- **Total:** {total} tests; **passed:** {passed}; **failed:** {total - passed}",
        f"- **Pass rate:** {passed / total:.1%}",
        f"- **Time spent in tests:** {seconds:.0f} seconds",
        f"- **Failing tests:** {', '.join(t['case']['id'] for t in tests if status(t) == 'FAIL') or 'none'}",
        "",
        "**By type**",
        "",
        *counts_table(tally(tests, "level"), LEVELS, "Type"),
        "",
        "**By priority**",
        "",
        *counts_table(tally(tests, "priority"), PRIORITIES, "Priority"),
        "",
        "**To run again:**",
        "",
        "```",
        "cd backend",
        "python -m pytest",
        "cd ../frontend",
        "npx playwright test",
        "cd ../backend",
        "python tests/report.py",
        "```",
        "",
    ]
    return lines


def main() -> None:
    data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    environment = data["environment"]
    ui_tests, ui_environment = read_ui_results()
    tests = sorted([*(t for t in data["tests"] if t["case"]), *ui_tests], key=natural)

    command = re.sub(r"\s+", " ", environment["command"]).strip()
    lines = [
        "# TraceRAG - test report",
        "",
        f"Generated by `tests/report.py` from the pytest run of {environment['date']} (`{command}`) and the "
        f"Playwright run of {ui_environment.get('date', '(none)')} (`npx playwright test`). "
        "Statuses and actual outputs are taken from those runs; nothing in them is written by hand.",
        "",
        *strategy(environment, ui_environment),
        *criteria(tests),
        *risks(tests),
        *test_cases(tests),
        *bugs(tests, environment),
        *summary(tests, environment, ui_environment),
    ]
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {REPORT_FILE} ({len(tests)} tests)")


if __name__ == "__main__":
    main()
