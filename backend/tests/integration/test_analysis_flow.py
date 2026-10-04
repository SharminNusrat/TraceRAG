"""New analysis and re-run, through the HTTP API."""

from tests.helpers import (
    ARCHITECTURE_DOCUMENT, CODE, DATA, EXPECTED_LINKS, REQUIREMENTS, analyse_and_save, analysis_path,
    get, link_pairs, new_project, pending_uploads, report, run_analysis, save_analysis, sign_up,
    update_side, versions_of,
)
from tests.recorder import case


@case(
    id="I-03",
    feature="New analysis / upload, run, save",
    level="integration",
    priority="Critical",
    why="This is the first thing shown in the demo. If any step between upload and the saved analysis is wrong, nothing after it can work.",
    preconditions="A signed-in user with an empty project",
    input="Upload 4 use cases and 2 Java files to the project, run (whole document -> method, top-k 10), then save",
    expected="Job succeeds with links UC1->login, UC2->logout, UC3->borrow and UC4 unimplemented. Saving creates one analysis (requirements->code) at version 1 with 1 run and a source and a target side; its report lists 3 valid links and UC4 uncovered; the working folder is removed",
)
def test_new_analysis_is_run_and_saved(client, record):
    """A New Analysis goes from uploaded files to a new analysis with its version, sides and report."""
    headers = sign_up(client)
    project = new_project(client, headers)
    before = pending_uploads()

    saved = analyse_and_save(client, headers, project)
    result = saved["result"]
    record(f"links: {sorted(link_pairs(result['trace_links']))}")
    record(f"unimplemented: {[item['identifier'] for item in result['unimplemented']]}")

    path = analysis_path(project, saved["config_id"])
    versions = get(client, headers, f"{path}/versions")
    sides = get(client, headers, f"{path}/sources")
    configs = get(client, headers, f"/projects/{project}/configs")
    links = report(client, headers, project, saved["config_id"])
    record(f"versions: {[(v['version_number'], len(v['sources']), v['analysis_count']) for v in versions]} (number, sides, runs)")
    record(f"sides: {[(s['role'], s['kind'], s['name'], s['origin']) for s in sides]}")
    record(f"analyses: {[(c['source_kind'], c['target_kind'], c['version_number'], c['analysis_count']) for c in configs]}")
    record(f"report: {links['summary']}; uncovered {links['uncovered']}")
    record(f"working folders left: {sorted(pending_uploads() - before)}")

    assert link_pairs(result["trace_links"]) == EXPECTED_LINKS
    assert [item["identifier"] for item in result["unimplemented"]] == ["UC4.txt"]
    assert saved["artifact_count"] == 2
    assert [(v["version_number"], len(v["sources"]), v["analysis_count"]) for v in versions] == [(1, 2, 1)]
    assert [(s["role"], s["kind"], s["origin"]) for s in sides] == [
        ("source", "requirements", "upload"), ("target", "code", "upload"),
    ]
    assert [(c["source_kind"], c["target_kind"], c["version_number"], c["analysis_count"]) for c in configs] == [
        ("requirements", "code", 1, 1),
    ]
    assert links["summary"] == {"valid": 3, "no_longer_found": 0, "broken": 0, "new": 0, "uncovered": 1}
    assert links["uncovered"] == ["UC4.txt"]
    assert pending_uploads() == before


