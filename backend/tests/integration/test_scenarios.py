"""The scenarios the demo walks through, end to end through the API, and what must hold after them."""

import json

from sqlalchemy import select

from api import pipeline_factory
from core.db.models import Analysis, Artifact, ArtifactFile, ProjectVersion
from core.projects import artifact_store, uploads
from tests import fakes
from tests.helpers import (
    CODE, DATA, REQUIREMENTS, analyse_and_save, analysis_path, get, link_pairs, new_project,
    pending_uploads, report, short, side_ids, sign_up, stage, states, sync, update_side, versions_of,
)
from tests.recorder import case

LOGIN, LOGOUT, BORROW = (REQUIREMENTS[2][name].strip() for name in ("UC1.txt", "UC2.txt", "UC3.txt"))
EXTRA = "Every account is locked after five failed attempts."
SENTENCES = dict(source_preprocessor="sentence", source_output_level="sentence", dependency_expansion_depth=0)
MODEL = dict(target_preprocessor="model_uml", target_output_level="component", dependency_expansion_depth=0)


def links_of(client, headers, analysis_id) -> set:
    return link_pairs(get(client, headers, f"/analyses/{analysis_id}")["result"]["trace_links"])


def scored_links(client, headers, analysis_id) -> set:
    """Links as (source, target, confidence), so two runs can be compared exactly."""
    return {
        (short(link["source_id"]), short(link["target_id"]), round(link["confidence"], 6))
        for link in get(client, headers, f"/analyses/{analysis_id}")["result"]["trace_links"]
    }


def reasons(answer: dict) -> dict:
    """A report's links as {(source, target): (state, source changed, target changed)}."""
    return {
        (short(link["source_id"]), short(link["target_id"])):
            (link["state"], link["source_changed"], link["target_changed"])
        for link in answer["links"]
    }


