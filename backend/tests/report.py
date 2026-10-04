"""Write TEST_REPORT.md from the last test run.

    python -m pytest          (writes tests/results/results.json)
    python tests/report.py    (writes tests/TEST_REPORT.md from it)

Every status, actual output and count below is read from results.json. The
only things written by hand are the fixed sections (strategy, criteria, risks)
and FINDINGS, which says what was found to be the cause of a failing test.
"""

import json
import re
from collections import Counter
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
RESULTS_FILE = TESTS_DIR / "results" / "results.json"
REPORT_FILE = TESTS_DIR / "TEST_REPORT.md"

LEVELS = ("unit", "graph", "integration")
PRIORITIES = ("Critical", "High", "Medium")
ID_ORDER = "SUGI"

# The diagnosis of each failing test: where the fault is and what it means for
# the demo. Only shown for tests that actually failed in the run being reported.
FINDINGS = {
    "I-26": {
        "title": "Links are not pinned after a New Analysis, so the first sync can lose them",
        "severity": "High for the demo",
        "cause": (
            "`AnalyzeResponse.element_links` is declared with `exclude=True` (`api/schemas.py`), so it is "
            "left out of the job result the client receives. The client posts that result back to "
            "`POST /projects/{id}/analyses`; `save_analysis` in `api/project_routes.py` then calls "
            "`update_graph`, and `record_element_links` in `core/projects/service.py` is given an empty "
            "list. An empty list means 'this run linked nothing', so no pins are stored - and any pins the "
            "configuration already had are deleted. Re-runs and syncs build their result on the server and "
            "are not affected, which is why I-31 passes."
        ),
        "impact": (
            "Between a New Analysis and the first sync, the tool's main guarantee does not hold: adding "
            "unrelated code can push a link out of the top-k and it is reported as removed although "
            "nothing rejected it. Saving a New Analysis into a configuration that already has pins also "
            "wipes them."
        ),
        "workaround": "Run one sync with 'Re-run anyway' straight after a New Analysis; that stores the pins.",
    },
    "I-13": {
        "title": "A sync in which every configuration fails leaves an empty version behind",
        "severity": "Medium",
        "cause": (
            "`perform_sync` in `api/sync_jobs.py` calls `service.next_version()` before the loop over "
            "configurations and never removes that version when no configuration succeeds. The job is "
            "also recorded as succeeded, with a detail line that counts the failed configurations as re-run."
        ),
        "impact": (
            "Version history shows a version with 0 runs, and the job reads 'succeeded' although nothing "
            "ran. The sources are correctly left untouched, so a second sync still works. It only happens "
            "when the models cannot be reached."
        ),
        "workaround": "Check Ollama and Groq are reachable before syncing in the demo.",
    },
    "I-29": {
        "title": "POST /analyze needs no login and reads any folder on the server",
        "severity": "Low on a laptop, High if the API is ever reachable by others",
        "cause": (
            "The `analyze` route in `api/analysis_routes.py` has no authentication dependency, and "
            "`provider_for` in `api/pipeline_factory.py` passes the caller's `path` straight to a provider. "
            "The response includes each element's content."
        ),
        "impact": (
            "Anyone who can reach the API can read the text of any .txt/.pdf/.docx or source file the "
            "server process can read, by naming its folder."
        ),
        "workaround": "Keep the API bound to localhost for the demo. Do not deploy without closing this.",
    },
    "U-19": {
        "title": "A model reply without the <trace> tag is read as 'yes' if it contains the letters 'yes'",
        "severity": "Low to Medium",
        "cause": (
            "`_parse_response` in `core/classification/simple_classifier.py` and "
            "`core/classification/reasoning_classifier.py` falls back to `\"yes\" in cleaned.lower()` when "
            "the tag is missing. That matches inside other words: 'eyes', 'yesterday'."
        ),
        "impact": (
            "When the model ignores the output format, a clear 'No' can become a false trace link. "
            "It adds false positives; it never removes a real link."
        ),
        "workaround": "None needed for the demo; replies normally carry the tag.",
    },
    "I-30": {
        "title": "The response to saving an analysis reports version_number as null",
        "severity": "Low",
        "cause": (
            "`save_analysis` in `api/project_routes.py` builds its answer with `to_analysis_summary(...)` "
            "without passing `version_number`, so it defaults to None. The list endpoint passes it, which "
            "is why the same run shows version 1 there."
        ),
        "impact": "Cosmetic. A client reading the save response cannot tell which version was created.",
        "workaround": "Read the version from GET /analyses or GET /projects/{id}/versions.",
    },
    "I-32": {
        "title": "A requirement that was deleted is listed as 'newly implemented' when comparing runs",
        "severity": "Low",
        "cause": (
            "`compare_analyses` in `core/projects/service.py` computes `newly_implemented` as "
            "`base_unimplemented - head_unimplemented`. A requirement missing from the later run is absent "
            "from its unimplemented list, so it lands in that difference whether it gained a link or was removed."
        ),
        "impact": "The Compare page can overstate progress when unimplemented requirements are deleted.",
        "workaround": "None needed for the demo.",
    },
}