@case(
    id="I-05",
    feature="Re-run / same files, same settings",
    level="integration",
    priority="Critical",
    why="Re-running must not invent a new version or lose the files: nothing about the artifacts changed, so the history must say so.",
    preconditions="A saved analysis in version 1",
    input="POST /analyses/{id}/rerun",
    expected="201; the same 3 links; still exactly 1 version, now with 2 runs; still one analysis, now with 2 runs; the new run has its 2 artifacts; the report reads the new run and lists 3 valid links",
)
def test_rerun_repeats_the_run_in_the_same_version(client, record):
    """A re-run is a second run of the same version of the same analysis."""
    headers = sign_up(client)
    project = new_project(client, headers)
    original = analyse_and_save(client, headers, project)

    response = client.post(f"/analyses/{original['analysis_id']}/rerun", headers=headers, json={})
    rerun = response.json()
    versions = get(client, headers, f"{analysis_path(project, original['config_id'])}/versions")
    configs = get(client, headers, f"/projects/{project}/configs")
    links = report(client, headers, project, original["config_id"])
    record(f"status {response.status_code}; links: {sorted(link_pairs(rerun['result']['trace_links']))}")
    record(f"versions (number, runs): {[(v['version_number'], v['analysis_count']) for v in versions]}")
    record(f"configurations (id, runs): {[(c['config_id'], c['analysis_count']) for c in configs]}")
    record(f"artifacts on the re-run: {[(a['role'], a['artifact_type'], a['files_available']) for a in rerun['artifacts']]}")
    record(f"report: read run {links['head_analysis_id']}, {links['summary']}")

    assert response.status_code == 201
    assert rerun["analysis_id"] != original["analysis_id"]
    assert link_pairs(rerun["result"]["trace_links"]) == EXPECTED_LINKS
    assert [(v["version_number"], v["analysis_count"]) for v in versions] == [(1, 2)]
    assert len(configs) == 1 and configs[0]["analysis_count"] == 2
    assert sorted((a["role"], a["files_available"]) for a in rerun["artifacts"]) == [("source", True), ("target", True)]
    assert links["head_analysis_id"] == rerun["analysis_id"] and links["summary"]["valid"] == 3


@case(
    id="I-35",
    feature="Re-run / settings cannot change",
    level="integration",
    priority="High",
    why="Settings belong to the analysis. A re-run that quietly used other settings would put links of a different granularity into the same history and make every version comparison meaningless.",
    preconditions="A saved analysis reported at method level",
    input="POST /analyses/{id}/rerun with a body that also carries a config asking for file level",
    expected="201; the run uses the analysis's own settings (links still per method, the reported config still says function); still one analysis with 1 version and 2 runs",
)
def test_rerun_always_uses_the_analysis_settings(client, record):
    """Whatever a re-run request carries, the run uses its analysis's settings."""
    headers = sign_up(client)
    project = new_project(client, headers)
    original = analyse_and_save(client, headers, project)
    other = {**get(client, headers, f"/analyses/{original['analysis_id']}")["config"], "target_output_level": "file"}

    response = client.post(f"/analyses/{original['analysis_id']}/rerun", headers=headers, json={"config": other})
    rerun = response.json()
    configs = get(client, headers, f"/projects/{project}/configs")
    record(f"status {response.status_code}; links: {sorted(link_pairs(rerun['result']['trace_links']))}")
    record(f"reported target level: {rerun['config']['target_output_level']}; analyses: {len(configs)}")

    assert response.status_code == 201
    assert link_pairs(rerun["result"]["trace_links"]) == EXPECTED_LINKS
    assert rerun["config"]["target_output_level"] == "function"
    assert len(configs) == 1
    assert versions_of(client, headers, project, original["config_id"]) == [(1, 2)]


@case(
    id="I-04",
    feature="New analysis / session run saved later",
    level="integration",
    priority="High",
    why="A visitor can run without an account and save afterwards. The files uploaded for that run must still be there to claim.",
    input="Run an upload anonymously in session mode; then sign up and save the result into a new project",
    expected="The anonymous job succeeds with 3 links. Saving returns 201 with 2 artifacts claimed, and the new analysis gets 2 sides and version 1",
)
def test_anonymous_run_can_be_saved_into_a_project_afterwards(client, record):
    """A run made without an account keeps its files long enough to be saved once the user signs in."""
    result = run_analysis(client)
    record(f"anonymous run: {len(result['trace_links'])} links, upload id present: {bool(result['upload_id'])}")

    headers = sign_up(client)
    project = new_project(client, headers)
    saved = save_analysis(client, headers, project, result)
    path = analysis_path(project, saved.json()["config_id"])
    sides = get(client, headers, f"{path}/sources")
    record(f"save: {saved.status_code}, artifacts claimed: {saved.json().get('artifact_count')}, sides: {len(sides)}")

    assert link_pairs(result["trace_links"]) == EXPECTED_LINKS
    assert saved.status_code == 201 and saved.json()["artifact_count"] == 2
    versions = get(client, headers, f"{path}/versions")
    assert len(sides) == 2 and [v["version_number"] for v in versions] == [1]