@case(
    id="I-49",
    feature="Demo story / four versions of one analysis",
    level="integration",
    priority="Critical",
    why="This is the demonstration itself in one go. Each step must make exactly one version, and the report across all of them must say what happened to every link and why.",
    preconditions="A signed-in user with an empty project; the corpus UC1-UC4 and Auth.java, Loans.java",
    input="New Analysis requirements -> code (v1). Update the requirements: a cosmetic rewording of UC1 (v2); UC2 rewritten to be about borrowing (v3); UC3 removed (v4). Read the report for v1 -> v4",
    expected="Versions 4, 3, 2, 1, one run each. v1 -> v4: UC1->login valid with its source changed; UC2->logout no longer found (source changed); UC2->borrow new; UC3->borrow broken, its source gone; UC4 uncovered; nothing changed on the code side",
)
def test_demo_story_from_v1_to_v4(client, record):
    """A New Analysis and three requirement updates give four versions and a report that explains each link."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    files = dict(REQUIREMENTS[2])

    steps = []
    files["UC1.txt"] = "A visitor performs a login, with a passphrase.\n"
    steps.append(update_side(client, headers, project, config, "source", files)["job"]["result"])
    files["UC2.txt"] = "A signed-in visitor can borrow a title.\n"
    steps.append(update_side(client, headers, project, config, "source", files)["job"]["result"])
    del files["UC3.txt"]
    steps.append(update_side(client, headers, project, config, "source", files)["job"]["result"])

    span = report(client, headers, project, config, base=1, head=4)
    for step in steps:
        record(f"v{step['version_number']}: {step['detail']}")
    record(f"versions (number, runs): {versions_of(client, headers, project, config)}")
    record(f"v1 -> v4 summary: {span['summary']}; uncovered {span['uncovered']}")
    record(f"v1 -> v4 links (state, source changed, target changed): {reasons(span)}")

    assert [step["version_number"] for step in steps] == [2, 3, 4]
    assert versions_of(client, headers, project, config) == [(4, 1), (3, 1), (2, 1), (1, 1)]
    assert reasons(span) == {
        ("UC1.txt", "Auth.java::Auth::login"): ("valid", True, False),
        ("UC2.txt", "Auth.java::Auth::logout"): ("no_longer_found", True, False),
        ("UC2.txt", "Loans.java::Loans::borrow"): ("new", True, False),
        ("UC3.txt", "Loans.java::Loans::borrow"): ("broken", True, False),
    }
    broken = next(link for link in span["links"] if link["state"] == "broken")
    assert (broken["source_present"], broken["target_present"]) == (False, True)
    assert span["summary"] == {"valid": 1, "no_longer_found": 1, "broken": 1, "new": 1, "uncovered": 1}
    assert span["uncovered"] == ["UC4.txt"]


@case(
    id="I-50",
    feature="GitHub update / code moved into a folder, mixed line endings",
    level="integration",
    priority="Critical",
    why="A real repository is laid out differently from an upload: code under a folder, CRLF from Windows, a .gitignore. Read naively, every file looks deleted and re-added and every link breaks.",
    preconditions="A saved requirements->code analysis from uploaded flat files (Auth.java, Loans.java, LF line endings)",
    input="Take the code side from a repository whose commit holds code/Auth.java with CRLF line endings, code/Loans.java with LF, and a .gitignore - no rename list from GitHub - and update",
    expected="Both files reported renamed into code/, none added or removed, .gitignore not listed anywhere. The report: 3 links valid, none broken, no longer found or new, every link now on code/",
)
def test_code_moved_into_a_folder_keeps_its_links(client, github, record):
    """Files moved into a folder, some with CRLF, are recognised as renamed and keep their links."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    code = side_ids(client, headers, project, config)["target"]
    github.push("commit-1", {
        "code/Auth.java": CODE[2]["Auth.java"].replace("\n", "\r\n"),
        "code/Loans.java": CODE[2]["Loans.java"],
        ".gitignore": "*.class\nbuild/\n",
    })
    client.post(f"{analysis_path(project, config)}/sources/{code}/github", headers=headers,
                json={"repository": "owner/library"})

    result = sync(client, headers, project, config, source_id=code)["job"]["result"]
    files = result["changes"]["files"]
    span = report(client, headers, project, config)
    targets = sorted({link["target_id"].split("::")[0] for link in span["links"]})
    record(f"update: {result['detail']}")
    record(f"files: {files}")
    record(f"report: {span['summary']}; link targets in: {targets}")

    assert result["version_number"] == 2
    assert sorted((r["old"], r["new"]) for r in files["renamed"]) == [
        ("Auth.java", "code/Auth.java"), ("Loans.java", "code/Loans.java"),
    ]
    assert files["added"] == [] and files["removed"] == []
    assert ".gitignore" not in json.dumps(files)
    assert span["summary"] == {"valid": 3, "no_longer_found": 0, "broken": 0, "new": 0, "uncovered": 1}
    assert targets == ["code/Auth.java", "code/Loans.java"]


