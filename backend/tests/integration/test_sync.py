"""Updating one side of an analysis: the analysis re-run over its refreshed files."""

import logging
from hashlib import sha256

from sqlalchemy import func, select

from api import sync_jobs
from core import jobs
from core.db.models import ElementLink
from core.projects import artifact_store
from tests.helpers import (
    ARCHITECTURE_DOCUMENT, CODE, REQUIREMENTS, SETTINGS, analyse_and_save, analysis_path, get,
    link_pairs, new_project, pending_uploads, report, run_analysis, save_analysis, short, side_ids,
    sign_up, stage, states, sync, update_side, versions_of,
)
from tests.recorder import case

# Names one method only: login() is the only one that takes a passphrase.
NEW_REQUIREMENT = "A passphrase is required.\n"

# Shares only short, common words with the requirement below: close enough to
# be retrieved ahead of login(), with nothing in it the classifier would link.
PAGER = "public class Pager {\n\n    public void page() {\n        this.then();\n        this.from();\n    }\n}\n"
TWO_METHOD_REQUIREMENT = ("requirements", "reqs", {"UC9.txt": "A visitor can login and then logout from this page."})
AUTH_ONLY = {"Auth.java": CODE[2]["Auth.java"]}


def pins_stored(db, config_id: int) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(ElementLink).where(ElementLink.config_id == config_id))


def add_unrelated_code_and_update(client, headers, project, config) -> dict:
    """Upload the code again with Pager.java added, update, and return what the update did."""
    answer = update_side(client, headers, project, config, "target", {**AUTH_ONLY, "Pager.java": PAGER})
    outcome = answer["job"]["result"]
    outcome["links"] = link_pairs(get(client, headers, f"/analyses/{outcome['analysis_id']}")["result"]["trace_links"])
    return outcome


# One requirement per sentence, each naming one method; the extra sentence
# names nothing in the code.
LOGIN, LOGOUT, BORROW = REQUIREMENTS[2]["UC1.txt"].strip(), REQUIREMENTS[2]["UC2.txt"].strip(), REQUIREMENTS[2]["UC3.txt"].strip()
EXTRA = "Every account is locked after five failed attempts."
SENTENCES = dict(source_preprocessor="sentence", source_output_level="sentence", dependency_expansion_depth=0)


def pins_offered(monkeypatch) -> list:
    """Record the pins each update's run is handed."""
    real, offered = sync_jobs.build_pipeline_response, []

    def run(**settings):
        offered.append(settings["pinned_links"])
        return real(**settings)

    monkeypatch.setattr(sync_jobs, "build_pipeline_response", run)
    return offered


def failing_pipeline(monkeypatch) -> None:
    """Make every run of the pipeline during an update raise."""
    def run(**settings):
        raise RuntimeError("the model could not be reached")

    monkeypatch.setattr(sync_jobs, "build_pipeline_response", run)


