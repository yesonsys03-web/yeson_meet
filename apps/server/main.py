from fastapi import FastAPI

from apps.server.api.v1.health import router as health_router

app = FastAPI(title="yeson-meet", version="0.0.0")
app.include_router(health_router, prefix="/api/v1")


def run() -> None:
    import uvicorn

    uvicorn.run("apps.server.main:app", host="0.0.0.0", port=8000, reload=False)


if __name__ == "__main__":
    run()