@case(
    id="I-51",
    feature="Update / the same result as a fresh analysis",
    level="integration",
    priority="Critical",
    why="An update re-runs over the new files with pins from the last run. If that ever gives different links from analysing the new files from scratch, the history is lying about what the files say.",
    preconditions="Sentence-level requirements->code analyses over one file of three sentences (login, logout, borrow) and UC4",
    input="Four changes, each applied once as an update and once as a fresh New Analysis of exactly the new files with the same settings: a modified sentence, an inserted sentence, a removed file (UC4), a modified method (logout now also borrows)",
    expected="For every change, the updated version's links equal the fresh analysis's links, pair for pair and score for score",
)
def test_update_gives_the_same_links_as_a_fresh_analysis(client, record):
    """An update and a fresh analysis of the same new files find exactly the same links."""
    headers = sign_up(client)
    project = new_project(client, headers)
    requirements = {"UC.txt": f"{LOGIN} {LOGOUT} {BORROW}\n", "UC4.txt": REQUIREMENTS[2]["UC4.txt"]}
    logout_borrows = CODE[2]["Auth.java"].replace("this.clear();", "this.clear();\n        this.borrow();")
    changes = {
        "modified sentence": ("source", {**requirements, "UC.txt": f"{LOGIN} A signed-in visitor can borrow a title. {BORROW}\n"}),
        "inserted sentence": ("source", {**requirements, "UC.txt": f"{LOGIN} {EXTRA} {LOGOUT} {BORROW}\n"}),
        "removed file": ("source", {"UC.txt": requirements["UC.txt"]}),
        "modified method": ("target", {**CODE[2], "Auth.java": logout_borrows}),
    }

    differences = {}
    for name, (role, files) in changes.items():
        config = analyse_and_save(client, headers, project, source=("requirements", "reqs", requirements),
                                  **SENTENCES)["config_id"]
        updated = update_side(client, headers, project, config, role, files)["job"]["result"]
        sides = {"source": ("requirements", "reqs", requirements), "target": CODE}
        sides[role] = (sides[role][0], sides[role][1], files)
        fresh = analyse_and_save(client, headers, project, source=sides["source"], target=sides["target"],
                                 **SENTENCES)
        after_update = scored_links(client, headers, updated["analysis_id"])
        from_scratch = scored_links(client, headers, fresh["analysis_id"])
        differences[name] = (after_update ^ from_scratch)
        record(f"{name}: update v{updated['version_number']} has {len(after_update)} links, "
               f"fresh has {len(from_scratch)}; differing: {sorted(differences[name]) or 'none'}")

    assert all(not difference for difference in differences.values()), differences


class FailingClassifier(fakes.FakeClassifier):
    """Answers the first question, then fails as if the model went away mid-run."""

    def _ask(self, source, target):
        if self.asked:
            raise RuntimeError("the classifier stopped answering")
        return super()._ask(source, target)


