from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import documents, auth
from backend.routers import collaboration, ai

app = FastAPI(
    title="Collaborative Doc Editor API",
    version="0.1.0",
    description=(
        "Backend for the Collaborative Document Editor with AI Writing "
        "Assistant. Member 1 owns auth/permissions, Member 2 owns documents/"
        "versioning, Member 3 owns realtime collaboration (WebSocket) and the "
        "AI streaming endpoints (SSE)."
    ),
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174", "http://127.0.0.1:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
app.include_router(collaboration.router)
app.include_router(ai.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
