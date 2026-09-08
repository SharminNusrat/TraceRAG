import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import auth_router, integration_router, job_router, project_router, router
from core import jobs
from core.db import init_db
from core.db.session import SessionLocal
from core.projects.artifact_store import purge_expired_uploads, purge_trash

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-5s %(name)s | %(message)s",
    datefmt="%H:%M:%S",
    # Uvicorn configures logging before importing this module, so basicConfig
    # would otherwise find a handler already in place and do nothing.
    force=True,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Uploads whose run was never saved are dead weight; clear the backlog at
    # startup as well as on each new upload.
    purge_expired_uploads()
    # Retired blobs nobody came back for. Done here rather than during a sweep,
    # so the window to notice a wrong sweep is real time, not the next delete.
    purge_trash()
    # Background work does not survive a restart, so anything still claiming to
    # run is describing a process that is gone - and would block its project's
    # next sync for ever if it were left saying so.
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

app.include_router(auth_router)
app.include_router(project_router)
app.include_router(integration_router)
app.include_router(job_router)
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
