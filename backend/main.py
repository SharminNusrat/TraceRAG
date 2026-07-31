import logging
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from api import router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="TraceRAG API",
    description="RAG-based Software Traceability Link Recovery",
    version="0.1.0"
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

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}