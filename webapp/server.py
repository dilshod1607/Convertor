import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Convertor Telegram Mini App")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CURRENT_DIR = os.path.dirname(os.path.abspath(__file__))


@app.get("/")
async def read_index():
    return FileResponse(os.path.join(CURRENT_DIR, "index.html"))


@app.get("/health")
async def health():
    return {"status": "ok", "app": "Convertor Mini App"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("webapp.server:app", host="0.0.0.0", port=8090, reload=False)
