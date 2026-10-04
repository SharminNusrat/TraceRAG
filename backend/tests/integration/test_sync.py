"""Syncing a pair: every configuration re-run over the refreshed files."""

import logging

from sqlalchemy import func, select

from api import sync_jobs
from core import jobs
from core.db.models import ElementLink
from tests.helpers import (
    ARCHITECTURE_DOCUMENT, CODE, REQUIREMENTS, SETTINGS, analyse_and_save, get, link_pairs,
    new_project, pending_uploads, run_analysis, save_analysis, sign_up, source_ids, stage, sync,
)
from tests.recorder import case

# Names one method only: login() is the only one that takes a passphrase.
NEW_REQUIREMENT = "A passphrase is required.\n"

# Shares only short, common words with the requirement below: close enough to
# be retrieved ahead of login(), with nothing in it the classifier would link.
PAGER = "public class Pager {\n\n    public void page() {\n        this.then();\n        this.from();\n    }\n}\n"
TWO_METHOD_REQUIREMENT = ("requirements", "reqs", {"UC9.txt": "A visitor can login and then logout from this page."})
AUTH_ONLY = {"Auth.java": CODE[2]["Auth.java"]}


def project_with_two_configs(client):
    """A saved requirements->code run, re-run at file level: one pair, two configurations."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(client, headers, project)
    config = {**get(client, headers, f"/analyses/{saved['analysis_id']}")["config"], "target_output_level": "file"}
    rerun = client.post(f"/analyses/{saved['analysis_id']}/rerun", headers=headers, json={"config": config})
    assert rerun.status_code == 201, rerun.text
    return headers, project


def outcomes(answer) -> list[str]:
    """Each configuration's result in a sync, as text for the report."""
    return [
        f"config {c['config_id']}: "
        + (f"FAILED - {c['error']}" if c["error"] else f"ok, {c['trace_links']} links (+{c['added']} -{c['removed']})")
        for c in answer["job"]["result"]["configs"]
    ]


def version_numbers(client, headers, project) -> list[tuple[int, int]]:
    """The project's versions, newest first, as (number, runs in it)."""
    return [(v["version_number"], v["analysis_count"]) for v in get(client, headers, f"/projects/{project}/versions")]


def pins_stored(db, config_id: int) -> int:
    db.expire_all()
    return db.scalar(select(func.count()).select_from(ElementLink).where(ElementLink.config_id == config_id))


def add_unrelated_code_and_sync(client, headers, project) -> dict:
    """Upload the code again with Pager.java added, sync, and return the configuration's outcome."""
    staged = stage(client, headers, project, source_ids(client, headers, project)["code"],
                   {**AUTH_ONLY, "Pager.java": PAGER}).json()
    answer = sync(client, headers, project, replacements={staged["source_id"]: staged["upload_id"]})
    outcome = answer["job"]["result"]["configs"][0]
    outcome["links"] = link_pairs(get(client, headers, f"/analyses/{outcome['analysis_id']}")["result"]["trace_links"])
    return outcome


def failing_for(monkeypatch, should_fail) -> None:
    """Make the pipeline raise for the configurations `should_fail` picks."""
    real = sync_jobs.build_pipeline_response

    def run(**settings):
        if should_fail(settings):
            raise RuntimeError("the model could not be reached")
        return real(**settings)

    monkeypatch.setattr(sync_jobs, "build_pipeline_response", run)


@case(
    id="I-07",
    feature="Sync / several configurations in one pair",
    level="integration",
    priority="Critical",
    why="Regression test for a real bug: the first configuration's save deleted the working folder, so every later configuration failed with 'Path does not exist'.",
    preconditions="One pair (requirements->code) with 2 configurations: method level and file level",
    input="POST /projects/{id}/sync for the pair with force=true",
    expected="Job succeeds; BOTH configurations succeed with 3 links each and no error; version 2 holds 2 runs, each with its 2 artifacts; no working folder left",
)
def test_sync_runs_every_configuration_of_the_pair(client, record):
    """Every configuration of the pair is re-run over the same files, not only the first."""
    headers, project = project_with_two_configs(client)
    before = pending_uploads()

    answer = sync(client, headers, project, force=True)
    record(f"job: {answer['job']['state']} - {answer['job']['result']['detail']}")
    for line in outcomes(answer):
        record(line)
    record(f"versions (number, runs): {version_numbers(client, headers, project)}")
    record(f"working folders left: {sorted(pending_uploads() - before)}")

    configs = answer["job"]["result"]["configs"]
    assert answer["job"]["state"] == "succeeded"
    assert len(configs) == 2
    assert [c["error"] for c in configs] == [None, None]
    assert [c["trace_links"] for c in configs] == [3, 3]
    assert version_numbers(client, headers, project) == [(2, 2), (1, 2)]
    for config in configs:
        artifacts = get(client, headers, f"/analyses/{config['analysis_id']}")["artifacts"]
        assert sorted(a["role"] for a in artifacts) == ["source", "target"]
        assert all(a["files_available"] for a in artifacts)
    assert pending_uploads() == before