@case(
    id="I-08",
    feature="Update / replace a side by upload",
    level="integration",
    priority="Critical",
    why="Requirements live on someone's machine, so uploading new files is the only way they change. This is the demo's 'requirements changed' step.",
    preconditions="A saved requirements->code analysis over UC1-UC4",
    input="Stage a new requirements set (UC1-UC3 unchanged, UC4 removed, new UC5 about the passphrase), then update the requirements side with it",
    expected="Staging reports 4 files, 3 matched, 1 missing: UC5 added and UC4 removed. The update creates version 2: requirements refreshed, code carried over, 4 links (+1). The report: 3 valid, 1 new (UC5->login), none lost; UC4 is not listed as uncovered; the staged upload is consumed",
)
def test_update_with_uploaded_requirements(client, record):
    """New requirement files handed over for an update become the analysis's requirements, and the report follows."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    old_ref = get(client, headers, f"{analysis_path(project, config)}/sources")[0]["last_sync_ref"]

    files = {name: text for name, text in REQUIREMENTS[2].items() if name != "UC4.txt"}
    files["UC5.txt"] = NEW_REQUIREMENT
    answer = update_side(client, headers, project, config, "source", files)
    staged, result = answer["staged"], answer["job"]["result"]
    record(f"staged: {staged['file_count']} files, {staged['matched']} matched, {staged['missing']} missing")

    links = report(client, headers, project, config)
    new_ref = get(client, headers, f"{analysis_path(project, config)}/sources")[0]["last_sync_ref"]
    record(f"update: {result['detail']}")
    record(f"files: {staged['changes']['files']}")
    record(f"sides: {[(s['kind'], 'refreshed' if s['refreshed'] else 'carried over') for s in result['sources']]}")
    record(f"run: {result['trace_links']} links (+{result['added']} -{result['removed']})")
    record(f"report: {links['summary']}; new {[k for k, v in states(links).items() if v == 'new']}")
    record(f"requirements fingerprint changed: {old_ref != new_ref}; "
           f"staged upload still on disk: {staged['upload_id'] in pending_uploads()}")

    assert (staged["file_count"], staged["matched"], staged["missing"]) == (4, 3, 1)
    assert (staged["changes"]["files"]["added"], staged["changes"]["files"]["removed"]) == (["UC5.txt"], ["UC4.txt"])
    assert result["version_number"] == 2
    assert {s["kind"]: s["refreshed"] for s in result["sources"]} == {"requirements": True, "code": False}
    assert (result["trace_links"], result["added"]) == (4, 1)
    assert links["summary"] == {"valid": 3, "no_longer_found": 0, "broken": 0, "new": 1, "uncovered": 0}
    assert states(links)[("UC5.txt", "Auth.java::Auth::login")] == "new"
    assert old_ref != new_ref
    assert staged["upload_id"] not in pending_uploads()


@case(
    id="I-26",
    feature="Update / link pinning end to end",
    level="integration",
    priority="Critical",
    why="The research finding the tool exists to fix: adding unrelated code pushes an existing link out of the top-k, and it disappears though nothing rejected it.",
    preconditions="A New Analysis saved with top-k 2, where one requirement is linked to both login() and logout()",
    input="Add an unrelated class (Pager) that ranks above login() for that requirement, upload the new code, and update the code side",
    expected="Pins for the 2 links were stored when the analysis was saved, and both links are still there after the update: 2 links, 0 removed. The update logs that 1 link was carried over, and no warning that the pins matched nothing",
)
def test_no_link_is_lost_when_unrelated_code_is_added(client, db, record, caplog):
    """The first update after a New Analysis re-offers that analysis's links, so none is lost to a shifted top-k."""
    headers = sign_up(client)
    project = new_project(client, headers)
    first = analyse_and_save(client, headers, project, source=TWO_METHOD_REQUIREMENT,
                             target=("code", "code", AUTH_ONLY), n_results=2)
    before = link_pairs(first["result"]["trace_links"])
    config = first["config_id"]
    pins = pins_stored(db, config)
    record(f"links after the New Analysis: {sorted(before)}")
    record(f"'element_links' present in the result the client is given: {'element_links' in first['result']}")
    record(f"pins stored for the analysis after saving: {pins}")
    assert len(before) == 2

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        after = add_unrelated_code_and_update(client, headers, project, config)
    messages = [r.getMessage() for r in caplog.records]
    carried = [m for m in messages if m.startswith("Carried")]
    unmatched = [m for m in messages if "pinned source identifier" in m]
    record(f"links after adding Pager.java and updating: {sorted(after['links'])}")
    record(f"update reports: {after['trace_links']} links, +{after['added']} -{after['removed']}")
    record(f"pipeline log: {carried}; 'no match' warnings: {len(unmatched)}")

    assert after["links"] == before, f"lost: {sorted(before - after['links'])}"
    assert after["removed"] == 0
    assert pins == 2
    assert carried == ["Carried 1 link(s) from the last run into classification"]
    assert unmatched == []


@case(
    id="I-31",
    feature="Update / link pinning end to end",
    level="integration",
    priority="High",
    why="Pins are replaced by every run, not only by a save. A re-run in between must leave pins that the next update can still use.",
    preconditions="The same analysis as I-26, re-run once since the New Analysis",
    input="Add the unrelated Pager class, upload the new code, and update the code side",
    expected="The re-run stored 2 pins; after adding Pager both links are still there, 0 removed, and the log says 1 link was carried over",
)
def test_pinning_holds_after_a_rerun(client, db, record, caplog):
    """After a re-run, the next update re-offers that run's links and none is lost."""
    headers = sign_up(client)
    project = new_project(client, headers)
    first = analyse_and_save(client, headers, project, source=TWO_METHOD_REQUIREMENT,
                             target=("code", "code", AUTH_ONLY), n_results=2)
    before = link_pairs(first["result"]["trace_links"])
    config = first["config_id"]

    rerun = client.post(f"/analyses/{first['analysis_id']}/rerun", headers=headers, json={})
    assert rerun.status_code == 201, rerun.text
    pins = pins_stored(db, config)
    record(f"pins stored after one re-run: {pins}")

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        after = add_unrelated_code_and_update(client, headers, project, config)
    carried = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Carried")]
    record(f"links after adding Pager.java and updating: {sorted(after['links'])}")
    record(f"update reports: {after['trace_links']} links, +{after['added']} -{after['removed']}")
    record(f"pipeline log: {carried}")

    assert pins == 2
    assert after["links"] == before and after["removed"] == 0
    assert carried == ["Carried 1 link(s) from the last run into classification"]


