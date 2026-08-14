import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import auth_router, project_router, router
from core.db import init_db
from core.projects.artifact_store import purge_expired_uploads

logging.basicConfig(level=logging.INFO)


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Uploads whose run was never saved are dead weight; clear the backlog at
    # startup as well as on each new upload.
    purge_expired_uploads()
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
app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}