@case(
    id="I-08",
    feature="Sync / replace a source by upload",
    level="integration",
    priority="Critical",
    why="Requirements live on someone's machine, so uploading new files is the only way they change. This is the demo's 'requirements changed' step.",
    preconditions="A saved requirements->code run over UC1-UC4",
    input="Stage a new requirements set (UC1-UC3 unchanged, UC4 removed, new UC5 about the passphrase), then sync with it",
    expected="Staging reports 4 files, 3 matched, 1 missing. Sync creates version 2: requirements refreshed, code carried over, 4 links (+1). The graph has 4 active links and 1 node gone (UC4); the staged upload is consumed",
)
def test_sync_with_uploaded_requirements_updates_the_graph(client, record):
    """New requirement files handed over for a sync become the project's requirements, and the graph follows."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    requirements = source_ids(client, headers, project)["requirements"]
    old_ref = get(client, headers, f"/projects/{project}/sources")[0]["last_sync_ref"]

    files = {name: text for name, text in REQUIREMENTS[2].items() if name != "UC4.txt"}
    files["UC5.txt"] = NEW_REQUIREMENT
    staged = stage(client, headers, project, requirements, files).json()
    record(f"staged: {staged['file_count']} files, {staged['matched']} matched, {staged['missing']} missing")

    answer = sync(client, headers, project, replacements={requirements: staged["upload_id"]})
    result = answer["job"]["result"]
    graph = get(client, headers, f"/projects/{project}/graph")
    new_ref = get(client, headers, f"/projects/{project}/sources")[0]["last_sync_ref"]
    record(f"sync: {result['detail']}")
    record(f"sources: {[(s['kind'], 'refreshed' if s['refreshed'] else 'carried over') for s in result['sources']]}")
    for line in outcomes(answer):
        record(line)
    record(f"graph: {graph['summary']}")
    record(f"requirements fingerprint changed: {old_ref != new_ref}; "
           f"staged upload still on disk: {staged['upload_id'] in pending_uploads()}")

    assert (staged["file_count"], staged["matched"], staged["missing"]) == (4, 3, 1)
    assert result["version_number"] == 2
    assert {s["kind"]: s["refreshed"] for s in result["sources"]} == {"requirements": True, "code": False}
    assert result["configs"][0]["error"] is None
    assert (result["configs"][0]["trace_links"], result["configs"][0]["added"]) == (4, 1)
    assert graph["summary"]["links_active"] == 4 and graph["summary"]["nodes_gone"] == 1
    assert old_ref != new_ref
    assert staged["upload_id"] not in pending_uploads()


@case(
    id="I-26",
    feature="Sync / link pinning end to end",
    level="integration",
    priority="Critical",
    why="The research finding the tool exists to fix: adding unrelated code pushes an existing link out of the top-k, and it disappears though nothing rejected it.",
    preconditions="A New Analysis saved with top-k 2, where one requirement is linked to both login() and logout()",
    input="Add an unrelated class (Pager) that ranks above login() for that requirement, upload the new code, and sync",
    expected="Pins for the 2 links were stored when the analysis was saved, and both links are still there after the sync: 2 links, 0 removed. The sync logs that 1 link was carried over, and no warning that the pins matched nothing",
)
def test_no_link_is_lost_when_unrelated_code_is_added(client, db, record, caplog):
    """The first sync after a New Analysis re-offers that analysis's links, so none is lost to a shifted top-k."""
    headers = sign_up(client)
    project = new_project(client, headers)
    first = analyse_and_save(client, headers, project, source=TWO_METHOD_REQUIREMENT,
                             target=("code", "code", AUTH_ONLY), n_results=2)
    before = link_pairs(first["result"]["trace_links"])
    config = get(client, headers, f"/projects/{project}/configs")[0]["config_id"]
    pins = pins_stored(db, config)
    record(f"links after the New Analysis: {sorted(before)}")
    record(f"'element_links' present in the result the client is given: {'element_links' in first['result']}")
    record(f"pins stored for the configuration after saving: {pins}")
    assert len(before) == 2

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        after = add_unrelated_code_and_sync(client, headers, project)
    messages = [r.getMessage() for r in caplog.records]
    carried = [m for m in messages if m.startswith("Carried")]
    unmatched = [m for m in messages if "pinned source identifier" in m]
    record(f"links after adding Pager.java and syncing: {sorted(after['links'])}")
    record(f"sync reports: {after['trace_links']} links, +{after['added']} -{after['removed']}")
    record(f"pipeline log: {carried}; 'no match' warnings: {len(unmatched)}")

    assert after["links"] == before, f"lost: {sorted(before - after['links'])}"
    assert after["removed"] == 0
    assert pins == 2
    assert carried == ["Carried 1 link(s) from the last run into classification"]
    assert unmatched == []


