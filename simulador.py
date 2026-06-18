"""
SIMULADOR ESP32 — El Morichal (HTTP POST a Railway)
=====================================================
Simula sensores ZMPT101B (voltaje) + FCS2151-SD (corriente)
enviando lecturas via HTTP POST al backend principal.

Corre 24/7 en Railway como proyecto independiente.
"""

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone, timedelta

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - SIMULADOR - %(message)s",
)
logger = logging.getLogger("simulador")

BACKEND_URL = "https://proyecto-valentina-production.up.railway.app"
DATABASE_URL = os.getenv("DATABASE_URL")
INTERVALO = int(os.getenv("INTERVALO", "5"))
DURACION_APAGON_HORAS = int(os.getenv("DURACION_APAGON_HORAS", "5"))

_estados_evento = {}
apagon_activo = False
apagon_fin = None


def _init_estado(eid: int):
    if eid not in _estados_evento:
        _estados_evento[eid] = {
            "activo": False,
            "tipo": None,
            "amplitud": 0,
            "pasos": 0,
        }


def _tiene_motor(nombre: str, tipo: str) -> bool:
    texto = (nombre + " " + tipo).upper()
    return any(p in texto for p in ["AIRE", "NEVERA", "COMPRESOR", "MOTOR", "CONGELADOR", "LAVADORA"])


def simular_lectura(equipo: dict) -> dict:
    eid = equipo["id"]
    _init_estado(eid)
    st = _estados_evento[eid]

    v_nom = float(equipo.get("voltaje_nominal", 120))
    v_min = float(equipo.get("voltaje_minimo_seguro", v_nom * 0.90))
    v_max = float(equipo.get("voltaje_maximo_seguro", v_nom * 1.10))
    v_cmin = float(equipo.get("voltaje_critico_minimo", v_nom * 0.75))
    v_cmax = float(equipo.get("voltaje_critico_maximo", v_nom * 1.25))

    if not st["activo"] and random.random() < 0.02:
        eventos = [
            ("sag", v_min - random.uniform(8, 15)),
            ("swell", v_max + random.uniform(3, 10)),
            ("critico_bajo", v_cmin - random.uniform(2, 8)),
            ("critico_alto", v_cmax + random.uniform(2, 8)),
        ]
        tipo, amp = random.choice(eventos)
        st["activo"] = True
        st["tipo"] = tipo
        st["amplitud"] = amp
        st["pasos"] = random.randint(3, 8)

    if st["activo"]:
        avance = 1.0 - (st["pasos"] / 8.0)
        objetivo = st["amplitud"] + (v_nom - st["amplitud"]) * avance
        voltaje = objetivo + random.gauss(0, 1.5)
        st["pasos"] -= 1
        if st["pasos"] <= 0:
            st["activo"] = False
    else:
        voltaje = v_nom + random.gauss(0, 1.2)

    if voltaje < v_cmin or voltaje > v_cmax:
        calidad = "CRITICA"
    elif voltaje < v_min or voltaje > v_max:
        calidad = "ADVERTENCIA"
    else:
        calidad = "ESTABLE"

    p_nom = float(equipo.get("potencia_nominal_watts", 500))
    i_max = float(equipo.get("corriente_maxima_segura", 999))
    corriente = max(p_nom / voltaje + random.gauss(0, 0.3), 0.1)

    if _tiene_motor(equipo.get("nombre_equipo", ""), equipo.get("tipo", "")):
        if random.random() < 0.02:
            corriente *= 3.0 + random.random() * 2.5
            logger.info("  [%d] ⚡ ARRANQUE compresor/motor", eid)

    if corriente > i_max * 1.3:
        calidad = "CRITICA"
    elif corriente > i_max and calidad == "ESTABLE":
        calidad = "ADVERTENCIA"

    return {
        "electrodomestico_id": eid,
        "lineas": [
            {
                "voltaje": round(voltaje, 2),
                "corriente": round(corriente, 2),
                "potencia": round(voltaje * corriente, 2),
            }
        ],
        "es_fluctuacion": calidad != "ESTABLE",
        "calidad_energia": calidad,
    }


async def cargar_equipos():
    import asyncpg

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch(
            "SELECT id, nombre_equipo, tipo, potencia_nominal_watts, "
            "voltaje_nominal, voltaje_minimo_seguro, voltaje_maximo_seguro, "
            "voltaje_critico_minimo, voltaje_critico_maximo, corriente_maxima_segura "
            "FROM electrodomesticos WHERE estado = 'ACTIVO' ORDER BY id"
        )
        equipos = [dict(r) for r in rows]
        logger.info("Equipos cargados: %d", len(equipos))
        for eq in equipos:
            logger.info("  [%d] %s — %dW @ %dV",
                        eq["id"], eq["nombre_equipo"],
                        eq["potencia_nominal_watts"], eq["voltaje_nominal"])
        return equipos
    finally:
        await conn.close()


async def enviar(payload: dict) -> bool:
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.post(f"{BACKEND_URL}/api/lectura", json=payload)
            if r.status_code != 200:
                logger.warning("HTTP %d: %s", r.status_code, r.text[:200])
            return r.status_code == 200
    except Exception as e:
        logger.warning("Error enviando: %s", e)
        return False


async def simular(equipo: dict):
    while True:
        global apagon_activo
        if apagon_activo:
            await asyncio.sleep(INTERVALO)
            continue

        payload = simular_lectura(equipo)
        ok = await enviar(payload)
        linea = payload["lineas"][0]
        logger.info("[%d] %s V=%.1f I=%.2f P=%.0f %s",
                    equipo["id"],
                    "OK" if ok else "FAIL",
                    linea["voltaje"], linea["corriente"],
                    linea["potencia"], payload["calidad_energia"])
        await asyncio.sleep(INTERVALO)


async def gestor_apagones():
    global apagon_activo, apagon_fin
    while True:
        if not apagon_activo and random.random() < 0.001:
            apagon_activo = True
            apagon_fin = datetime.now(timezone.utc) + timedelta(hours=DURACION_APAGON_HORAS)
            logger.warning("=" * 50)
            logger.warning("⚡ APAGON INICIADO — %d hora(s)", DURACION_APAGON_HORAS)
            logger.warning("   Restauracion estimada: %s", apagon_fin.strftime("%H:%M:%S"))
            logger.warning("=" * 50)

        if apagon_activo and datetime.now(timezone.utc) >= apagon_fin:
            apagon_activo = False
            apagon_fin = None
            logger.warning("=" * 50)
            logger.warning("🔦 LUZ RESTAURADA — Reanudando lecturas")
            logger.warning("=" * 50)

        await asyncio.sleep(60)


async def main():
    logger.info("=" * 50)
    logger.info("SIMULADOR ESP32 — El Morichal")
    logger.info("Modo: HTTP POST a %s", BACKEND_URL)
    logger.info("=" * 50)

    if not DATABASE_URL:
        logger.error("DATABASE_URL no configurada")
        return

    equipos = await cargar_equipos()
    if not equipos:
        logger.warning("No hay equipos activos en Neon. Saliendo.")
        return

    logger.info("Simulando %d equipo(s) cada %d s", len(equipos), INTERVALO)

    tareas = [asyncio.create_task(simular(eq)) for eq in equipos]
    tareas.append(asyncio.create_task(gestor_apagones()))
    await asyncio.gather(*tareas)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Simulador detenido.")