@case(
    id="I-52",
    feature="Update / the classifier fails part-way",
    level="integration",
    priority="Critical",
    why="Groq can fail half-way through a run, after the files were read and embedded. Whatever was done up to then must not leave a half-made version behind.",
    preconditions="A saved requirements->code analysis at version 1 with 3 links",
    input="Update the requirements with an edited UC1, with a classifier that answers one question and then raises",
    expected="The job fails with the classifier's error. The analysis still has only version 1 with its one run and its 3 links; its requirements fingerprint is unchanged; the report still answers; no working folder is left",
)
def test_classifier_failing_mid_update_leaves_the_previous_version(client, monkeypatch, record):
    """A classifier that dies in the middle of an update leaves the analysis exactly as it was."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(client, headers, project)
    config = saved["config_id"]
    old_ref = get(client, headers, f"{analysis_path(project, config)}/sources")[0]["last_sync_ref"]
    before = pending_uploads()
    monkeypatch.setattr(pipeline_factory, "get_classifier", lambda *_: FailingClassifier())

    answer = update_side(client, headers, project, config, "source",
                         {**REQUIREMENTS[2], "UC1.txt": "A visitor performs a login with a passphrase, twice.\n"})
    new_ref = get(client, headers, f"{analysis_path(project, config)}/sources")[0]["last_sync_ref"]
    still = report(client, headers, project, config)
    record(f"job: {answer['job']['state']} - {answer['job']['error']}")
    record(f"versions (number, runs): {versions_of(client, headers, project, config)}")
    record(f"links of the saved run: {len(links_of(client, headers, saved['analysis_id']))}; report {still['summary']}")
    record(f"fingerprint unchanged: {old_ref == new_ref}; new working folders: {sorted(pending_uploads() - before)}")

    assert answer["job"]["state"] == "failed" and "stopped answering" in answer["job"]["error"]
    assert versions_of(client, headers, project, config) == [(1, 1)]
    assert len(links_of(client, headers, saved["analysis_id"])) == 3
    assert still["head_version"] == 1 and still["summary"]["valid"] == 3
    assert old_ref == new_ref
    assert pending_uploads() <= before


@case(
    id="I-53",
    feature="Update / bad files handed over for a side",
    level="integration",
    priority="High",
    why="The Update dialog takes files from the user's disk. A bad set must be refused before anything runs, never become a version, and never leave a half-written set on the server.",
    preconditions="A saved requirements->code analysis at version 1; the per-file limit lowered to 1,000 bytes",
    input="Stage for an update: no files; a .md file for the requirements; a file named .zip that is not a zip, for the code; an oversized requirements file",
    expected="400, 400, 400 and 413, each with a message. The analysis still has 1 version and no working folder is left",
)
def test_bad_files_for_an_update_are_refused(client, monkeypatch, record):
    """Bad files for an update are refused, make no version and leave nothing behind."""
    monkeypatch.setattr(uploads, "MAX_UPLOAD_BYTES", 1000)
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    sides = side_ids(client, headers, project, config)
    before = pending_uploads()

    def staged(role, files):
        response = stage(client, headers, project, config, sides[role], files)
        return response.status_code, response.json().get("detail")

    answers = {
        "no files": staged("source", {}),
        "wrong extension": staged("source", {"notes.md": "# notes"}),
        "not a zip": staged("target", {"code.zip": "this is not a zip"}),
        "oversized file": staged("source", {"UC1.txt": "x" * 1500}),
    }
    for name, (status, detail) in answers.items():
        record(f"{name}: {status} - {detail}")
    record(f"versions: {versions_of(client, headers, project, config)}; "
           f"working folders left: {sorted(pending_uploads() - before)}")

    assert {name: status for name, (status, _) in answers.items()} == {
        "no files": 400, "wrong extension": 400, "not a zip": 400, "oversized file": 413,
    }
    assert all(detail for _, detail in answers.values())
    assert versions_of(client, headers, project, config) == [(1, 1)]
    assert pending_uploads() == before


@case(
    id="I-54",
    feature="Access control / the endpoints I-20 does not reach",
    level="integration",
    priority="High",
    why="I-20 covers most endpoints. The line diff, the analysis itself and the update endpoints with no login at all are the ones it leaves out, and each of them reads or changes another user's analysis.",
    preconditions="User A owns an analysis at version 2 (UC1 edited). User B is a different signed-in user",
    input="As B: the analysis, its line diff, its delete. With no login: the analysis, report, line diff, sides, update status, staging, update, GitHub side, re-run, run delete and analysis delete",
    expected="Every request by B gets 404; every anonymous request gets 401. A's analysis still has 2 versions and its sides afterwards",
)
def test_endpoints_beyond_i20_refuse_other_users_and_anonymous_callers(client, record):
    """The line diff, the analysis itself and every update endpoint refuse other users and anonymous callers."""
    owner, intruder = sign_up(client), sign_up(client)
    project = new_project(client, owner)
    saved = analyse_and_save(client, owner, project)
    config = saved["config_id"]
    update_side(client, owner, project, config, "source",
                {**REQUIREMENTS[2], "UC1.txt": "A visitor performs a login with a passphrase, twice.\n"})
    sides = side_ids(client, owner, project, config)
    mine = analysis_path(project, config)
    diff = {"params": {"base": 1, "head": 2, "role": "source", "path": "UC1.txt"}}
    file = [("files", ("UC1.txt", b"changed", "text/plain"))]

    by_intruder = [("GET", mine, {}), ("GET", f"{mine}/report/diff", diff), ("DELETE", mine, {})]
    anonymous = [
        ("GET", mine, {}), ("GET", f"{mine}/report", {}), ("GET", f"{mine}/report/diff", diff),
        ("GET", f"{mine}/versions", {}), ("GET", f"{mine}/sync/status", {}),
        ("POST", f"{mine}/sources/{sides['source']}/files", {"files": file}),
        ("POST", f"{mine}/sync", {"json": {"source_id": sides["source"], "upload_id": "A" * 22}}),
        ("POST", f"{mine}/sources/{sides['target']}/github", {"json": {"repository": "owner/library"}}),
        ("POST", f"/analyses/{saved['analysis_id']}/rerun", {"json": {}}),
        ("DELETE", f"/analyses/{saved['analysis_id']}", {}),
        ("DELETE", mine, {}),
    ]
    other = {f"{m} {p}": client.request(m, p, headers=intruder, **o).status_code for m, p, o in by_intruder}
    nobody = {f"{m} {p}": client.request(m, p, **o).status_code for m, p, o in anonymous}
    record(f"as another user: {other}")
    record(f"anonymous: {sorted(set(nobody.values()))} over {len(nobody)} requests")
    record(f"A's versions afterwards: {versions_of(client, owner, project, config)}")

    assert set(other.values()) == {404}, other
    assert set(nobody.values()) == {401}, nobody
    assert [number for number, _ in versions_of(client, owner, project, config)] == [2, 1]
    assert len(side_ids(client, owner, project, config)) == 2


@case(
    id="I-55",
    feature="Data integrity / versions, runs and stored files",
    level="integration",
    priority="Critical",
    why="Every screen trusts three things: a version always has a run, every file a version lists can be read back, and deleting a run never takes files another version still needs.",
    preconditions="An analysis taken through four versions as in I-49, with version 4 re-run once so it holds two runs",
    input="Check every version and file row of the analysis; delete one of version 4's runs and collect garbage with the grace period switched off; check again, and read every version's files back",
    expected="Before and after the delete: every version has at least one run, and every file row's blob is in the store. After the delete version 4 keeps one run, none of the analysis's blobs is retired, and every artifact still downloads",
)
def test_versions_runs_and_blobs_stay_consistent(client, db, monkeypatch, record):
    """No version without a run, no file row without its blob, and a deleted run takes no shared files."""
    headers = sign_up(client)
    project = new_project(client, headers)
    first = analyse_and_save(client, headers, project)
    config = first["config_id"]
    files = dict(REQUIREMENTS[2])
    files["UC1.txt"] = "A visitor performs a login, with a passphrase.\n"
    update_side(client, headers, project, config, "source", files)
    files["UC2.txt"] = "A signed-in visitor can borrow a title.\n"
    update_side(client, headers, project, config, "source", files)
    del files["UC3.txt"]
    last = update_side(client, headers, project, config, "source", files)["job"]["result"]["analysis_id"]
    rerun = client.post(f"/analyses/{last}/rerun", headers=headers, json={})
    assert rerun.status_code == 201, rerun.text

    def check() -> tuple[list, list, list]:
        db.expire_all()
        versions = db.scalars(select(ProjectVersion).where(ProjectVersion.config_id == config)).all()
        empty = [v.version_number for v in versions
                 if not db.scalars(select(Analysis).where(Analysis.version_id == v.version_id)).first()]
        rows = db.execute(
            select(ArtifactFile.relative_path, ArtifactFile.sha256, Artifact.artifact_id)
            .join(Artifact, Artifact.artifact_id == ArtifactFile.artifact_id)
            .join(ProjectVersion, ProjectVersion.version_id == Artifact.version_id)
            .where(ProjectVersion.config_id == config)
        ).all()
        # In the store itself, not merely recoverable from the bin.
        missing = [path for path, digest, _ in rows if not artifact_store.blob_path(digest).is_file()]
        return empty, missing, rows

    empty_before, missing_before, rows = check()
    deleted = client.delete(f"/analyses/{rerun.json()['analysis_id']}", headers=headers).status_code
    # Every blob counts as old enough to sweep, so only references protect them.
    monkeypatch.setattr(artifact_store, "GC_GRACE_SECONDS", -60)
    referenced = {digest for (digest,) in db.execute(select(ArtifactFile.sha256)).all()}
    retired = artifact_store.collect_garbage(referenced)
    empty_after, missing_after, _ = check()
    downloads = {client.get(f"/artifacts/{artifact}/download", headers=headers).status_code
                 for artifact in {artifact for _, _, artifact in rows}}
    record(f"versions {versions_of(client, headers, project, config)}; file rows {len(rows)}")
    record(f"before delete: versions without a run {empty_before}, rows without a blob {missing_before}")
    record(f"delete run: {deleted}; blobs retired by garbage collection: {retired}")
    record(f"after delete: versions without a run {empty_after}, rows without a blob {missing_after}; "
           f"artifact downloads: {sorted(downloads)}")

    assert empty_before == [] and missing_before == []
    assert deleted == 204
    assert versions_of(client, headers, project, config)[0] == (4, 1)
    assert empty_after == [] and missing_after == []
    assert downloads == {200}


@case(
    id="I-56",
    feature="Update / one UML component changed",
    level="integration",
    priority="High",
    why="UML components are named by a counter, so a component added in front renumbers every one after it. An update that changes one component must not break the links of the others, and must still show them by name.",
    preconditions="A saved requirements -> architecture model analysis at component level over model.uml (Authentication, Lending)",
    input="Update the model with a new component 'Audit' inserted before Authentication, so both existing components get a new counter; read the report",
    expected="Version 2. Every link of version 1 is valid, none broken or no longer found. Every link in the report and in version 2's run carries a component name, never a raw '$' id",
)
def test_changing_one_uml_component_keeps_the_other_links(client, record):
    """Inserting one UML component renumbers the others but keeps their links, shown by name."""
    headers = sign_up(client)
    project = new_project(client, headers)
    original = (DATA / "model.uml").read_text(encoding="utf-8")
    saved = analyse_and_save(client, headers, project, target=("architecture", "model", {"model.uml": original}), **MODEL)
    config = saved["config_id"]
    audit = ('  <packagedElement xmi:type="uml:Component" xmi:id="_audit" name="Audit"/>\n'
             '  <packagedElement xmi:type="uml:Component" xmi:id="_auth" name="Authentication">')
    changed = original.replace('  <packagedElement xmi:type="uml:Component" xmi:id="_auth" name="Authentication">', audit)
    assert changed != original

    result = update_side(client, headers, project, config, "target", {"model.uml": changed})["job"]["result"]
    span = report(client, headers, project, config)
    run = get(client, headers, f"/analyses/{result['analysis_id']}")["result"]
    before = sorted({link["target_id"] for link in saved["result"]["trace_links"]})
    after = sorted({link["target_id"] for link in run["trace_links"]})
    record(f"update: {result['detail']}; elements {result['changes']['elements']}")
    record(f"component ids before {before}, after {after}")
    record(f"report: {span['summary']}; names {sorted({(l['state'], l['target_name']) for l in span['links']})}")

    assert result["version_number"] == 2
    assert before != after
    assert span["summary"]["broken"] == 0 and span["summary"]["no_longer_found"] == 0
    assert span["summary"]["valid"] == len(saved["result"]["trace_links"])
    assert all(link["target_name"] in {"Authentication", "Lending"} for link in span["links"])
    assert all(link["target_name"] and "$" not in link["target_name"] for link in run["trace_links"])


@case(
    id="I-57",
    feature="GitHub update / a whitespace-only commit",
    level="integration",
    priority="High",
    why="A commit that only reformats files changes the commit id but not the code. It must make no version, and the side must stop showing as changed afterwards, or the Update button nags forever.",
    preconditions="A saved requirements->code analysis whose code side was taken from the repository at commit-1 (same files as stored)",
    input="Push commit-2 with the same code re-indented and with trailing blank lines; read the status, update, read the status again; then push commit-3 with a real change",
    expected="Before the update: changed, commit-1 -> commit-2. The update makes no version ('No meaningful change'). Afterwards: not changed, at commit-2. Commit-3 is then detected as changed again",
)
def test_whitespace_only_commit_stops_showing_as_changed(client, github, record):
    """A reformatting commit makes no version and is then no longer reported as new."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config = analyse_and_save(client, headers, project)["config_id"]
    code = side_ids(client, headers, project, config)["target"]
    github.push("commit-1", CODE[2])
    client.post(f"{analysis_path(project, config)}/sources/{code}/github", headers=headers,
                json={"repository": "owner/library"})
    sync(client, headers, project, config, source_id=code)

    def status():
        rows = get(client, headers, f"{analysis_path(project, config)}/sync/status")
        row = next(row for row in rows if row["kind"] == "code")
        return row["changed"], row["last_sync_ref"], row["latest_ref"]

    github.push("commit-2", {name: text.replace("    ", "\t") + "\n\n" for name, text in CODE[2].items()})
    before = status()
    answer = sync(client, headers, project, config, source_id=code)
    result = answer.get("job", {}).get("result") or answer
    after = status()
    github.push("commit-3", {**CODE[2], "Extra.java": "public class Extra { void audit() {} }\n"})
    real = status()
    record(f"status after the reformatting push: {before}")
    record(f"update: '{result['detail']}'")
    record(f"status after the update: {after}; after a real change: {real}")
    record(f"versions: {versions_of(client, headers, project, config)}")

    assert before == (True, "commit-1", "commit-2")
    assert "No meaningful change" in result["detail"]
    assert versions_of(client, headers, project, config) == [(1, 1)]
    assert after[:2] == (False, "commit-2")
    assert real[0] is True