@case(
    id="I-33",
    feature="Link pinning / saving a result that carries no pairs",
    level="integration",
    priority="High",
    why="An older saved result, or another client, does not send the pairs. Saving it must work, and must never touch the pins of another analysis.",
    preconditions="A project with one saved New Analysis holding 2 pins",
    input="Save two more New Analyses of the same files into the same project: one with the 'element_links' field removed, one with it sent as an empty list",
    expected="Both saves succeed as analyses of their own, each with 0 pins. The first analysis still has its 2 pins after both",
)
def test_saving_a_result_without_pairs_touches_no_other_analysis(client, db, record):
    """A result that does not carry pairs is saved without any, and every other analysis keeps its own."""
    headers = sign_up(client)
    project = new_project(client, headers)
    run = dict(source=TWO_METHOD_REQUIREMENT, target=("code", "code", AUTH_ONLY), n_results=2)
    config = analyse_and_save(client, headers, project, **run)["config_id"]
    start = pins_stored(db, config)

    def save_changed(change) -> tuple[int, int]:
        """A fresh run of the same files and settings, saved after `change` has edited its result."""
        result = run_analysis(client, headers, project, **run)
        saved = save_analysis(client, headers, project, change(result), n_results=2)
        return saved.status_code, pins_stored(db, saved.json()["config_id"])

    without = save_changed(lambda r: {k: v for k, v in r.items() if k != "element_links"})
    empty = save_changed(lambda r: {**r, "element_links": []})
    configs = get(client, headers, f"/projects/{project}/configs")
    record(f"pins of the first analysis: {start} before, {pins_stored(db, config)} after")
    record(f"saved with no 'element_links' field: status and own pins {without}")
    record(f"saved with 'element_links': []: status and own pins {empty}")
    record(f"analyses (id, runs): {[(c['config_id'], c['analysis_count']) for c in configs]}")

    assert start == 2 and pins_stored(db, config) == 2
    assert without == (201, 0) and empty == (201, 0)
    assert [c["analysis_count"] for c in configs] == [1, 1, 1]


@case(
    id="I-34",
    feature="Link pinning / pairs stay with the saving user's analysis",
    level="integration",
    priority="High",
    why="The pairs arrive from the browser. They must only ever be stored against an analysis in a project the caller owns.",
    preconditions="User A has a saved New Analysis with 2 pins. User B is a different signed-in user",
    input="B saves a result carrying pairs into B's own project, with the same settings as A. Then B posts that result to A's project",
    expected="B's own analysis gets its 2 pins. The post to A's project is refused with 404. A's pins are 2 before and after both",
)
def test_pairs_from_one_user_never_reach_another_users_analysis(client, db, record):
    """Pairs sent with a save are stored for the saver's own project and nowhere else."""
    owner, other = sign_up(client), sign_up(client)
    run = dict(source=TWO_METHOD_REQUIREMENT, target=("code", "code", AUTH_ONLY), n_results=2)
    owners_project = new_project(client, owner)
    owners_config = analyse_and_save(client, owner, owners_project, **run)["config_id"]
    before = pins_stored(db, owners_config)

    others_project = new_project(client, other)
    saved = analyse_and_save(client, other, others_project, **run)
    others_config = saved["config_id"]
    forged = {**saved["result"], "element_links": [["UC9.txt", "Evil.java::Evil::run()"]]}
    intrusion = client.post(f"/projects/{owners_project}/analyses", headers=other, json={
        "config": {**SETTINGS, "n_results": 2}, "result": forged,
    })
    record(f"A's pins before: {before}; after B's own save and B's post to A's project: {pins_stored(db, owners_config)}")
    record(f"B's own analysis ({others_config != owners_config and 'a different one'}): {pins_stored(db, others_config)} pins")
    record(f"B posting to A's project: {intrusion.status_code}")

    assert others_config != owners_config
    assert pins_stored(db, others_config) == 2
    assert intrusion.status_code == 404
    assert before == 2 and pins_stored(db, owners_config) == 2


