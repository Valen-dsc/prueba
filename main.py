import asyncio
from fastapi import FastAPI
import uvicorn

app = FastAPI(title="Simulador ESP32 - Health")


@app.get("/health")
async def health():
    return {"status": "simulador corriendo", "target": "proyecto-valentina"}


if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
