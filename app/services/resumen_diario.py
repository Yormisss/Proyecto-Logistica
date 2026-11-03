"""Resumen diario de la operacion: una sola fuente para la consulta y el envio.

`contenido` arma el resumen del dia (KPIs, productos bajo el minimo, fallidos
para reprogramar y el HTML del correo), con las cifras de `analitica`, las
mismas del tablero. Lo usan:

* GET /api/automatizacion/resumen-diario, que Make consultaba en un horario
  fijo y queda como respaldo (siempre como "Sistema SGDS (automático)");
* `enviar`, que lo manda a Make como evento `resumen_diario` por
  MAKE_WEBHOOK_RESUMEN_URL (el escenario privado, "Rutalytics Privado"). Lo
  disparan el boton del tablero, Gaby (gestor/resumen-diario:enviar) y el
  comando `flask --app run enviar-resumen`, que se programa en el Programador
  de tareas de Windows o en el cron del servidor. Make nunca llama a la app.

El evento lleva exactamente los campos de la respuesta del GET, en la raiz,
mas `tipo`; quien lo origino es quien lo pidio, o el sistema si fue el comando.
Sale un evento por destinatario: los gestores y los administradores activos,
cada uno con su correo de avisos en `correo_destino` (CORREO_OPERACIONES solo
si ninguno tiene correo). El GET de respaldo sigue con CORREO_OPERACIONES.
"""

from flask import current_app, render_template
from sqlalchemy.orm import joinedload, selectinload

from app.extensions import db
from app.models import Cliente, EstadoPedido, Pedido
from app.services import analitica
from app.services.notificaciones import (
    DESTINATARIOS_RESUMEN, ORIGEN_AUTOMATICO, TIPO_RESUMEN_DIARIO, correos_de_rol, datos_actor,
    encolar_evento, esperar_envios,
)
from app.tiempo import hoy, iso_bogota

CLAVE_URL = "MAKE_WEBHOOK_RESUMEN_URL"
# Entidad de la auditoria (y de la funcion de voz).
ENTIDAD = "resumen"

SIN_WEBHOOK = ("No se envió el resumen: falta configurar el webhook del escenario privado "
               "(MAKE_WEBHOOK_RESUMEN_URL).")
SIN_CORREO = ("No se envió el resumen: no hay gestores ni administradores activos con correo, ni "
              "un correo de respaldo (CORREO_OPERACIONES).")


class ResumenNoEnviado(Exception):
    """Falta la configuracion para enviar el resumen. El mensaje dice cual."""


def correo_destino():
    """CORREO_OPERACIONES, o None si no esta configurado (el destino del GET de respaldo)."""
    return (current_app.config.get("CORREO_OPERACIONES") or "").strip() or None


def destinatarios():
    """Los correos a los que sale el resumen: gestores y administradores activos, o el respaldo."""
    return correos_de_rol(DESTINATARIOS_RESUMEN)


def webhook_configurado():
    return bool((current_app.config.get(CLAVE_URL) or "").strip())


def _fallidos_para_reprogramar(fecha, actor):
    """Pedidos FALLIDOS del dia cuyo cliente tiene correo para coordinar otra visita."""
    pedidos = (
        db.session.query(Pedido)
        .join(Cliente, Pedido.cliente_id == Cliente.id)
        .options(selectinload(Pedido.pruebas_entrega), joinedload(Pedido.cliente))
        .filter(
            Pedido.estado == EstadoPedido.FALLIDO,
            Pedido.fecha_despacho == fecha,
            Cliente.correo.isnot(None),
        )
        .order_by(Pedido.codigo)
        .all()
    )
    filas = []
    for pedido in pedidos:
        correo = (pedido.cliente.correo or "").strip()
        if not correo:
            continue
        filas.append({
            "codigo": pedido.codigo,
            "cliente": pedido.cliente_nombre,
            "correo": correo,
            "direccion": pedido.direccion_completa,
            "motivo": pedido.prueba_entrega.motivo_fallo if pedido.prueba_entrega else None,
            **actor,
        })
    return filas