@case(
    id="I-09",
    feature="Update / analyses do not share sides",
    level="integration",
    priority="High",
    why="The old model shared one requirements source between every pair, so updating one relation left another silently out of date. Each analysis is now updated on its own.",
    preconditions="One project with requirements->code and requirements->architecture_document analyses, both on the same requirements",
    input="Update the requirements side of the code analysis with a new UC5",
    expected="The code analysis moves to version 2 with 4 links. The document analysis stays at version 1 with 1 run, and its requirements side keeps its old fingerprint",
)
def test_updating_one_analysis_leaves_the_other_alone(client, record):
    """Updating one analysis's side changes nothing about another analysis in the same project."""
    headers = sign_up(client)
    project = new_project(client, headers)
    code = analyse_and_save(client, headers, project)["config_id"]
    document = analyse_and_save(client, headers, project, target=ARCHITECTURE_DOCUMENT,
                                target_preprocessor="single", target_output_level="artifact")["config_id"]
    document_ref = get(client, headers, f"{analysis_path(project, document)}/sources")[0]["last_sync_ref"]

    answer = update_side(client, headers, project, code, "source", {**REQUIREMENTS[2], "UC5.txt": NEW_REQUIREMENT})
    after_ref = get(client, headers, f"{analysis_path(project, document)}/sources")[0]["last_sync_ref"]
    record(f"code analysis: {answer['job']['result']['detail']}")
    record(f"versions: code {versions_of(client, headers, project, code)}, document {versions_of(client, headers, project, document)}")
    record(f"document's requirements fingerprint unchanged: {document_ref == after_ref}")

    assert answer["job"]["result"]["version_number"] == 2 and answer["job"]["result"]["trace_links"] == 4
    assert versions_of(client, headers, project, code) == [(2, 1), (1, 1)]
    assert versions_of(client, headers, project, document) == [(1, 1)]
    assert document_ref == after_ref


@case(
    id="I-10",
    feature="Update / nothing to take in",
    level="integration",
    priority="High",
    why="An update with nothing new must not create an empty version or spend model calls.",
    preconditions="A saved requirements->code analysis whose code side was then taken from a GitHub repository holding the same files",
    input="Update the code side; update it again with the repository unchanged; then ask to update the uploaded requirements side without giving any files",
    expected="First update: the fetched files are the stored ones, so nothing changed and no version is made. Second: started=false, 'Nothing has changed', nothing fetched. Requirements without files: 400. The analysis still has 1 version",
)
def test_update_does_nothing_when_nothing_moved(client, github, record):
    """An unchanged side is left alone, and an uploaded side cannot be updated without its files."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    sides = side_ids(client, headers, project, config)
    github.push("commit-1", CODE[2])
    client.post(f"{analysis_path(project, config)}/sources/{sides['target']}/github", headers=headers,
                json={"repository": "owner/library"})
    fetched = sync(client, headers, project, config, source_id=sides["target"])["job"]["result"]

    idle = sync(client, headers, project, config, source_id=sides["target"])
    no_files = sync(client, headers, project, config, source_id=sides["source"])
    record(f"first update: synced={fetched['synced']}, '{fetched['detail']}'")
    record(f"unchanged repository: started={idle['started']}, '{idle['detail']}'")
    record(f"requirements without files: {no_files['status']} - {no_files['detail']}")
    record(f"versions: {versions_of(client, headers, project, config)}")

    assert fetched["synced"] is False and "Nothing has changed" in fetched["detail"]
    assert idle["started"] is False and "Nothing has changed" in idle["detail"]
    assert no_files["status"] == 400
    assert versions_of(client, headers, project, config) == [(1, 1)]


@case(
    id="I-36",
    feature="Update / the same files again",
    level="integration",
    priority="Critical",
    why="Uploading a side's files again unchanged is common - nobody remembers what they sent last time. It must say so and not make a version that differs from the last in nothing but its number.",
    preconditions="A saved requirements->code analysis over UC1-UC4",
    input="Stage exactly the same 4 requirement files for the requirements side, then ask to update with them",
    expected="Staging reports no file changed. The update does not start: 'Nothing has changed'. The analysis still has 1 version",
)
def test_uploading_the_same_files_changes_nothing(client, record):
    """Identical files are recognised as identical, and no version is made."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]

    answer = update_side(client, headers, project, config, "source", REQUIREMENTS[2])
    record(f"staged changes: {answer['staged']['changes']}")
    record(f"update: started={answer['started']}, '{answer['detail']}'")

    assert answer["staged"]["changes"]["changed"] is False
    assert answer["started"] is False and "Nothing has changed" in answer["detail"]
    assert versions_of(client, headers, project, config) == [(1, 1)]


