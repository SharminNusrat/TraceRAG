import logging
from fastapi import FastAPI
from api import router

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="TraceRAG API",
    description="RAG-based Software Traceability Link Recovery",
    version="0.1.0"
)

app.include_router(router)


@app.get("/health")
def health():
    return {"status": "ok"}