@case(
    id="I-31",
    feature="Sync / link pinning end to end",
    level="integration",
    priority="High",
    why="Shows pinning itself works once pins exist, which isolates the fault in I-26 to how a New Analysis is saved rather than to the pinning mechanism.",
    preconditions="The same project as I-26, but one forced sync has already run since the New Analysis",
    input="Add the unrelated Pager class, upload the new code, and sync",
    expected="The forced sync stored 2 pins; after adding Pager both links are still there, 0 removed, and the log says 1 link was carried over",
)
def test_pinning_holds_once_a_sync_has_stored_the_pins(client, db, record, caplog):
    """After any sync has run, the next one re-offers its links and none is lost."""
    headers = sign_up(client)
    project = new_project(client, headers)
    first = analyse_and_save(client, headers, project, source=TWO_METHOD_REQUIREMENT,
                             target=("code", "code", AUTH_ONLY), n_results=2)
    before = link_pairs(first["result"]["trace_links"])
    config = get(client, headers, f"/projects/{project}/configs")[0]["config_id"]

    sync(client, headers, project, force=True)
    pins = pins_stored(db, config)
    record(f"pins stored after one forced sync: {pins}")

    with caplog.at_level(logging.INFO, logger="core.pipeline"):
        after = add_unrelated_code_and_sync(client, headers, project)
    carried = [r.getMessage() for r in caplog.records if r.getMessage().startswith("Carried")]
    record(f"links after adding Pager.java and syncing: {sorted(after['links'])}")
    record(f"sync reports: {after['trace_links']} links, +{after['added']} -{after['removed']}")
    record(f"pipeline log: {carried}")

    assert pins == 2
    assert after["links"] == before and after["removed"] == 0
    assert carried == ["Carried 1 link(s) from the last run into classification"]


@case(
    id="I-33",
    feature="Link pinning / saving a result that carries no pairs",
    level="integration",
    priority="High",
    why="An older saved result, or another client, does not send the pairs. Reading that as 'this run linked nothing' would delete the pins a later sync depends on.",
    preconditions="A configuration with 2 stored pins, from a saved New Analysis",
    input="Run the same New Analysis twice more into the same configuration, changing only the result before saving: once with the 'element_links' field removed, once with it sent as an empty list",
    expected="All three runs are filed under one configuration. Field removed: the save succeeds and the 2 pins are left untouched. Empty list sent: the pins are cleared, because that run said it linked nothing",
)
def test_saving_a_result_without_pairs_leaves_the_pins_alone(client, db, record):
    """A result that does not mention the pairs changes nothing; only an explicit empty list clears them."""
    headers = sign_up(client)
    project = new_project(client, headers)
    run = dict(source=TWO_METHOD_REQUIREMENT, target=("code", "code", AUTH_ONLY), n_results=2)
    analyse_and_save(client, headers, project, **run)
    config = get(client, headers, f"/projects/{project}/configs")[0]["config_id"]
    start = pins_stored(db, config)

    def save_changed(change) -> int:
        """A fresh run of the same files and settings, saved after `change` has edited its result."""
        result = run_analysis(client, headers, project, **run)
        return save_analysis(client, headers, project, change(result), n_results=2).status_code

    status_without = save_changed(lambda r: {k: v for k, v in r.items() if k != "element_links"})
    after_without = pins_stored(db, config)
    status_empty = save_changed(lambda r: {**r, "element_links": []})
    after_empty = pins_stored(db, config)
    configs = get(client, headers, f"/projects/{project}/configs")
    record(f"pins after the New Analysis: {start}")
    record(f"saved with no 'element_links' field: {status_without}, pins now {after_without}")
    record(f"saved with 'element_links': []: {status_empty}, pins now {after_empty}")
    record(f"configurations (id, runs): {[(c['config_id'], c['analysis_count']) for c in configs]}")

    # All three saves landed on the configuration whose pins are being counted.
    assert [(c["config_id"], c["analysis_count"]) for c in configs] == [(config, 3)]
    assert start == 2
    assert (status_without, after_without) == (201, 2)
    assert (status_empty, after_empty) == (201, 0)


