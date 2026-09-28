from fastapi import FastAPI

app = FastAPI(title="Food, Nutrition & Food Safety Chatbot API", version="0.1.0")


@app.get("/api/health")
async def health() -> dict[str, str]:
    # Phase 1 extends this with database and model readiness checks.
    return {"status": "ok"}
