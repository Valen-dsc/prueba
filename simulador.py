"""
SIMULADOR ESP32 — El Morichal (HTTP POST a Railway)
=====================================================
Simula el comportamiento del sistema eléctrico venezolano:

Programación diaria:
  - Bajones: 1-2 por día (a veces 3)
  - Apagón: día sí, día no (2-8 h)
  - Recuperación: picos inestables al restablecer

Ciclo típico en día de apagón:
  NORMAL (1-3 h) → BAJÓN (10-60 min) → NORMAL → BAJÓN →
  NORMAL → APAGÓN (2-8 h) → RECUPERACIÓN (2-15 min) → NORMAL
"""

import asyncio
import logging
import os
import random
import time
from datetime import datetime, timezone
from enum import Enum

import httpx

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - SIMULADOR - %(message)s",
)
logger = logging.getLogger("simulador")

BACKEND_URL = "https://proyecto-valentina-production.up.railway.app"
DATABASE_URL = os.getenv("DATABASE_URL")
INTERVALO = int(os.getenv("INTERVALO", "5"))


class EstadoRed(Enum):
    NORMAL = "NORMAL"
    BAJON = "BAJON"
    APAGON = "APAGON"
    RECUPERACION = "RECUPERACION"


class RedElectrica:
    """
    Red eléctrica con programación diaria realista.

    - Día par → apagón programado (ocurre tras los bajones del día)
    - Día impar → sin apagón, solo bajones
    - Bajones: 1-2 por día (a veces 3, a veces ninguno)
    """

    def __init__(self):
        self.estado = EstadoRed.NORMAL
        self.cambio_en = time.time() + 10
        self.siguiente = EstadoRed.NORMAL
        self.dia_num = -1
        self.apagon_pendiente = False
        self.bajones_pendientes = 0
        self._iniciar_dia()
        self._programar()

    def _iniciar_dia(self):
        dia = int(time.time()) // 86400
        if dia == self.dia_num:
            return
        self.dia_num = dia
        self.bajones_pendientes = random.choices([0, 1, 2, 3], weights=[0.1, 0.4, 0.4, 0.1])[0]
        self.apagon_pendiente = (dia % 2 == 0)
        if self.apagon_pendiente:
            logger.info(f"📅 Día {dia}: apagón PROGRAMADO, ~{self.bajones_pendientes} bajón(es)")
        else:
            logger.info(f"📅 Día {dia}: sin apagón, ~{self.bajones_pendientes} bajón(es)")

    def _programar(self):
        ahora = time.time()
        self._iniciar_dia()

        if self.estado == EstadoRed.NORMAL:
            if self.bajones_pendientes > 0:
                self.bajones_pendientes -= 1
                logger.info(f"  ↳ Próximo bajón en ~1-3 h  (quedan {self.bajones_pendientes})")
                self.cambio_en = ahora + random.uniform(3600, 10800)
                self.siguiente = EstadoRed.BAJON
            elif self.apagon_pendiente:
                self.apagon_pendiente = False
                logger.info(f"  ↳ APAGÓN programado en ~30 min - 2 h")
                self.cambio_en = ahora + random.uniform(1800, 7200)
                self.siguiente = EstadoRed.APAGON
            else:
                self.cambio_en = ahora + random.uniform(3600, 14400)
                self.siguiente = EstadoRed.NORMAL

        elif self.estado == EstadoRed.BAJON:
            self.cambio_en = ahora + random.uniform(600, 3600)
            self.siguiente = EstadoRed.NORMAL

        elif self.estado == EstadoRed.APAGON:
            self.cambio_en = ahora + random.uniform(7200, 28800)
            self.siguiente = EstadoRed.RECUPERACION

        elif self.estado == EstadoRed.RECUPERACION:
            self.cambio_en = ahora + random.uniform(120, 900)
            self.siguiente = EstadoRed.NORMAL

    def _transicionar(self):
        self.estado = self.siguiente
        if self.estado == EstadoRed.APAGON:
            logger.warning("⚡ ¡APAGÓN! Red fuera de servicio")
        elif self.estado == EstadoRed.RECUPERACION:
            logger.warning("⚡ LUZ RESTAURADA — Recuperación inestable")
        elif self.estado == EstadoRed.BAJON:
            logger.warning("⚠️  Bajón de voltaje — red debilitada")
        elif self.estado == EstadoRed.NORMAL and self.siguiente != EstadoRed.NORMAL:
            pass
        self._programar()

    def generar_lectura(self) -> tuple[dict, str, bool]:
        ahora = time.time()
        if ahora >= self.cambio_en:
            self._transicionar()

        if self.estado == EstadoRed.APAGON:
            v = 0.0
            calidad = "CRITICA"
            es_fluctuacion = True

        elif self.estado == EstadoRed.BAJON:
            v = random.uniform(85, 105)
            if random.random() < 0.03:
                v -= random.uniform(5, 15)
            calidad = "ADVERTENCIA"
            es_fluctuacion = True

        elif self.estado == EstadoRed.RECUPERACION:
            if random.random() < 0.06:
                v = 120 + random.uniform(15, 35)
                calidad = "CRITICA"
            elif random.random() < 0.04:
                v = 120 - random.uniform(20, 40)
                calidad = "CRITICA"
            elif random.random() < 0.10:
                v = 120 + random.uniform(8, 15)
                calidad = "ADVERTENCIA"
            else:
                v = 120 + random.gauss(0, 6)
                calidad = "ESTABLE"
            v = max(min(v, 160), 0)
            es_fluctuacion = calidad != "ESTABLE"

        else:  # NORMAL
            v = 120 + random.gauss(0, 1.5)
            calidad = "ESTABLE"
            es_fluctuacion = False

        return {
            "voltaje": round(max(v, 0), 2),
            "corriente": round(0, 2),
            "potencia": round(0, 2),
        }, calidad, es_fluctuacion