@case(
    id="I-27",
    feature="Artifact kinds / architecture document",
    level="integration",
    priority="High",
    why="Architecture documents use the same preprocessors as requirements. The two relations must stay two analyses, each with its own kinds and links.",
    preconditions="A project with a saved requirements->code analysis",
    input="In the same project, run architecture_document->code with exactly the same settings",
    expected="2 analyses with their own kinds, each reporting its own links: 3 from requirements, and arch.txt linked to login, logout and borrow",
)
def test_architecture_document_is_its_own_analysis(client, record):
    """An architecture document traced to code is a separate analysis from requirements traced to code."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    documents = analyse_and_save(client, headers, project, source=ARCHITECTURE_DOCUMENT)

    configs = get(client, headers, f"/projects/{project}/configs")
    valid = {
        c["source_kind"]: report(client, headers, project, c["config_id"])["summary"]["valid"]
        for c in configs
    }
    record(f"document links: {sorted(link_pairs(documents['result']['trace_links']))}")
    record(f"analyses: {[(c['config_id'], c['source_kind'], c['target_kind']) for c in configs]}")
    record(f"valid links per analysis: {valid}")

    assert link_pairs(documents["result"]["trace_links"]) == {
        ("arch.txt", "Auth.java::Auth::login"), ("arch.txt", "Auth.java::Auth::logout"),
        ("arch.txt", "Loans.java::Loans::borrow"),
    }
    assert [(c["source_kind"], c["target_kind"]) for c in configs] == [
        ("requirements", "code"), ("architecture_document", "code"),
    ]
    assert valid == {"requirements": 3, "architecture_document": 3}


@case(
    id="I-28",
    feature="Configuration identity / expansion depth through the API",
    level="integration",
    priority="Medium",
    why="The UI can still send depth 1 for a non-code target. What is stored must be what happened.",
    input="Run requirements->architecture_document, sending dependency_expansion_depth=1",
    expected="The saved analysis and the save response both report depth 0",
)
def test_api_stores_no_expansion_for_a_document_target(client, record):
    """A depth sent for a target that cannot be expanded is saved as 0."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(
        client, headers, project, target=ARCHITECTURE_DOCUMENT,
        target_preprocessor="single", target_output_level="artifact", dependency_expansion_depth=1,
    )

    config = get(client, headers, f"/projects/{project}/configs")[0]
    record(f"sent depth 1; analysis stores {config['config']['dependency_expansion_depth']}, "
           f"save response says {saved['dependency_expansion_depth']}")

    assert config["config"]["dependency_expansion_depth"] == 0
    assert saved["dependency_expansion_depth"] == 0


@case(
    id="I-30",
    feature="New analysis / save response",
    level="integration",
    priority="Medium",
    why="The response says which version a run belongs to. Null means 'saved before versioning existed', which is untrue for a run just saved.",
    preconditions="A project with no runs",
    input="Save a new analysis, then read the same run from GET /analyses",
    expected="Both report version_number 1",
)
def test_save_response_reports_the_version_it_created(client, record):
    """The answer to saving a run names the version the run was saved into."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(client, headers, project)
    listed = get(client, headers, "/analyses", project_id=project)[0]
    record(f"save response version_number: {saved['version_number']}; list endpoint: {listed['version_number']}")

    assert listed["version_number"] == 1
    assert saved["version_number"] == 1


@case(
    id="I-02",
    feature="Capabilities / option lists",
    level="integration",
    priority="Medium",
    why="The whole New Analysis form is drawn from this answer. A kind missing here cannot be chosen; a wrong default starts every run wrong.",
    input="GET /capabilities",
    expected="4 kinds; documents offer Sentences and Whole document only (no Sections) and default to Whole document; upload limit 30 MB",
)
def test_capabilities_list_the_kinds_and_their_options(client, record):
    """The options the frontend offers come from the backend's registry."""
    capabilities = get(client, {}, "/capabilities")
    kinds = {k["key"]: k for k in capabilities["artifact_kinds"]}
    offered = {key: [p["key"] for p in kind["preprocessors"]] for key, kind in kinds.items()}
    record(f"kinds and their preprocessors: {offered}")
    record(f"defaults: { {key: kind['default_preprocessor'] for key, kind in kinds.items()} }")

    assert list(kinds) == ["requirements", "architecture_document", "code", "architecture"]
    assert offered["requirements"] == offered["architecture_document"] == ["sentence", "single"]
    assert kinds["requirements"]["default_preprocessor"] == "single"
    assert capabilities["max_upload_bytes"] == 30 * 1024 * 1024
    assert REQUIREMENTS[0] in kinds and CODE[0] in kinds


