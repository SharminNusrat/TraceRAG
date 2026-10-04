"""New analysis and re-run, through the HTTP API."""

from tests.helpers import (
    ARCHITECTURE_DOCUMENT, CODE, EXPECTED_LINKS, REQUIREMENTS, analyse_and_save, get, link_pairs,
    new_project, pending_uploads, run_analysis, save_analysis, sign_up,
)
from tests.recorder import case


@case(
    id="I-03",
    feature="New analysis / upload, run, save",
    level="integration",
    priority="Critical",
    why="This is the first thing shown in the demo. If any step between upload and saved graph is wrong, nothing after it can work.",
    preconditions="A signed-in user with an empty project",
    input="Upload 4 use cases and 2 Java files to the project, run (whole document -> method, top-k 10), then save",
    expected="Job succeeds with links UC1->login, UC2->logout, UC3->borrow and UC4 unimplemented. Saving creates version 1 with 2 sources, 1 configuration (requirements->code), 3 active links, and removes the working folder",
)
def test_new_analysis_is_run_saved_and_graphed(client, record):
    """A New Analysis goes from uploaded files to a saved version, sources, configuration and graph."""
    headers = sign_up(client)
    project = new_project(client, headers)
    before = pending_uploads()

    saved = analyse_and_save(client, headers, project)
    result = saved["result"]
    record(f"links: {sorted(link_pairs(result['trace_links']))}")
    record(f"unimplemented: {[item['identifier'] for item in result['unimplemented']]}")

    versions = get(client, headers, f"/projects/{project}/versions")
    sources = get(client, headers, f"/projects/{project}/sources")
    configs = get(client, headers, f"/projects/{project}/configs")
    graph = get(client, headers, f"/projects/{project}/graph")
    pairs = get(client, headers, f"/projects/{project}/pairs")
    record(f"versions: {[(v['version_number'], len(v['sources']), v['analysis_count']) for v in versions]} (number, sources, runs)")
    record(f"sources: {[(s['kind'], s['name'], s['origin'], s['is_active']) for s in sources]}")
    record(f"configurations: {[(c['source_kind'], c['target_kind'], c['analysis_count']) for c in configs]}")
    record(f"graph: {graph['summary']}")
    record(f"working folders left: {sorted(pending_uploads() - before)}")

    assert link_pairs(result["trace_links"]) == EXPECTED_LINKS
    assert [item["identifier"] for item in result["unimplemented"]] == ["UC4.txt"]
    assert saved["artifact_count"] == 2
    assert [(v["version_number"], len(v["sources"]), v["analysis_count"]) for v in versions] == [(1, 2, 1)]
    assert sorted((s["kind"], s["origin"], s["is_active"]) for s in sources) == [
        ("code", "upload", True), ("requirements", "upload", True),
    ]
    assert [(c["source_kind"], c["target_kind"], c["analysis_count"]) for c in configs] == [("requirements", "code", 1)]
    assert graph["summary"]["links_active"] == 3 and graph["summary"]["links_broken"] == 0
    assert [(p["source_kind"], p["target_kind"], p["out_of_date"]) for p in pairs] == [("requirements", "code", False)]
    assert pending_uploads() == before


@case(
    id="I-05",
    feature="Re-run / same configuration",
    level="integration",
    priority="Critical",
    why="Re-running must not invent a new version or lose the files: nothing about the artifacts changed, so the history must say so.",
    preconditions="A saved analysis in version 1",
    input="POST /analyses/{id}/rerun with no body changes",
    expected="201; the same 3 links; still exactly 1 version, now with 2 runs; the same configuration, now with 2 runs; the new run has its 2 artifacts; graph unchanged",
)
def test_rerun_repeats_the_run_in_the_same_version(client, record):
    """A re-run with the original settings is a second run of the same version and configuration."""
    headers = sign_up(client)
    project = new_project(client, headers)
    original = analyse_and_save(client, headers, project)

    response = client.post(f"/analyses/{original['analysis_id']}/rerun", headers=headers, json={})
    rerun = response.json()
    versions = get(client, headers, f"/projects/{project}/versions")
    configs = get(client, headers, f"/projects/{project}/configs")
    graph = get(client, headers, f"/projects/{project}/graph")
    record(f"status {response.status_code}; links: {sorted(link_pairs(rerun['result']['trace_links']))}")
    record(f"versions (number, runs): {[(v['version_number'], v['analysis_count']) for v in versions]}")
    record(f"configurations (id, runs): {[(c['config_id'], c['analysis_count']) for c in configs]}")
    record(f"artifacts on the re-run: {[(a['role'], a['artifact_type'], a['files_available']) for a in rerun['artifacts']]}")
    record(f"graph: {graph['summary']}")

    assert response.status_code == 201
    assert rerun["analysis_id"] != original["analysis_id"]
    assert link_pairs(rerun["result"]["trace_links"]) == EXPECTED_LINKS
    assert [(v["version_number"], v["analysis_count"]) for v in versions] == [(1, 2)]
    assert len(configs) == 1 and configs[0]["analysis_count"] == 2
    assert sorted((a["role"], a["files_available"]) for a in rerun["artifacts"]) == [("source", True), ("target", True)]
    assert graph["summary"]["links_active"] == 3 and graph["summary"]["links_stale"] == 0


