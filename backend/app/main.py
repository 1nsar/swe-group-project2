from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .routes_auth import router as auth_router
from .routes_docs import router as docs_router

app = FastAPI(title="Collaborative Document Editor API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth_router)
app.include_router(docs_router)

@app.get("/")
def root():
    return {"message": "API is running"}