@case(
    id="I-37",
    feature="Update / whitespace-only change",
    level="integration",
    priority="Critical",
    why="Re-saving files in another editor changes their bytes but not their meaning. That is not a new state of the requirements, and must not make a version.",
    preconditions="A saved requirements->code analysis over UC1-UC4",
    input="Stage the same 4 files with extra spaces and blank lines added to each, then ask to update with them",
    expected="Staging reports 4 files modified but no element changed (meaningful=false). The update does not start: 'No meaningful change'. The analysis still has 1 version",
)
def test_whitespace_only_change_makes_no_version(client, record):
    """Files that differ only in whitespace make no new version."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    reformatted = {name: f"  {text.strip()}  \n\n\n" for name, text in REQUIREMENTS[2].items()}

    answer = update_side(client, headers, project, config, "source", reformatted)
    changes = answer["staged"]["changes"]
    record(f"staged: files modified {changes['files']['modified']}; elements {changes['elements']}; meaningful {changes['meaningful']}")
    record(f"update: started={answer['started']}, '{answer['detail']}'")

    assert changes["changed"] and not changes["meaningful"]
    assert len(changes["files"]["modified"]) == 4
    assert answer["started"] is False and "No meaningful change" in answer["detail"]
    assert versions_of(client, headers, project, config) == [(1, 1)]


@case(
    id="I-38",
    feature="Update / a sentence inserted, end to end",
    level="integration",
    priority="Critical",
    why="With sentence-level requirements, one inserted sentence renames every sentence after it. The pins handed to the run, and the links that come out, must follow the sentences rather than their old positions.",
    preconditions="A New Analysis of one file with 3 sentences (login, logout, borrow), split into sentences, each linked to its method",
    input="Stage the same file with a sentence about account locking inserted after the first, check what staging says, then update",
    expected="Staging: UC.txt modified; 1 sentence added (sentence_1), 2 moved. The run is handed pins for sentence_0, sentence_2 and sentence_3 - the logout and borrow pins followed their sentences. Version 2's links: sentence_0->login, sentence_2->logout, sentence_3->borrow. Version 2 records what changed",
)
def test_inserted_sentence_keeps_every_link_on_its_sentence(client, monkeypatch, record):
    """Pins and links follow sentences that moved when another was inserted above them."""
    headers = sign_up(client)
    project = new_project(client, headers)
    first = analyse_and_save(client, headers, project, source=("requirements", "reqs", {
        "UC.txt": f"{LOGIN} {LOGOUT} {BORROW}\n",
    }), **SENTENCES)
    config = first["config_id"]
    offered = pins_offered(monkeypatch)

    answer = update_side(client, headers, project, config, "source", {"UC.txt": f"{LOGIN} {EXTRA} {LOGOUT} {BORROW}\n"})
    changes = answer["staged"]["changes"]
    result = answer["job"]["result"]
    links = link_pairs(get(client, headers, f"/analyses/{result['analysis_id']}")["result"]["trace_links"])
    pins = {(short(source), short(target)) for source, targets in offered[0].items() for target in targets}
    stored = get(client, headers, f"{analysis_path(project, config)}/versions")[0]["changes"]
    record(f"links before: {sorted(link_pairs(first['result']['trace_links']))}")
    record(f"staged: files {changes['files']['modified']}, elements {changes['elements']}")
    record(f"pins handed to the run: {sorted(pins)}")
    record(f"links after: {sorted(links)}")
    record(f"version {result['version_number']} records: {[(c['role'], c['elements']['added']) for c in stored]}")

    assert link_pairs(first["result"]["trace_links"]) == {
        ("UC.txt::sentence_0", "Auth.java::Auth::login"), ("UC.txt::sentence_1", "Auth.java::Auth::logout"),
        ("UC.txt::sentence_2", "Loans.java::Loans::borrow"),
    }
    assert changes["files"]["modified"] == ["UC.txt"]
    assert changes["elements"]["added"] == ["UC.txt::sentence_1"]
    assert changes["elements"]["moved"] == [
        {"old": "UC.txt::sentence_1", "new": "UC.txt::sentence_2"},
        {"old": "UC.txt::sentence_2", "new": "UC.txt::sentence_3"},
    ]
    assert pins == {
        ("UC.txt::sentence_0", "Auth.java::Auth::login"), ("UC.txt::sentence_2", "Auth.java::Auth::logout"),
        ("UC.txt::sentence_3", "Loans.java::Loans::borrow"),
    }
    assert links == pins
    assert result["version_number"] == 2
    assert [(c["role"], c["elements"]["added"]) for c in stored] == [("source", ["UC.txt::sentence_1"])]


@case(
    id="I-11",
    feature="Update / refusals",
    level="integration",
    priority="High",
    why="Each refusal protects something: an analysis with nothing to restore from, files that are no longer there, and two updates writing the same versions at once.",
    preconditions="A saved requirements->code analysis, and one saved without its files",
    input="Update: the analysis saved without files; a side that does not exist; an expired upload; an empty body; and an update while another update job of the same analysis is unfinished",
    expected="409, 404, 410, 422 and 409 in that order, each with a message saying what is wrong; no new version created by any of them",
)
def test_update_refuses_what_it_cannot_do_safely(client, db, record):
    """An update that cannot be done is refused up front with a reason."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(client, headers, project)
    config = saved["config_id"]
    requirements = side_ids(client, headers, project, config)["source"]
    bare = save_analysis(client, headers, project, {**saved["result"], "upload_id": None}).json()["config_id"]

    refusals = {
        "no stored files": sync(client, headers, project, bare, source_id=requirements),
        "unknown side": sync(client, headers, project, config, source_id=999999, upload_id="A" * 22),
        "expired upload": sync(client, headers, project, config, source_id=requirements, upload_id="A" * 22),
    }
    empty = client.post(f"{analysis_path(project, config)}/sync", headers=headers, json={})
    refusals["empty body"] = {"status": empty.status_code, "detail": "source_id is required"}

    # Another update of this analysis already in flight.
    running = jobs.create_job(db, None, project, jobs.KIND_SYNC, config_id=config)
    refusals["already updating"] = sync(client, headers, project, config, source_id=requirements, upload_id="A" * 22)
    jobs.fail(db, running.job_id, "ended by the test")

    for name, answer in refusals.items():
        record(f"{name}: {answer['status']} - {str(answer['detail'])[:110]}")

    assert [answer["status"] for answer in refusals.values()] == [409, 404, 410, 422, 409]
    assert versions_of(client, headers, project, config) == [(1, 1)]


