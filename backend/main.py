import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import (
    auth_router, github_router, job_router, project_router, router, source_router, sync_router,
)
from api.uploads import upload_error_response
from core import jobs
from core.db import init_db
from core.db.session import SessionLocal
from core.projects.artifact_store import purge_expired_uploads, purge_trash
from core.projects.uploads import UploadError

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    purge_expired_uploads()
    purge_trash()
    with SessionLocal() as db:
        jobs.sweep_unfinished(db)
    yield

app = FastAPI(
    title="TraceRAG API",
    description="RAG-based Software Traceability Link Recovery",
    version="0.1.0",
    lifespan=lifespan,
)

# Vite dev server origins. Tighten/extend when the frontend gets deployed.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Upload refusals are raised without knowing about HTTP; this is where they
# become the 400 or 413 the client reads.
app.add_exception_handler(UploadError, upload_error_response)

app.include_router(auth_router)
app.include_router(project_router)
app.include_router(github_router)
app.include_router(source_router)
app.include_router(sync_router)
app.include_router(job_router)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