def drop_only_run(db, analysis_id: int) -> None:
    """Leave a version with no run, as a delete made before the refusal could."""
    from core.projects import service
    service.delete_analysis(db, db.get(Analysis, analysis_id))


def two_versions(client, headers, project) -> tuple[int, dict]:
    """An analysis at v2 (UC1 edited), and v2's run."""
    first = analyse_and_save(client, headers, project)
    config = first["config_id"]
    result = update_side(client, headers, project, config, "source",
                         {**REQUIREMENTS[2], "UC1.txt": "A visitor performs a login with a passphrase, twice.\n"})
    return config, {"first": first["analysis_id"], "second": result["job"]["result"]["analysis_id"]}


@case(
    id="I-58",
    feature="Deleting a run / the only run of a version",
    level="integration",
    priority="High",
    why="A version without a run has no links, so the change report has nothing to compare it by. Its last run must not be deletable.",
    preconditions="A saved analysis at v1 with one run",
    input="DELETE that run",
    expected="409 'This is the only run of this version.'; the run is still there and v1 still has 1 run",
)
def test_the_only_run_of_a_version_cannot_be_deleted(client, record):
    """Deleting a version's last run is refused."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(client, headers, project)

    refused = client.delete(f"/analyses/{saved['analysis_id']}", headers=headers)
    still = client.get(f"/analyses/{saved['analysis_id']}", headers=headers).status_code
    record(f"delete: {refused.status_code} - {refused.json()['detail']}; run afterwards: {still}; "
           f"versions {versions_of(client, headers, project, saved['config_id'])}")

    assert (refused.status_code, refused.json()["detail"]) == (409, "This is the only run of this version.")
    assert still == 200
    assert versions_of(client, headers, project, saved["config_id"]) == [(1, 1)]


@case(
    id="I-59",
    feature="Deleting a run / one of several",
    level="integration",
    priority="High",
    why="The refusal must only stop the last run; clearing out a re-run stays routine.",
    preconditions="A saved analysis at v1 with a run and a re-run",
    input="DELETE the re-run; then DELETE the remaining run",
    expected="204, and v1 keeps 1 run; then 409 for the remaining run",
)
def test_one_of_two_runs_can_still_be_deleted(client, record):
    """A version with two runs can lose one of them, not both."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(client, headers, project)
    rerun = client.post(f"/analyses/{saved['analysis_id']}/rerun", headers=headers, json={}).json()

    first = client.delete(f"/analyses/{rerun['analysis_id']}", headers=headers).status_code
    after = versions_of(client, headers, project, saved["config_id"])
    last = client.delete(f"/analyses/{saved['analysis_id']}", headers=headers).status_code
    record(f"delete the re-run: {first}; versions {after}; delete the last run: {last}")

    assert (first, after, last) == (204, [(1, 1)], 409)