@case(
    id="I-24",
    feature="Change report / broken links through the API",
    level="integration",
    priority="High",
    why="'Broken' is what the user is told to act on. The report must show the link, both ends named, with the missing end marked and the reason given.",
    preconditions="A saved analysis where UC3 is linked to borrow()",
    input="Update the requirements side with files that no longer include UC3; then GET the change report",
    expected="1 broken link, UC3.txt -> borrow, with source_present=false, target_present=true and source_changed=true; the other 2 links valid",
)
def test_removed_requirement_shows_as_a_broken_link(client, record):
    """A link whose requirement was removed is reported as broken, not dropped."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    files = {name: text for name, text in REQUIREMENTS[2].items() if name != "UC3.txt"}

    update_side(client, headers, project, config, "source", files)
    answer = report(client, headers, project, config)
    broken = [link for link in answer["links"] if link["state"] == "broken"]
    record(f"summary: {answer['summary']}")
    record("broken links: " + str([
        (l["source_id"], l["target_id"].split("::")[-1], l["source_present"], l["target_present"], l["source_changed"])
        for l in broken
    ]))

    assert answer["summary"]["broken"] == 1 and answer["summary"]["valid"] == 2
    link, = broken
    assert link["source_id"] == "UC3.txt" and "borrow" in link["target_id"]
    assert (link["source_present"], link["target_present"], link["source_changed"]) == (False, True, True)


@case(
    id="I-13",
    feature="Update / the run fails",
    level="integration",
    priority="High",
    why="If nothing ran, nothing about the analysis changed. A version with no run in it is a state of the files that nothing ever analysed.",
    preconditions="A saved requirements->code analysis in version 1",
    input="Update the requirements side with changed files, with the pipeline made to raise",
    expected="The job fails with the error; no new version is created; the requirements side keeps its old fingerprint; no working folder is left",
)
def test_a_failed_update_changes_nothing(client, monkeypatch, record):
    """An update whose run failed leaves the analysis exactly as it was."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    old_ref = get(client, headers, f"{analysis_path(project, config)}/sources")[0]["last_sync_ref"]
    before = pending_uploads()
    failing_pipeline(monkeypatch)

    answer = update_side(client, headers, project, config, "source", {"UC1.txt": "Changed text about login.\n"})
    versions = versions_of(client, headers, project, config)
    new_ref = get(client, headers, f"{analysis_path(project, config)}/sources")[0]["last_sync_ref"]
    record(f"job state: {answer['job']['state']} - {answer['job']['error']}")
    record(f"versions (number, runs): {versions}")
    record(f"requirements fingerprint unchanged: {old_ref == new_ref}; "
           f"new working folders: {sorted(pending_uploads() - before)}")

    assert answer["job"]["state"] == "failed" and "could not be reached" in answer["job"]["error"]
    assert old_ref == new_ref
    assert pending_uploads() <= before
    assert versions == [(1, 1)], f"an empty version was left behind: {versions}"