red = RedElectrica()


def _tiene_motor(nombre: str, tipo: str) -> bool:
    texto = (nombre + " " + tipo).upper()
    return any(p in texto for p in ["AIRE", "NEVERA", "COMPRESOR", "MOTOR", "CONGELADOR", "LAVADORA"])


def simular_lectura(equipo: dict) -> dict:
    eid = equipo["id"]
    p_nom = float(equipo.get("potencia_nominal_watts", 500))
    i_max = float(equipo.get("corriente_maxima_segura", 999))

    linea, calidad, es_fluctuacion = red.generar_lectura()
    v = linea["voltaje"]

    if v == 0:
        c = 0.0
        p = 0.0
        calidad = "CRITICA"
        es_fluctuacion = True
    else:
        c = p_nom / v if p_nom > 0 else random.uniform(0.5, 5.0)
        c += random.gauss(0, 0.15 * c)
        c = max(c, 0.1)

        if _tiene_motor(equipo.get("nombre_equipo", ""), equipo.get("tipo", "")):
            if red.estado == EstadoRed.RECUPERACION and random.random() < 0.10:
                c *= random.uniform(3, 6)
                logger.info(f"  [{eid}] ⚡ ARRANQUE compresor/motor — {c:.1f}A")

        if c > i_max * 1.3:
            calidad = "CRITICA"
            es_fluctuacion = True
        elif c > i_max and calidad == "ESTABLE":
            calidad = "ADVERTENCIA"
            es_fluctuacion = True

        p = v * c

    linea["corriente"] = round(c, 2)
    linea["potencia"] = round(p, 2)

    return {
        "electrodomestico_id": eid,
        "lineas": [linea],
        "es_fluctuacion": es_fluctuacion,
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
        payload = simular_lectura(equipo)
        ok = await enviar(payload)
        linea = payload["lineas"][0]
        estado = red.estado.value
        logger.info("[%d] %s V=%.1f I=%.2f P=%.0f [%s] %s",
                    equipo["id"],
                    "OK" if ok else "FAIL",
                    linea["voltaje"], linea["corriente"],
                    linea["potencia"], estado,
                    payload["calidad_energia"])
        await asyncio.sleep(INTERVALO)


async def main():
    logger.info("=" * 50)
    logger.info("SIMULADOR ELÉCTRICO — Venezuela")
    logger.info("Modo: HTTP POST a %s", BACKEND_URL)
    logger.info("Ciclo: NORMAL → BAJÓN → APAGÓN (día sí/no) → RECUPERACIÓN")
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
    await asyncio.gather(*tareas)


async def run_forever():
    try:
        await main()
    except asyncio.CancelledError:
        logger.info("Simulador detenido.")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(run_forever())
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Simulador detenido.")