@case(
    id="I-34",
    feature="Link pinning / pairs stay with the saving user's configuration",
    level="integration",
    priority="High",
    why="The pairs now arrive from the browser. They must only ever be stored against the configuration of the project the caller owns.",
    preconditions="User A has a saved New Analysis with 2 pins. User B is a different signed-in user",
    input="B saves a result carrying pairs into B's own project, with the same settings as A. Then B posts that result to A's project",
    expected="B's own configuration gets its 2 pins. The post to A's project is refused with 404. A's pins are 2 before and after both",
)
def test_pairs_from_one_user_never_reach_another_users_configuration(client, db, record):
    """Pairs sent with a save are stored for the saver's own project and nowhere else."""
    owner, other = sign_up(client), sign_up(client)
    run = dict(source=TWO_METHOD_REQUIREMENT, target=("code", "code", AUTH_ONLY), n_results=2)
    owners_project = new_project(client, owner)
    analyse_and_save(client, owner, owners_project, **run)
    owners_config = get(client, owner, f"/projects/{owners_project}/configs")[0]["config_id"]
    before = pins_stored(db, owners_config)

    others_project = new_project(client, other)
    saved = analyse_and_save(client, other, others_project, **run)
    others_config = get(client, other, f"/projects/{others_project}/configs")[0]["config_id"]
    forged = {**saved["result"], "element_links": [["UC9.txt", "Evil.java::Evil::run()"]]}
    intrusion = client.post(f"/projects/{owners_project}/analyses", headers=other, json={
        "config": {**SETTINGS, "n_results": 2}, "result": forged,
    })
    record(f"A's pins before: {before}; after B's own save and B's post to A's project: {pins_stored(db, owners_config)}")
    record(f"B's own configuration ({others_config != owners_config and 'a different one'}): {pins_stored(db, others_config)} pins")
    record(f"B posting to A's project: {intrusion.status_code}")

    assert others_config != owners_config
    assert pins_stored(db, others_config) == 2
    assert intrusion.status_code == 404
    assert before == 2 and pins_stored(db, owners_config) == 2


