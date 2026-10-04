"""Caches, background jobs, comparing two runs, and the schema itself."""

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import inspect, text

from core import jobs
from core.cache import PersistentEmbeddingCache, PersistentSummaryCache
from core.db.session import BACKEND_DIR, Base, engine
from core.projects import service
from core.schemas import Element, ElementLevel
from core.summarization import ElementSummarizer
from tests.fakes import FakeChatProvider, FakeClassifier
from tests.helpers import make_project, make_user, result_of, save_run, unique
from tests.recorder import case


def element(identifier: str, content: str) -> Element:
    return Element(identifier=identifier, type="text", content=content,
                   granularity=0, level=ElementLevel.ARTIFACT)


@case(
    id="G-21",
    feature="Caches / classification verdicts",
    level="graph",
    priority="High",
    why="This cache is why a re-run on unchanged files is fast, free and gives the same links. If it misses, results drift between runs for no reason.",
    input="Classify one requirement against 3 methods (two with identical text) twice, with a fresh classifier each time and the same cache namespace",
    expected="First time: 2 model calls (identical texts asked once). Second time: 0 model calls and the same links",
)
def test_second_classification_makes_no_model_calls(record):
    """A pair that was judged once is not asked about again, and gets the same verdict."""
    namespace = unique("classifier")
    requirement = element("UC1", f"A visitor performs a login. {namespace}")
    candidates = [
        (element("login", "boolean login(String name)"), 0.9),
        (element("login-copy", "boolean login(String name)"), 0.8),
        (element("report", "void printReport()"), 0.1),
    ]

    first = FakeClassifier(namespace)
    first_links = [r.target.identifier for r in first.classify(requirement, candidates)]
    second = FakeClassifier(namespace)
    second_links = [r.target.identifier for r in second.classify(requirement, candidates)]
    record(f"first run: {len(first.asked)} model calls, links {first_links}")
    record(f"second run: {len(second.asked)} model calls, links {second_links}")

    assert len(first.asked) == 2 and len(second.asked) == 0
    assert first_links == second_links == ["login", "login-copy"]


@case(
    id="G-20",
    feature="Comparison / compare_analyses",
    level="graph",
    priority="High",
    why="The sync dialog's +added / -removed numbers and the Compare page come from this. Wrong counts misreport what a change did.",
    preconditions="Base run: UC1->login (0.9), UC2->logout (0.8); UC3 unimplemented",
    input="Head run: UC1->login at 0.72 (score and band changed), UC3->borrow new, UC2->logout gone; UC2 now unimplemented",
    expected="added 1, removed 1, modified 1, unchanged 0; UC3 newly implemented, UC2 newly unimplemented; runs are comparable",
)
def test_two_runs_are_diffed_by_link(db, record):
    """A link is identified by the pair it joins: new pairs are added, missing ones removed, changed ones modified."""
    project = make_project(db)
    methods = ["login()", "logout()", "borrow()"]
    base = save_run(db, project, result_of(
        ["UC1", "UC2", "UC3"], methods, [("UC1", "login()", 0.9), ("UC2", "logout()", 0.8)],
        unimplemented=["UC3"],
    ))
    head = save_run(db, project, result_of(
        ["UC1", "UC2", "UC3"], methods, [("UC1", "login()", 0.72), ("UC3", "borrow()", 0.9)],
        unimplemented=["UC2"],
    ))

    diff = service.compare_analyses(db, base, head)
    record(f"summary: {diff['summary']}")
    record(f"modified fields: {diff['modified'][0]['changed_fields']}")
    record(f"newly implemented {diff['newly_implemented']}, newly unimplemented {diff['newly_unimplemented']}")

    assert diff["summary"] == {
        "base_total": 2, "head_total": 2, "added": 1, "removed": 1, "modified": 1, "unchanged": 0,
    }
    assert [(l["source_id"], l["target_id"]) for l in diff["added"]] == [("UC3", "borrow()")]
    assert [(l["source_id"], l["target_id"]) for l in diff["removed"]] == [("UC2", "logout()")]
    assert diff["modified"][0]["changed_fields"] == ["similarity_score", "confidence_level"]
    assert diff["newly_implemented"] == ["UC3"] and diff["newly_unimplemented"] == ["UC2"]
    assert diff["comparable"] and diff["config_differences"] == []


