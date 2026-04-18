from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import documents, auth
# Member 3 will add: from backend.routers import collaboration, ai

app = FastAPI(title="Collaborative Doc Editor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5174"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(documents.router)
# Member 3: app.include_router(collaboration.router)
# Member 3: app.include_router(ai.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