@case(
    id="I-60",
    feature="Change report / an earlier version with no run",
    level="integration",
    priority="Critical",
    why="Versions left without a run by an older delete still exist. Comparing against one must not fail and hide the later version's links.",
    preconditions="An analysis at v2 (UC1 edited) whose v1 run was deleted before deleting the last run was refused",
    input="Read the report for 1 -> 2; then remove v2's run as well and read 1 -> 2 again",
    expected="1 -> 2: 200, no_run_version 1, v2's 3 links returned, the net change of the files still there. With v2's run gone too: 200, no_run_version 2, no links",
)
def test_report_with_a_runless_earlier_version(client, db, record):
    """A run-less earlier version gives the later version's links, uncompared, instead of an error."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config, runs = two_versions(client, headers, project)
    drop_only_run(db, runs["first"])

    span = client.get(f"{analysis_path(project, config)}/report", headers=headers, params={"base": 1, "head": 2})
    body = span.json()
    record(f"1 -> 2: {span.status_code}; no_run_version {body['no_run_version']}; links {len(body['links'])}; "
           f"net files {[row['path'] for side in body['net'] for row in side['files']]}")
    drop_only_run(db, runs["second"])
    empty = client.get(f"{analysis_path(project, config)}/report", headers=headers, params={"base": 1, "head": 2}).json()
    record(f"with v2's run gone too: no_run_version {empty['no_run_version']}; links {len(empty['links'])}")

    assert span.status_code == 200
    assert body["no_run_version"] == 1 and body["head_version"] == 2
    assert len(body["links"]) == 3
    assert [row["path"] for side in body["net"] for row in side["files"]] == ["UC1.txt"]
    assert (empty["no_run_version"], empty["links"]) == (2, [])


@case(
    id="I-61",
    feature="Change report / which earlier version is chosen",
    level="integration",
    priority="High",
    why="The Trace Links page opens on the default comparison. It must compare against the nearest version that has links, not a run-less one.",
    preconditions="An analysis at v3 (UC1 edited in v2, again in v3) whose v2 run was deleted; and one at v2 whose v1 run was deleted",
    input="Read the default report of each",
    expected="First: 1 -> 3, compared (no_run_version none). Second: 1 -> 2 with no_run_version 1 and v2's links, since no earlier version has a run",
)
def test_default_report_skips_runless_versions(client, db, record):
    """By default the report compares against the nearest earlier version with a run."""
    headers = sign_up(client)
    project = new_project(client, headers)
    config, runs = two_versions(client, headers, project)
    update_side(client, headers, project, config, "source",
                {**REQUIREMENTS[2], "UC1.txt": "A visitor performs a login with a passphrase, three times.\n"})
    drop_only_run(db, runs["second"])
    skipped = report(client, headers, project, config)

    alone_config, alone_runs = two_versions(client, headers, project)
    drop_only_run(db, alone_runs["first"])
    alone = report(client, headers, project, alone_config)
    record(f"v3 with a run-less v2: {skipped['base_version']} -> {skipped['head_version']}, "
           f"no_run_version {skipped['no_run_version']}, {skipped['summary']}")
    record(f"v2 with a run-less v1: {alone['base_version']} -> {alone['head_version']}, "
           f"no_run_version {alone['no_run_version']}, links {len(alone['links'])}")

    assert (skipped["base_version"], skipped["head_version"], skipped["no_run_version"]) == (1, 3, None)
    assert skipped["summary"]["valid"] == 3
    assert (alone["base_version"], alone["head_version"], alone["no_run_version"]) == (1, 2, 1)
    assert len(alone["links"]) == 3