@case(
    id="I-40",
    feature="Analysis page / sides, files and versions",
    level="integration",
    priority="High",
    why="The analysis page shows what each side holds now and every version with its runs. If it reads another side's files, or files a re-run under a new version, the page tells the user something untrue.",
    preconditions="A saved requirements->code analysis, re-run once, then its requirements side updated with a fifth use case",
    input="GET the analysis, then its versions",
    expected="The analysis lists the requirements side holding UC1-UC5 and the code side holding Auth.java and Loans.java. Versions: v2 with 1 run, v1 with 2 runs (the save and the re-run), newest first; v2 records UC5.txt as added",
)
def test_analysis_lists_its_sides_files_and_versions(client, record):
    """One analysis reads back with each side's current files, and each version with its runs."""
    headers = sign_up(client)
    project = new_project(client, headers)
    saved = analyse_and_save(client, headers, project)
    config = saved["config_id"]
    client.post(f"/analyses/{saved['analysis_id']}/rerun", headers=headers, json={})
    update_side(client, headers, project, config, "source", {**REQUIREMENTS[2], "UC5.txt": "A passphrase is required.\n"})

    analysis = get(client, headers, analysis_path(project, config))
    versions = get(client, headers, f"{analysis_path(project, config)}/versions")
    record(f"sides: {[(s['role'], s['files']) for s in analysis['sides']]}")
    record(f"versions (number, runs): {[(v['version_number'], len(v['runs'])) for v in versions]}")
    record(f"v2 changes: {versions[0]['changes'][0]['files']}")

    assert [(s["role"], s["files"]) for s in analysis["sides"]] == [
        ("source", ["UC1.txt", "UC2.txt", "UC3.txt", "UC4.txt", "UC5.txt"]),
        ("target", ["Auth.java", "Loans.java"]),
    ]
    assert [(v["version_number"], len(v["runs"])) for v in versions] == [(2, 1), (1, 2)]
    assert versions[1]["runs"][1]["analysis_id"] == saved["analysis_id"]
    assert versions[0]["changes"][0]["files"]["added"] == ["UC5.txt"]


@case(
    id="I-48",
    feature="Display names / a UML analysis end to end",
    level="integration",
    priority="High",
    why="Every screen and every export labels elements from what the API sends. A UML component sent only by its raw id is unreadable everywhere at once.",
    preconditions="tests/data/model.uml, with the components Authentication and Lending",
    input="Run, save and reopen requirements -> architecture model at component level, and read its change report",
    expected="Every link into the model carries the component's name (Authentication or Lending) as target_name, and so does every component element; the raw ids stay in target_id. Requirements keep their file names (no name sent). The report's links carry the same names",
)
def test_uml_components_are_sent_with_their_names(client, record):
    """A UML analysis sends component names with its elements and links, live, saved and in the report."""
    headers = sign_up(client)
    project = new_project(client, headers)
    model = ("architecture", "model", {"model.uml": (DATA / "model.uml").read_text(encoding="utf-8")})
    saved = analyse_and_save(client, headers, project, target=model, target_preprocessor="model_uml",
                             target_output_level="component", dependency_expansion_depth=0)
    stored = get(client, headers, f"/analyses/{saved['analysis_id']}")["result"]
    links = report(client, headers, project, saved["config_id"])["links"]

    def named(result):
        return sorted({(link["target_id"].split("$")[-1], link["target_name"], link["source_name"])
                       for link in result["trace_links"]})

    record(f"live links (xmi id, target name, source name): {named(saved['result'])}")
    record(f"stored elements: {[(e['identifier'], e['display_name']) for e in stored['target_elements']]}")
    record(f"report: {sorted({(l['target_name'], l['source_name']) for l in links})}")

    assert saved["result"]["trace_links"]
    assert named(saved["result"]) == named(stored)
    assert {name for _, name, _ in named(stored)} <= {"Authentication", "Lending"}
    assert all(source is None for _, _, source in named(stored))
    assert {e["display_name"] for e in stored["target_elements"]} == {"Authentication", "Lending"}
    assert {link["target_name"] for link in links} <= {"Authentication", "Lending"}
    assert all("$" in link["target_id"] for link in stored["trace_links"])