def contenido(actor=None):
    """El resumen de hoy como dict, con `actor` (de `datos_actor`) como quien lo origino.

    Sin `actor`, el sistema (automatico), como en el GET de respaldo. Cada
    fallido para reprogramar lleva el mismo actor que el resumen.
    """
    actor = actor or datos_actor(origen=ORIGEN_AUTOMATICO)
    fecha = hoy()
    kpis_dia = analitica.kpis_del_dia(fecha)
    ventana = analitica.cumplimiento_ventana(dias=1, hasta=fecha)

    kpis = {
        "total": kpis_dia["total_dia"],
        "entregados": kpis_dia["entregados"],
        "fallidos": kpis_dia["fallidos"],
        "cancelados": kpis_dia["cancelados"],
        "pendientes": kpis_dia["pendientes"],
        "tasa_exito": kpis_dia["porcentaje_exito"],
        # None si hoy no hubo entregas con ventana horaria que medir.
        "cumplimiento_ventana": ventana["porcentaje"],
    }
    productos = [
        {
            "sku": producto.sku,
            "producto": producto.nombre,
            "stock_actual": producto.stock_actual,
            "stock_minimo": producto.stock_minimo,
            "negativo": producto.stock_actual < 0,
        }
        for producto in analitica.productos_bajo_minimo()
    ]
    fallidos = _fallidos_para_reprogramar(fecha, actor)

    return {
        "fecha": fecha.isoformat(),
        "fecha_hora": iso_bogota(),
        "correo_destino": correo_destino(),
        "kpis": kpis,
        "productos_bajo_minimo": productos,
        "fallidos_para_reprogramar": fallidos,
        "resumen_html": render_template(
            "automatizacion/resumen_diario.html",
            fecha=fecha, kpis=kpis, productos=productos, fallidos=fallidos,
        ),
        **actor,
    }


def verificar_envio():
    """Lanza ResumenNoEnviado si falta el webhook o no hay a quien enviarlo. Devuelve los correos."""
    if not webhook_configurado():
        raise ResumenNoEnviado(SIN_WEBHOOK)
    correos = destinatarios()
    if not correos:
        raise ResumenNoEnviado(SIN_CORREO)
    return correos


def enviar(usuario=None, origen=None):
    """Encola un evento `resumen_diario` por destinatario y devuelve el contenido.

    El contenido lleva en `destinatarios` la lista de correos (no viaja en el
    evento: cada evento lleva el suyo en `correo_destino`). `usuario` es quien
    lo pidio (pantalla o voz); sin el, el sistema (automatico). Los avisos
    salen despues del commit de quien llama, como todo evento de Make. Lanza
    ResumenNoEnviado si falta configuracion.
    """
    correos = verificar_envio()
    actor = datos_actor(usuario, origen) if usuario is not None else datos_actor(origen=ORIGEN_AUTOMATICO)
    datos = contenido(actor)
    campos = {clave: valor for clave, valor in datos.items() if clave not in actor}
    for correo in correos:
        encolar_evento(TIPO_RESUMEN_DIARIO, {**campos, "correo_destino": correo}, actor, clave_url=CLAVE_URL)
    return {**datos, "destinatarios": correos}


def esperar(segundos=10):
    """Espera a que el aviso salga (el comando termina el proceso al volver)."""
    esperar_envios(segundos)


def registrar_envio(datos):
    """Deja el envio en la auditoria, sin el correo accion_asistente (el resumen ya es un correo).

    La pantalla y el comando lo llaman; por voz lo registra la confirmacion,
    como toda accion del asistente.
    """
    from app.services.auditoria import registrar_auditoria

    registrar_auditoria(
        "Enviar resumen del día", ENTIDAD,
        f"Envió el resumen del {datos['fecha']} a {', '.join(datos['destinatarios'])}.",
        {"fecha": datos["fecha"], "destinatarios": datos["destinatarios"], "kpis": datos["kpis"]},
        avisar=False,
    )