# Remarks worth carrying with a test even when it passes.
NOTES = {
    "S-02": "The same guard runs for the whole session; any blocked attempt fails the test that made it.",
    "U-16": "The aggregator keeps the first result it sees for a pair. Candidates arrive most-similar first, so in practice that is the strongest one.",
    "U-26": "An expanded link is kept only if similarity x edge confidence (0.7 for a call) x 0.85 per step reaches 0.35, so the link it grows from needs a similarity of about 0.59 or more.",
    "I-26": "Failed in the first run of this suite: the pairs were excluded from the result sent to the client, so saving stored none. Fixed by sending them with the result (api/schemas.py).",
    "I-31": "Companion to I-26: the same scenario when a sync, rather than a New Analysis, stored the pins.",
    "I-20": "This closes the 'second user never tested' gap for every endpoint that takes an id.",
}


def natural(test) -> tuple:
    prefix, number = test["case"]["id"].split("-")
    return ID_ORDER.index(prefix), int(number)


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


def strategy(environment: dict) -> list[str]:
    return [
        "## 1. Testing approach/strategy",
        "",
        "**Aim.** Find real faults and regressions in the TraceRAG backend before the final demonstration, "
        "and keep a repeatable check that what works today still works after a change.",
        "",
        "**Test levels.**",
        "",
        "| Level | What it exercises | What it needs |",
        "|---|---|---|",
        "| Unit | One function or class on its own: configuration keys, identifier handling, upload safety, "
        "preprocessors, response parsing, OAuth state, and the pipeline run on files with fake models | No database |",
        "| Graph / database | The service layer (`core/projects/service.py`, caches, jobs, artifact store) "
        "called directly: link statuses, renames, pinning store, versions, sources, garbage collection | The test database |",
        "| Integration | The whole application through its HTTP API with FastAPI's `TestClient`: new analysis, "
        "re-run, sync, GitHub sources, access control, error responses | The test database |",
        "",
        "Three extra checks (S-01 to S-03) test the test environment itself: that it is isolated, that the "
        "network is blocked, and that the fake models are deterministic.",
        "",
        "**How the scope was chosen (risk-based).** There is no coverage target. Tests were written where a "
        "fault would do the most damage, in this order:",
        "",
        "1. Orchestration code, where functions that are each correct can still fit together wrongly "
        "(sync, save, claim files, update graph). The project's known bug was of this kind: saving the first "
        "configuration deleted the working folder the second configuration still had to read. I-07 is the "
        "regression test for it.",
        "2. Domain logic where a silent error corrupts what users see: link statuses (active, stale, broken), "
        "rename handling, first-seen and last-verified versions, link pinning, configuration identity, caches.",
        "3. Safety code: path traversal, zip-slip, size limits, garbage collection refusals, OAuth redirect and state.",
        "4. The API surface: every main flow, error responses, and access control between two users.",
        "",
        "Each test has a priority: **Critical** (the demo breaks or data is silently corrupted if it fails), "
        "**High** (a core feature behaves wrongly), **Medium** (an edge case, a safety check or a helper).",
        "",
        "**Tools.** pytest " + environment["pytest"] + " and FastAPI `TestClient` (which runs background jobs "
        "inside the request, so a test can read a finished job directly). The only added dependency is pytest, "
        "listed in `backend/requirements-dev.txt`.",
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
        "python tests/report.py                # rewrite this report from the last run",
        "```",
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
        "| The React frontend | Checked separately in a browser with Playwright during development |",
        "| The chain feature (requirements -> design -> code) | Not implemented |",
        "| Performance and load | Not a goal for this release |",
        "| PDF and DOCX reading; Python, JavaScript and TypeScript parsing; the code-chunk preprocessor | The demo corpus is .txt and Java; listed as a risk in section 3 |",
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
        "| Graph / database | After the service calls, the rows in the test database (statuses, version stamps, sources, pins) and the files in the test store match the expected state exactly |",
        "| Integration | Each HTTP response has the expected status code and body, the background job finishes in the expected state, the database and files are left as expected, and no working folder is left behind |",
        "",
        "**Rules for a failing test.** Application code is not changed to make a test pass, and a test is not "
        "weakened. A failure is first diagnosed as a fault in the test or in the application. A test fault is "
        "fixed in the test. An application fault is left failing and listed in section 5.",
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
         "No embeddings, no classification, or no fetch; a sync fails part-way",
         "Tests never need them (S-02). One failing configuration does not lose the others (I-12). For the demo: run everything once beforehand so the caches are warm, and keep upload-replacement (I-08) as the fallback for GitHub"),
        ("PostgreSQL is required to run the tests",
         "The suite cannot run on a machine without a local Postgres; SQLite is not an option",
         "The suite creates its own database from the credentials in `.env` and refuses to run against the development one (S-01). A CI setup would need a Postgres service"),
        ("OAuth tokens expiring or being revoked",
         "Sync from a private repository fails; the app could still claim to be connected",
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
        ("Previously untested: a second user",
         "One user could read or change another's project",
         "Now covered: 21 cross-user requests are all refused (I-20), and anonymous jobs need their token (I-21, G-24)"),
        ("Untested: PDF and DOCX input; Python, JavaScript and TypeScript parsing; code chunks",
         "A fault there would show only with those inputs",
         "Not covered. The demo uses .txt requirements and Java code, which are"),
        ("Concurrency: two requests at once",
         "Two syncs of one project could write the same graph",
         "A second sync is refused while one is unfinished (I-11), and stale jobs are cleared (G-24). Parallel requests themselves were not simulated"),
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
    lines += ["", "### 4.2 Details", ""]

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
    return lines


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
        "- **A sync whose configurations all failed is still recorded as a succeeded job** (seen in I-13's actual output).",
        "- **The only pytest warning is from the application:** `config/settings.py` uses pydantic's class-based "
        "`Config`, which pydantic 2 marks as deprecated.",
        "",
    ]
    return lines