@case(
    id="G-24",
    feature="Background jobs / access and recovery",
    level="graph",
    priority="High",
    why="Job ids count up, so they are guessable. Only the owner or the token holder may read a job. And a job left 'running' by a crash blocks its project's next sync forever.",
    input="One job owned by user A. Read it as A, as user B, with its token, and with a wrong token. Then sweep unfinished jobs",
    expected="A and the right token can read it; B and a wrong token cannot. While running it is the project's active job; after the sweep it is failed and no longer blocks",
)
def test_jobs_are_private_and_do_not_outlive_a_restart(db, record):
    """A job is visible to its owner or its token holder only, and is failed if the server stopped under it."""
    owner, stranger = make_user(db), make_user(db)
    project = make_project(db, owner)
    job = jobs.create_job(db, owner.user_id, project.project_id, jobs.KIND_SYNC)
    jobs.start(db, job.job_id)

    access = {
        "owner": jobs.get_job(db, job.job_id, owner.user_id, None) is not None,
        "other user": jobs.get_job(db, job.job_id, stranger.user_id, None) is not None,
        "right token": jobs.get_job(db, job.job_id, None, job.token) is not None,
        "wrong token": jobs.get_job(db, job.job_id, None, "not-the-token") is not None,
        "nobody": jobs.get_job(db, job.job_id, None, None) is not None,
    }
    record(f"can read the job: {access}")
    assert access == {"owner": True, "other user": False, "right token": True, "wrong token": False, "nobody": False}

    assert jobs.active_job(db, project.project_id, jobs.KIND_SYNC).job_id == job.job_id
    swept = jobs.sweep_unfinished(db)
    db.refresh(job)
    record(f"swept {swept} unfinished job(s); state now '{job.state}': {job.error}")

    assert swept >= 1 and job.state == jobs.FAILED
    assert jobs.active_job(db, project.project_id, jobs.KIND_SYNC) is None


@case(
    id="G-22",
    feature="Caches / embeddings",
    level="graph",
    priority="Medium",
    why="Vectors from different models are not comparable. The namespace is what stops a changed model from being served another model's vectors.",
    input="Store vectors for 2 texts under one namespace; read them under that namespace and under another; store one of them again with a new vector",
    expected="Both found in their own namespace, none in the other, and the second write replaces the first",
)
def test_embedding_cache_keeps_namespaces_apart(record):
    """Embeddings are stored per text and per model namespace."""
    mine, other = PersistentEmbeddingCache(unique("model-a")), PersistentEmbeddingCache(unique("model-b"))
    texts = ["login with a passphrase", "borrow a title"]
    mine.set_many({texts[0]: [0.1, 0.2], texts[1]: [0.3, 0.4]})

    found, elsewhere = mine.get_many(texts + ["never stored"]), other.get_many(texts)
    mine.set_many({texts[0]: [0.9, 0.9]})
    record(f"own namespace: {len(found)} of 2 found; other namespace: {len(elsewhere)} found")
    record(f"after overwriting: {mine.get_many(texts[:1])[texts[0]]}")

    assert found == {texts[0]: [0.1, 0.2], texts[1]: [0.3, 0.4]}
    assert elsewhere == {}
    assert mine.get_many(texts[:1])[texts[0]] == [0.9, 0.9]


@case(
    id="G-23",
    feature="Caches / summaries",
    level="graph",
    priority="Medium",
    why="Summaries cost one model call per batch of methods. Paying again for unchanged code on every sync would make syncing slow and expensive.",
    input="Summarise 3 code elements (two with identical text) twice with the same cache; the third time with one element changed",
    expected="First: 1 chat call, every element gets a summary. Second: 0 chat calls, same summaries. Third: 1 call, for the changed element only",
)
def test_summaries_are_only_generated_once_per_text(record):
    """An element whose text has a stored summary is not sent to the model again."""
    cache = PersistentSummaryCache(unique("summary-model"))

    def run(contents):
        provider = FakeChatProvider()
        elements = [element(f"m{n}", content) for n, content in enumerate(contents)]
        ElementSummarizer(provider, cache=cache).summarize(elements)
        return len(provider.prompts), [e.summary for e in elements]

    marker = unique("body")
    original = [f"void login() {{}} // {marker}", f"void login() {{}} // {marker}", f"void logout() {{}} // {marker}"]
    first_calls, first = run(original)
    second_calls, second = run(original)
    third_calls, third = run(original[:2] + [f"void reset() {{}} // {marker}"])
    record(f"chat calls: first {first_calls}, second {second_calls}, third {third_calls}")
    record(f"summaries: {first}")

    assert (first_calls, second_calls, third_calls) == (1, 0, 1)
    assert all(first) and first == second
    assert third[:2] == first[:2] and third[2]


@case(
    id="G-26",
    feature="Database / migrations",
    level="graph",
    priority="Medium",
    why="The server runs the migrations at startup. A branch in the chain, or a model column no migration creates, breaks the app on a fresh database.",
    preconditions="The test database was built from empty by running every migration",
    input="The migration scripts, the revision the database is at, and every table and column the models declare",
    expected="Exactly one head revision; the database is at it; every model table and column exists in the database",
)
def test_migrations_build_the_schema_the_models_expect(record):
    """One unbroken migration chain produces every table and column the code uses."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "migrations"))
    heads = ScriptDirectory.from_config(config).get_heads()
    with engine.connect() as connection:
        current = connection.execute(text("SELECT version_num FROM alembic_version")).scalar()

    inspector = inspect(engine)
    missing = [
        f"{table.name}.{column.name}"
        for table in Base.metadata.sorted_tables
        for column in table.columns
        if column.name not in {c["name"] for c in inspector.get_columns(table.name)}
    ]
    record(f"heads: {heads}; database is at: {current}")
    record(f"model tables: {len(Base.metadata.tables)}; columns missing from the database: {missing or 'none'}")

    assert heads == [current]
    assert missing == []
