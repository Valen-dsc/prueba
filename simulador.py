"""
SIMULADOR ESP32 — El Morichal (HTTP POST a Railway)
=====================================================
Simula el comportamiento del sistema eléctrico venezolano:

  NORMAL ──→ BAJÓN (85-105V, 5-60 min) ──→ NORMAL
     │                                          ↑
     └──→ APAGÓN (0V, 1-10 h) → RECUPERACIÓN ──┘
                                (picos 130-155V, 2-15 min)

Los tiempos y eventos buscan reflejar la realidad del país:
largos períodos estables, bajones en horas pico, apagones
que duran horas, y picos peligrosos al restablecer.
"""

import asyncio
import logging
import os
import random
from datetime import datetime, timezone, timedelta
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
    """Estados del sistema eléctrico venezolano."""
    NORMAL = "NORMAL"             # 120V ±2%, estable
    BAJON = "BAJON"               # 85-105V sostenido
    APAGON = "APAGON"             # 0V
    RECUPERACION = "RECUPERACION"  # Post-apagón, inestable con picos


class RedElectrica:
    """
    Simula una red eléctrica realista.
    Es singleton - una sola instancia para todos los equipos.
    """

    def __init__(self):
        self.estado = EstadoRed.NORMAL
        self._reiniciar_timer()

    def _reiniciar_timer(self):
        ahora = time.time()
        if self.estado == EstadoRed.NORMAL:
            self.cambio_en = ahora + random.uniform(600, 10800)  # 10 min - 3 h
            self.v_base = 120.0
            self.ruido = 1.5
        elif self.estado == EstadoRed.BAJON:
            self.cambio_en = ahora + random.uniform(300, 3600)   # 5 min - 1 h
            self.v_base = random.uniform(85, 105)
            self.ruido = 3.0
        elif self.estado == EstadoRed.APAGON:
            self.cambio_en = ahora + random.uniform(3600, 36000) # 1 - 10 h
            self.v_base = 0.0
            self.ruido = 0.0
        elif self.estado == EstadoRed.RECUPERACION:
            self.cambio_en = ahora + random.uniform(120, 900)    # 2 - 15 min
            self.v_base = 120.0
            self.ruido = 6.0

    def _transicionar(self):
        if self.estado == EstadoRed.NORMAL:
            r = random.random()
            if r < 0.003:
                logger.warning("⚡ ¡APAGÓN! Red fuera de servicio")
                self.estado = EstadoRed.APAGON
            elif r < 0.04:
                logger.info(f"⚠️  Bajón de voltaje — red debilitada")
                self.estado = EstadoRed.BAJON
            else:
                self.estado = EstadoRed.NORMAL

        elif self.estado == EstadoRed.BAJON:
            if random.random() < 0.05:
                logger.warning("⚡ Bajón empeora — APAGÓN")
                self.estado = EstadoRed.APAGON
            else:
                logger.info(f"✅ Red restablecida tras bajón")
                self.estado = EstadoRed.NORMAL

        elif self.estado == EstadoRed.APAGON:
            logger.warning("⚡ LUZ RESTAURADA — Recuperación inestable")
            self.estado = EstadoRed.RECUPERACION

        elif self.estado == EstadoRed.RECUPERACION:
            logger.info("✅ Red estable después de recuperación")
            self.estado = EstadoRed.NORMAL

        self._reiniciar_timer()

    def generar_lectura(self) -> tuple[dict, str, bool]:
        """
        Genera una lectura según el estado actual de la red.
        Retorna: (linea: dict, calidad: str, es_fluctuacion: bool)
        """
        ahora = time.time()
        if ahora >= self.cambio_en:
            self._transicionar()

        if self.estado == EstadoRed.APAGON:
            v = 0.0
            c = 0.0
            p = 0.0
            calidad = "CRITICA"
            es_fluctuacion = True

        elif self.estado == EstadoRed.BAJON:
            v = self.v_base + random.gauss(0, self.ruido)
            v = max(v, 60.0)  # no baja de 60V
            # Micro-caídas dentro del bajón
            if random.random() < 0.03:
                v -= random.uniform(5, 15)
            c = random.uniform(0.5, 8.0)
            p = v * c
            calidad = "ADVERTENCIA"
            es_fluctuacion = True

        elif self.estado == EstadoRed.RECUPERACION:
            # Voltaje inestable con picos peligrosos
            if random.random() < 0.06:
                # Pico de hasta 155V al restablecer
                v = 120 + random.uniform(15, 35)
                calidad = "CRITICA"
            elif random.random() < 0.04:
                # Micro-caída post-recuperación
                v = 120 - random.uniform(20, 40)
                calidad = "CRITICA"
            elif abs((120 + random.gauss(0, self.ruido)) - 120) > 15:
                v = 120 + random.gauss(0, self.ruido)
                calidad = "ADVERTENCIA"
            else:
                v = 120 + random.gauss(0, self.ruido)
                calidad = "ESTABLE"
            v = max(min(v, 160), 0)
            c = random.uniform(0.5, 10.0)
            p = v * c
            es_fluctuacion = calidad != "ESTABLE"

        else:  # NORMAL
            v = self.v_base + random.gauss(0, self.ruido)
            c = random.uniform(0.5, 5.0)
            p = v * c
            calidad = "ESTABLE"
            es_fluctuacion = False

        linea = {
            "voltaje": round(max(v, 0), 2),
            "corriente": round(max(c, 0), 2),
            "potencia": round(max(p, 0), 2),
        }
        return linea, calidad, es_fluctuacion