@case(
    id="I-09",
    feature="Sync / a kind shared by two pairs",
    level="integration",
    priority="High",
    why="Requirements feed two pairs. After syncing one, the other is looking at old requirements and must say so, then catch up on request.",
    preconditions="One project with requirements->code and requirements->architecture_document, both run on the same requirements",
    input="Sync the code pair with changed requirements. Read the pairs. Then sync the document pair with no new files and no force",
    expected="After the first sync only the document pair is out of date. Its sync starts without force, re-runs 1 configuration, and afterwards neither pair is out of date",
)
def test_other_pair_is_marked_out_of_date_and_catches_up(client, record):
    """A pair whose shared source moved on is flagged, and a plain sync brings it up to date."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    analyse_and_save(client, headers, project, target=ARCHITECTURE_DOCUMENT,
                     target_preprocessor="single", target_output_level="artifact")

    def out_of_date():
        return {p["target_kind"]: p["out_of_date"] for p in get(client, headers, f"/projects/{project}/pairs")}

    start = out_of_date()
    requirements = source_ids(client, headers, project)["requirements"]
    staged = stage(client, headers, project, requirements, {**REQUIREMENTS[2], "UC5.txt": NEW_REQUIREMENT}).json()
    sync(client, headers, project, replacements={requirements: staged["upload_id"]})
    after_first = out_of_date()

    catch_up = sync(client, headers, project, target_kind="architecture_document")
    after_second = out_of_date()
    record(f"out of date at the start: {start}")
    record(f"after syncing requirements->code: {after_first}")
    record(f"document pair sync: started={catch_up['started']}, {catch_up['job']['result']['detail']}")
    record(f"after syncing the document pair: {after_second}")

    assert start == {"code": False, "architecture_document": False}
    assert after_first == {"code": False, "architecture_document": True}
    assert catch_up["started"] and len(catch_up["job"]["result"]["configs"]) == 1
    assert after_second == {"code": False, "architecture_document": False}


@case(
    id="I-10",
    feature="Sync / nothing changed",
    level="integration",
    priority="High",
    why="A sync with nothing to do must not create an empty version or spend model calls; but re-running on purpose must still be possible.",
    preconditions="A saved requirements->code run; nothing has changed since",
    input="Sync the pair without force, then with force=true",
    expected="Without force: started=false, 'already up to date', still 1 version. With force: a job runs and creates version 2 with the same 3 links (+0 -0)",
)
def test_sync_does_nothing_unless_something_moved_or_it_is_forced(client, record):
    """An unchanged pair is left alone unless the user asks for a re-run."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)

    idle = sync(client, headers, project)
    versions_after_idle = version_numbers(client, headers, project)
    forced = sync(client, headers, project, force=True)
    record(f"no force: started={idle['started']}, '{idle['detail']}', versions {versions_after_idle}")
    record(f"force: {forced['job']['result']['detail']}; {outcomes(forced)[0]}")

    assert idle["started"] is False and "up to date" in idle["detail"]
    assert versions_after_idle == [(1, 1)]
    config = forced["job"]["result"]["configs"][0]
    assert (config["trace_links"], config["added"], config["removed"]) == (3, 0, 0)
    assert version_numbers(client, headers, project) == [(2, 1), (1, 1)]


@case(
    id="I-11",
    feature="Sync / refusals",
    level="integration",
    priority="High",
    why="Each refusal protects something: a pair with nothing to restore from, files that are no longer there, and two syncs writing the same graph at once.",
    preconditions="A saved requirements->code run",
    input="Sync: a pair never run; a replacement for an unknown source; a replacement naming an expired upload; an empty body; and a sync while another sync job is unfinished",
    expected="409, 400, 410, 422 and 409 in that order, each with a message saying what is wrong; no new version created by any of them",
)
def test_sync_refuses_what_it_cannot_do_safely(client, db, record):
    """A sync that cannot be done is refused up front with a reason."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    requirements = source_ids(client, headers, project)["requirements"]

    refusals = {
        "pair never run": sync(client, headers, project, target_kind="architecture"),
        "unknown source": sync(client, headers, project, replacements={999999: "AAAAAAAAAAAAAAAAAAAAAA"}),
        "expired upload": sync(client, headers, project, replacements={requirements: "AAAAAAAAAAAAAAAAAAAAAA"}),
    }
    empty = client.post(f"/projects/{project}/sync", headers=headers, json={})
    refusals["empty body"] = {"status": empty.status_code, "detail": "source_kind and target_kind are required"}

    # Another sync already in flight for this project.
    running = jobs.create_job(db, None, project, jobs.KIND_SYNC)
    refusals["already syncing"] = sync(client, headers, project, force=True)
    jobs.fail(db, running.job_id, "ended by the test")

    for name, answer in refusals.items():
        record(f"{name}: {answer['status']} - {str(answer['detail'])[:110]}")

    assert [answer["status"] for answer in refusals.values()] == [409, 400, 410, 422, 409]
    assert version_numbers(client, headers, project) == [(1, 1)]


@case(
    id="I-24",
    feature="Sync / broken links through the API",
    level="integration",
    priority="High",
    why="'Broken' is what the user is told to act on. The graph endpoint must show the link, both ends named, with the missing end marked.",
    preconditions="A saved run where UC3 is linked to borrow()",
    input="Sync with requirements that no longer include UC3; then GET the graph filtered to broken links",
    expected="1 broken link, UC3.txt -> borrow, with from_present=false and to_present=true; the other 2 links active",
)
def test_removed_requirement_shows_as_a_broken_link(client, record):
    """A link whose requirement was removed is reported as broken, not dropped."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    requirements = source_ids(client, headers, project)["requirements"]
    files = {name: text for name, text in REQUIREMENTS[2].items() if name != "UC3.txt"}
    staged = stage(client, headers, project, requirements, files).json()

    sync(client, headers, project, replacements={requirements: staged["upload_id"]})
    broken = get(client, headers, f"/projects/{project}/graph", link_status="broken")
    record(f"summary: {broken['summary']}")
    record("broken links: " + str([
        (l["from_identifier"], l["to_identifier"].split("::")[-1], l["from_present"], l["to_present"])
        for l in broken["links"]
    ]))

    assert broken["summary"]["links_broken"] == 1 and broken["summary"]["links_active"] == 2
    link, = broken["links"]
    assert link["from_identifier"] == "UC3.txt" and "borrow" in link["to_identifier"]
    assert (link["from_present"], link["to_present"], link["status"]) == (False, True, "broken")