@case(
    id="I-06",
    feature="Re-run / different configuration",
    level="integration",
    priority="High",
    why="Trying another output level on the same files is a core use. It must get its own graph and must not be mistaken for a change to the artifacts.",
    preconditions="A saved analysis reported at method level",
    input="Re-run it with target output level 'file' instead of 'function'",
    expected="A second configuration with its own graph; links are per file (UC1->Auth.java, UC2->Auth.java, UC3->Loans.java); still 1 version; the first configuration's graph is untouched",
)
def test_rerun_with_other_settings_gets_its_own_configuration(client, record):
    """Different settings on the same files make a new configuration and graph, in the same version."""
    headers = sign_up(client)
    project = new_project(client, headers)
    original = analyse_and_save(client, headers, project)
    config = {**get(client, headers, f"/analyses/{original['analysis_id']}")["config"], "target_output_level": "file"}

    response = client.post(f"/analyses/{original['analysis_id']}/rerun", headers=headers, json={"config": config})
    links = link_pairs(response.json()["result"]["trace_links"])
    configs = get(client, headers, f"/projects/{project}/configs")
    graphs = {
        c["config"]["target_output_level"]: get(client, headers, f"/projects/{project}/graph", config_id=c["config_id"])["summary"]
        for c in configs
    }
    record(f"links at file level: {sorted(links)}")
    record(f"configurations: {[(c['config_id'], c['config']['target_output_level']) for c in configs]}")
    record(f"graph per configuration: {graphs}")

    assert response.status_code == 201
    assert links == {("UC1.txt", "Auth.java"), ("UC2.txt", "Auth.java"), ("UC3.txt", "Loans.java")}
    assert len(configs) == 2 and configs[0]["config_key"] != configs[1]["config_key"]
    assert len(get(client, headers, f"/projects/{project}/versions")) == 1
    assert graphs["function"]["links_active"] == 3 and graphs["file"]["links_active"] == 3


@case(
    id="I-04",
    feature="New analysis / session run saved later",
    level="integration",
    priority="High",
    why="A visitor can run without an account and save afterwards. The files uploaded for that run must still be there to claim.",
    input="Run an upload anonymously in session mode; then sign up and save the result into a new project",
    expected="The anonymous job succeeds with 3 links. Saving returns 201 with 2 artifacts claimed, and the project gets 2 sources and version 1",
)
def test_anonymous_run_can_be_saved_into_a_project_afterwards(client, record):
    """A run made without an account keeps its files long enough to be saved once the user signs in."""
    result = run_analysis(client)
    record(f"anonymous run: {len(result['trace_links'])} links, upload id present: {bool(result['upload_id'])}")

    headers = sign_up(client)
    project = new_project(client, headers)
    saved = save_analysis(client, headers, project, result)
    sources = get(client, headers, f"/projects/{project}/sources")
    record(f"save: {saved.status_code}, artifacts claimed: {saved.json().get('artifact_count')}, sources: {len(sources)}")

    assert link_pairs(result["trace_links"]) == EXPECTED_LINKS
    assert saved.status_code == 201 and saved.json()["artifact_count"] == 2
    versions = get(client, headers, f"/projects/{project}/versions")
    assert len(sources) == 2 and [v["version_number"] for v in versions] == [1]


@case(
    id="I-27",
    feature="Artifact kinds / architecture document",
    level="integration",
    priority="High",
    why="Architecture documents use the same preprocessors as requirements. Without the kinds in the key, the two relations would share one configuration and one graph.",
    preconditions="A project with a saved requirements->code run",
    input="In the same project, run architecture_document->code with exactly the same settings",
    expected="2 configurations with different keys, 2 pairs, each with its own graph: 3 links from requirements, and arch.txt linked to login, logout and borrow",
)
def test_architecture_document_is_its_own_kind_with_its_own_graph(client, record):
    """An architecture document traced to code is a separate relation from requirements traced to code."""
    headers = sign_up(client)
    project = new_project(client, headers)
    analyse_and_save(client, headers, project)
    documents = analyse_and_save(client, headers, project, source=ARCHITECTURE_DOCUMENT)

    configs = get(client, headers, f"/projects/{project}/configs")
    pairs = get(client, headers, f"/projects/{project}/pairs")
    graphs = {
        c["source_kind"]: get(client, headers, f"/projects/{project}/graph", config_id=c["config_id"])["summary"]["links_active"]
        for c in configs
    }
    record(f"document links: {sorted(link_pairs(documents['result']['trace_links']))}")
    record(f"configurations: {[(c['source_kind'], c['target_kind'], c['config_key']) for c in configs]}")
    record(f"pairs: {[(p['source_kind'], p['target_kind']) for p in pairs]}; active links per graph: {graphs}")

    assert link_pairs(documents["result"]["trace_links"]) == {
        ("arch.txt", "Auth.java::Auth::login"), ("arch.txt", "Auth.java::Auth::logout"),
        ("arch.txt", "Loans.java::Loans::borrow"),
    }
    assert len({c["config_key"] for c in configs}) == 2
    assert [(p["source_kind"], p["target_kind"]) for p in pairs] == [
        ("requirements", "code"), ("architecture_document", "code"),
    ]
    assert graphs == {"requirements": 3, "architecture_document": 3}


@case(
    id="I-28",
    feature="Configuration identity / expansion depth through the API",
    level="integration",
    priority="Medium",
    why="The UI can still send depth 1 for a non-code target. What is stored must be what happened, or the same run files under two configurations.",
    input="Run requirements->architecture_document, sending dependency_expansion_depth=1",
    expected="The saved configuration and the saved analysis both report depth 0",
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
    record(f"sent depth 1; configuration stores {config['config']['dependency_expansion_depth']}, "
           f"analysis stores {saved['dependency_expansion_depth']}")

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