@case(
    id="I-39",
    feature="Change report / across several versions",
    level="integration",
    priority="Critical",
    why="The report can compare any two versions, not only neighbours. Across several updates the id maps of every version in between must be followed, or a sentence that moved twice reads as lost.",
    preconditions="A sentence-level analysis of UC.txt (login, logout, borrow) at version 1",
    input="Version 2 inserts a sentence after login; version 3 removes the borrow sentence. Read the report for 1 -> 3, then for 2 -> 3 by default, then ask for 3 -> 1 and for version 9",
    expected="1 -> 3: login and logout valid (logout now sentence_2), borrow broken with its source gone; version 2 and 3's file changes listed. Default report is 2 -> 3. 3 -> 1 and version 9 are refused with 409",
)
def test_report_spans_several_versions(client, record):
    """A report between distant versions follows every version's id map in between."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project, source=("requirements", "reqs", {
        "UC.txt": f"{LOGIN} {LOGOUT} {BORROW}\n",
    }), **SENTENCES)["config_id"]
    update_side(client, headers, project, config, "source", {"UC.txt": f"{LOGIN} {EXTRA} {LOGOUT} {BORROW}\n"})
    update_side(client, headers, project, config, "source", {"UC.txt": f"{LOGIN} {EXTRA} {LOGOUT}\n"})

    span = report(client, headers, project, config, base=1, head=3)
    default = report(client, headers, project, config)
    backwards = client.get(f"{analysis_path(project, config)}/report", headers=headers, params={"base": 3, "head": 1})
    missing = client.get(f"{analysis_path(project, config)}/report", headers=headers, params={"head": 9})
    record(f"1 -> 3: {states(span)}")
    record(f"versions listed: {[(v['version_number'], v['changes'][0]['elements']) for v in span['versions']]}")
    record(f"default: {default['base_version']} -> {default['head_version']}")
    record(f"3 -> 1: {backwards.status_code} {backwards.json()['detail']}; version 9: {missing.status_code} {missing.json()['detail']}")

    assert states(span) == {
        ("UC.txt::sentence_0", "Auth.java::Auth::login"): "valid",
        ("UC.txt::sentence_2", "Auth.java::Auth::logout"): "valid",
        ("UC.txt::sentence_2", "Loans.java::Loans::borrow"): "broken",
    }
    broken = next(link for link in span["links"] if link["state"] == "broken")
    assert not broken["source_present"] and broken["source_changed"]
    assert [v["version_number"] for v in span["versions"]] == [2, 3]
    assert (default["base_version"], default["head_version"]) == (2, 3)
    assert (backwards.status_code, missing.status_code) == (409, 409)


@case(
    id="I-43",
    feature="Change report / a sentence inserted and removed again",
    level="integration",
    priority="Critical",
    why="Across a version that inserted a sentence and one that removed it again, every original sentence is back where it was. Reporting their links as broken would be a false alarm.",
    preconditions="A sentence-level analysis of UC.txt (login, logout, borrow) at version 1",
    input="Version 2 inserts a sentence after login; version 3 removes it again. Read the report for 1 -> 3",
    expected="All 3 links valid, none broken, no longer found or new",
)
def test_inserted_and_removed_sentence_leaves_links_valid(client, record):
    """A sentence inserted and removed again leaves every link valid."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project, source=("requirements", "reqs", {
        "UC.txt": f"{LOGIN} {LOGOUT} {BORROW}\n",
    }), **SENTENCES)["config_id"]
    update_side(client, headers, project, config, "source", {"UC.txt": f"{LOGIN} {EXTRA} {LOGOUT} {BORROW}\n"})
    update_side(client, headers, project, config, "source", {"UC.txt": f"{LOGIN} {LOGOUT} {BORROW}\n"})

    span = report(client, headers, project, config, base=1, head=3)
    record(f"1 -> 3: {states(span)}; {span['summary']}")

    assert set(states(span).values()) == {"valid"}
    assert span["summary"]["valid"] == 3 and span["summary"]["broken"] == 0



@case(
    id="I-44",
    feature="Change report / net change over several versions",
    level="integration",
    priority="Critical",
    why="Between two distant versions the user wants what is different now, not every step taken to get there. A file added in one version and removed in a later one is no difference at all.",
    preconditions="A saved requirements->code analysis over UC1-UC4 (whole documents)",
    input="v2 adds UC5; v3 removes UC5 again and edits UC1. Read the report for 1 -> 3",
    expected="Net, requirements side: only UC1.txt, modified, touched by 1 link whose source changed; UC5.txt does not appear; the side is whole documents. Step by step: v2 and v3, with UC5 added in v2",
)
def test_net_change_leaves_out_what_came_and_went(client, record):
    """The net change between two versions shows only what differs between them."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    update_side(client, headers, project, config, "source", {**REQUIREMENTS[2], "UC5.txt": NEW_REQUIREMENT})
    edited = {**REQUIREMENTS[2], "UC1.txt": "A visitor performs a login with a passphrase, twice.\n"}
    update_side(client, headers, project, config, "source", edited)

    span = report(client, headers, project, config, base=1, head=3)
    source = next(side for side in span["net"] if side["role"] == "source")
    rows = [(row["path"], row["change"], row["links"], row["changed"]) for row in source["files"]]
    steps = [(v["version_number"], v["changes"][0]["files"]["added"]) for v in span["versions"]]
    record(f"net rows: {rows}; whole documents: {source['whole_documents']}; counts {source['counts']}")
    record(f"step by step (version, files added): {steps}")

    assert span["net_available"]
    assert rows == [("UC1.txt", "modified", 1, 1)]
    assert source["whole_documents"] is True
    assert steps == [(2, ["UC5.txt"]), (3, [])]


@case(
    id="I-45",
    feature="Change report / a sentence that came and went",
    level="integration",
    priority="High",
    why="An element added in one version and removed in a later one must not appear in the net change, even though each version recorded it.",
    preconditions="A sentence-level analysis of UC.txt (login, logout, borrow) at version 1",
    input="v2 inserts a sentence after login; v3 removes it. Read the report for 1 -> 3",
    expected="Net: nothing changed on either side (every count 0, no file rows). Step by step: v2 added sentence_1, v3 removed it",
)
def test_net_change_of_a_sentence_that_came_and_went(client, record):
    """A sentence inserted and removed again is not part of the net change."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project, source=("requirements", "reqs", {
        "UC.txt": f"{LOGIN} {LOGOUT} {BORROW}\n",
    }), **SENTENCES)["config_id"]
    update_side(client, headers, project, config, "source", {"UC.txt": f"{LOGIN} {EXTRA} {LOGOUT} {BORROW}\n"})
    update_side(client, headers, project, config, "source", {"UC.txt": f"{LOGIN} {LOGOUT} {BORROW}\n"})

    span = report(client, headers, project, config, base=1, head=3)
    steps = [(v["version_number"], v["changes"][0]["elements"]["added"], v["changes"][0]["elements"]["removed"])
             for v in span["versions"]]
    record(f"net: {[(side['role'], side['counts'], side['files']) for side in span['net']]}")
    record(f"step by step (version, added, removed): {steps}")

    assert all(not side["files"] and not any(side["counts"].values()) for side in span["net"])
    assert steps == [(2, ["UC.txt::sentence_1"], []), (3, [], ["UC.txt::sentence_1"])]