@case(
    id="I-12",
    feature="Sync / one configuration fails",
    level="integration",
    priority="High",
    why="A model outage halfway through must not lose the configurations that already succeeded, or leave fetched files lying around.",
    preconditions="One pair with 2 configurations",
    input="Sync with force, with the pipeline made to raise for the file-level configuration only",
    expected="The method-level configuration succeeds with 3 links; the file-level one reports the error; version 2 holds the 1 successful run; no working folder left",
)
def test_one_failing_configuration_does_not_lose_the_others(client, monkeypatch, record):
    """One configuration failing is reported for that configuration and does not undo the rest."""
    headers, project = project_with_two_configs(client)
    before = pending_uploads()
    failing_for(monkeypatch, lambda settings: settings["target_output_level"].value == "file")

    answer = sync(client, headers, project, force=True)
    for line in outcomes(answer):
        record(line)
    record(f"job state: {answer['job']['state']}; versions (number, runs): {version_numbers(client, headers, project)}")
    record(f"working folders left: {sorted(pending_uploads() - before)}")

    errors = [c["error"] for c in answer["job"]["result"]["configs"]]
    assert errors[0] is None and "could not be reached" in errors[1]
    assert version_numbers(client, headers, project)[0] == (2, 1)
    assert pending_uploads() == before


@case(
    id="I-13",
    feature="Sync / every configuration fails",
    level="integration",
    priority="High",
    why="If nothing ran, nothing about the project changed. A version with no runs in it is a state of the artifacts that nothing ever analysed.",
    preconditions="A saved requirements->code run in version 1, and new requirements staged for upload",
    input="Sync with the staged requirements, with the pipeline made to raise for every configuration",
    expected="The configuration reports its error; no working folder is left; the requirements source keeps its old fingerprint; and no new, empty version is created",
)
def test_a_sync_where_everything_fails_changes_nothing(client, monkeypatch, record):
    """A sync that produced no run leaves the project exactly as it was."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    requirements = source_ids(client, headers, project)["requirements"]
    old_ref = get(client, headers, f"/projects/{project}/sources")[0]["last_sync_ref"]
    staged = stage(client, headers, project, requirements, {"UC1.txt": "Changed text about login.\n"}).json()
    before = pending_uploads()
    failing_for(monkeypatch, lambda settings: True)

    answer = sync(client, headers, project, replacements={requirements: staged["upload_id"]})
    versions = version_numbers(client, headers, project)
    new_ref = get(client, headers, f"/projects/{project}/sources")[0]["last_sync_ref"]
    record(f"job state: {answer['job']['state']} - {answer['job']['result']['detail']}")
    for line in outcomes(answer):
        record(line)
    record(f"versions (number, runs): {versions}")
    record(f"requirements fingerprint unchanged: {old_ref == new_ref}; "
           f"new working folders: {sorted(pending_uploads() - before)}")

    assert answer["job"]["result"]["configs"][0]["error"]
    assert old_ref == new_ref
    assert pending_uploads() <= before
    assert versions == [(1, 1)], f"an empty version was left behind: {versions}"