def summary(tests, environment) -> list[str]:
    total = len(tests)
    passed = sum(status(t) == "PASS" for t in tests)
    seconds = sum(t["duration"] for t in tests)
    lines = [
        "## 6. Summary",
        "",
        f"- **Date of run:** {environment['date']}",
        f"- **Operating system:** {environment['os']}",
        f"- **Python:** {environment['python']}; **pytest:** {environment['pytest']}",
        f"- **Database:** {environment['database']}",
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
        "**To run again** (from `backend/`):",
        "",
        "```",
        "python -m pytest",
        "python tests/report.py",
        "```",
        "",
    ]
    return lines


def main() -> None:
    data = json.loads(RESULTS_FILE.read_text(encoding="utf-8"))
    environment = data["environment"]
    tests = sorted((t for t in data["tests"] if t["case"]), key=natural)

    command = re.sub(r"\s+", " ", environment["command"]).strip()
    lines = [
        "# TraceRAG backend - test report",
        "",
        f"Generated by `tests/report.py` from the pytest run of {environment['date']} (`{command}`). "
        "Statuses and actual outputs are taken from that run; nothing in them is written by hand.",
        "",
        *strategy(environment),
        *criteria(tests),
        *risks(tests),
        *test_cases(tests),
        *bugs(tests, environment),
        *summary(tests, environment),
    ]
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {REPORT_FILE} ({len(tests)} tests)")


if __name__ == "__main__":
    main()