@case(
    id="I-46",
    feature="Change report / a stored file is gone",
    level="integration",
    priority="Medium",
    why="Blobs can be removed by hand or by garbage collection. The report must still answer, without the net change it cannot work out, and say so.",
    preconditions="An analysis at v2 whose v1 requirements included UC4.txt, removed in v2",
    input="Delete UC4.txt's blob from the store (and the trash), then read the report for 1 -> 2",
    expected="200; net_available=false and no net sides; the step by step list for v2 is still there; the links are still reported",
)
def test_report_without_the_net_change_when_a_file_is_gone(client, record):
    """A missing blob drops the net change, not the report."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    update_side(client, headers, project, config, "source",
                {name: text for name, text in REQUIREMENTS[2].items() if name != "UC4.txt"})
    digest = sha256(REQUIREMENTS[2]["UC4.txt"].encode("utf-8")).hexdigest()
    for path in (artifact_store.blob_path(digest), artifact_store.TRASH_ROOT / digest):
        path.unlink(missing_ok=True)

    span = report(client, headers, project, config, base=1, head=2)
    record(f"net_available: {span['net_available']}; net sides: {len(span['net'])}; "
           f"step by step versions: {[v['version_number'] for v in span['versions']]}; summary {span['summary']}")

    assert span["net_available"] is False and span["net"] == []
    assert [v["version_number"] for v in span["versions"]] == [2]
    assert span["summary"]["valid"] == 3



@case(
    id="I-47",
    feature="Change report / line diff of a changed file",
    level="integration",
    priority="Medium",
    why="Seeing what changed inside a modified file is what tells a reader whether a link's source really changed. It is read from the stored files, and must stay short enough to read.",
    preconditions="An analysis at v2, where v2 edited UC1.txt and added a 300-line UC9.txt",
    input="The line diff of UC1.txt for 1 -> 2; of UC9.txt; and of a .pdf path",
    expected="UC1.txt: the old line removed and the new line added. UC9.txt: cut at 200 lines and marked truncated. The .pdf: 400",
)
def test_line_diff_of_a_changed_file(client, record):
    """A changed text file's diff is read from the stored files, capped, and refused for PDFs."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    long_file = "".join(f"Line {number} of a long requirement.\n" for number in range(300))
    update_side(client, headers, project, config, "source", {
        **REQUIREMENTS[2], "UC1.txt": "A visitor performs a login with a passphrase, twice.\n", "UC9.txt": long_file,
    })
    endpoint = f"{analysis_path(project, config)}/report/diff"

    def diff(name: str):
        return client.get(endpoint, headers=headers, params={"base": 1, "head": 2, "role": "source", "path": name})

    edited, added, pdf = diff("UC1.txt").json(), diff("UC9.txt").json(), diff("srs.pdf")
    record(f"UC1.txt: {edited}")
    record(f"UC9.txt: {len(added['lines'])} lines, truncated={added['truncated']}; pdf: {pdf.status_code}")

    assert "-A visitor performs a login with a passphrase." in edited["lines"]
    assert "+A visitor performs a login with a passphrase, twice." in edited["lines"]
    assert (len(added["lines"]), added["truncated"]) == (200, True)
    assert pdf.status_code == 400
