from fastapi import FastAPI
from app.api.upload import router as upload_router

app = FastAPI(title="AccessEd Backend")
app.include_router(upload_router, prefix="/api")

@app.get("/")
def root():
    return {"status": "ok"}
