from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import documents

# Member 1 will add: from backend.routers import auth, sharing
# Member 3 will add: from backend.routers import collaboration, ai

app = FastAPI(title="Collaborative Doc Editor API", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(documents.router)
# Member 1: app.include_router(auth.router)
# Member 1: app.include_router(sharing.router)
# Member 3: app.include_router(collaboration.router)
# Member 3: app.include_router(ai.router)


@app.get("/api/health")
def health():
    return {"status": "ok"}