# Instancia global de la red (compartida entre equipos)
red = RedElectrica()


_estados_motor = {}


def _tiene_motor(nombre: str, tipo: str) -> bool:
    texto = (nombre + " " + tipo).upper()
    return any(p in texto for p in ["AIRE", "NEVERA", "COMPRESOR", "MOTOR", "CONGELADOR", "LAVADORA"])


def simular_lectura(equipo: dict) -> dict:
    """
    Genera una lectura realista para el equipo dados el estado de la red
    y las características particulares del equipo (motor, etc.).
    """
    eid = equipo["id"]
    p_nom = float(equipo.get("potencia_nominal_watts", 500))
    i_max = float(equipo.get("corriente_maxima_segura", 999))

    linea, calidad, es_fluctuacion = red.generar_lectura()
    v = linea["voltaje"]
    c = linea["corriente"]
    p = linea["potencia"]

    # Equipos con motor: arranque violento cuando vuelve la luz
    if _tiene_motor(equipo.get("nombre_equipo", ""), equipo.get("tipo", "")):
        if red.estado == EstadoRed.RECUPERACION and random.random() < 0.10:
            pico_corriente = c * random.uniform(3, 6)
            c = min(pico_corriente, i_max * 2)
            p = v * c
            linea["corriente"] = round(c, 2)
            linea["potencia"] = round(p, 2)
            calidad = "ADVERTENCIA"
            es_fluctuacion = True
            logger.info(f"  [{eid}] ⚡ ARRANQUE compresor/motor — {c:.1f}A")

    # Ajustar corriente según potencia nominal
    if p_nom > 0 and v > 0:
        c_esperada = p_nom / v
        variacion = random.gauss(0, 0.15 * c_esperada)
        c = max(c_esperada + variacion, 0.1)
        p = v * c
        linea["corriente"] = round(c, 2)
        linea["potencia"] = round(p, 2)

    # Verificar umbrales del equipo
    if v == 0:
        calidad = "CRITICA"
        es_fluctuacion = True
    elif c > i_max * 1.3:
        calidad = "CRITICA"
        es_fluctuacion = True
    elif c > i_max and calidad == "ESTABLE":
        calidad = "ADVERTENCIA"
        es_fluctuacion = True

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
    logger.info("Estados: NORMAL → BAJÓN/APAGÓN → RECUPERACIÓN → NORMAL")
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
