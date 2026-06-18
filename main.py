import asyncio
import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI
import uvicorn

logger = logging.getLogger("uvicorn")

simulador_task = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global simulador_task
    logger.info("Iniciando simulador en background...")
    from simulador import run_forever
    simulador_task = asyncio.create_task(run_forever())
    yield
    logger.info("Deteniendo simulador...")
    if simulador_task and not simulador_task.done():
        simulador_task.cancel()
        try:
            await simulador_task
        except asyncio.CancelledError:
            pass


app = FastAPI(title="Simulador ESP32 - Health", lifespan=lifespan)


@app.get("/health")
async def health():
    estado = "activo" if simulador_task and not simulador_task.done() else "inactivo"
    return {"status": f"simulador {estado}", "target": "proyecto-valentina"}


if __name__ == "__main__":
    import os
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run(app, host="0.0.0.0", port=port)
