// Asistente de voz (Retell AI): boton flotante del conductor.
//
// Flujo: 1) se pide el microfono antes que nada, para no crear llamadas que no
// se pueden usar; 2) el servidor crea la llamada web (la API key nunca llega al
// navegador) y devuelve los datos para unirse; 3) el SDK web se conecta.
//
// Se usa RetellWebClient de retell-client-js-sdk 3.0.2. Esta marcada como
// obsoleta y se elimina en la 4.0, pero es la unica clase del SDK web que se
// une a una llamada creada en el servidor: la nueva (RetellClient) crea la
// llamada desde el navegador con la API key. Ver README, "Asistente de voz".
// El SDK se carga al primer toque y no en cada pagina: pesa y casi ninguna
// visita lo usa.

const SDK_URL = "https://cdn.jsdelivr.net/npm/retell-client-js-sdk@3.0.2/+esm";

// Tiempo que el estado "Terminado" (o un error) queda visible antes de ocultarse.
const ESPERA_CIERRE_MS = 4000;

const contenedor = document.getElementById("asistente");
const boton = document.getElementById("asistente-boton");
const etiqueta = document.getElementById("asistente-estado");

let cliente = null;
let estado = "inactivo";
let temporizador = null;

const TEXTOS = {
  conectando: "Conectando...",
  escuchando: "Escuchando. Toque para colgar.",
  terminado: "Llamada terminada.",
};

function mostrar(nuevo, mensaje) {
  estado = nuevo;
  contenedor.dataset.estado = nuevo;
  clearTimeout(temporizador);

  const texto = mensaje || TEXTOS[nuevo];
  etiqueta.textContent = texto || "";
  etiqueta.hidden = !texto;

  boton.disabled = nuevo === "conectando";
  boton.setAttribute("aria-pressed", nuevo === "escuchando" ? "true" : "false");
  boton.setAttribute(
    "aria-label",
    nuevo === "escuchando" ? "Colgar la llamada con el asistente" : "Hablar con el asistente de voz",
  );

  if (nuevo === "terminado" || nuevo === "error") {
    temporizador = setTimeout(() => mostrar("inactivo"), ESPERA_CIERRE_MS);
  }
}

function fallar(mensaje) {
  if (cliente) {
    const anterior = cliente;
    cliente = null;
    anterior.stopCall();
  }
  mostrar("error", mensaje);
}

// Solo verifica el permiso: la pista se libera enseguida y el SDK abre la suya.
async function pedirMicrofono() {
  if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
    throw new Error("El microfono requiere abrir la aplicacion con https.");
  }
  try {
    const flujo = await navigator.mediaDevices.getUserMedia({ audio: true });
    flujo.getTracks().forEach((pista) => pista.stop());
  } catch (error) {
    if (error.name === "NotAllowedError" || error.name === "SecurityError") {
      throw new Error(
        "Permiso de microfono denegado. Habilitelo en la configuracion del navegador para usar el asistente.",
      );
    }
    if (error.name === "NotFoundError") {
      throw new Error("No se encontro un microfono en este dispositivo.");
    }
    throw new Error("No se pudo usar el microfono.");
  }
}

async function crearLlamada() {
  let respuesta;
  try {
    respuesta = await fetch(contenedor.dataset.url, {
      method: "POST",
      credentials: "same-origin",
      headers: { "X-CSRFToken": contenedor.dataset.csrf, Accept: "application/json" },
    });
  } catch {
    throw new Error("Sin conexion. Revise su internet e intente de nuevo.");
  }

  const tipo = respuesta.headers.get("Content-Type") || "";
  if (!tipo.includes("application/json")) {
    // Login vencido (redirige al formulario) o token CSRF caducado.
    throw new Error("Su sesion expiro. Recargue la pagina e intente de nuevo.");
  }
  const datos = await respuesta.json();
  if (!respuesta.ok) {
    throw new Error(datos.error || "No se pudo iniciar el asistente.");
  }
  return datos;
}

async function iniciar() {
  mostrar("conectando");
  try {
    await pedirMicrofono();

    let RetellWebClient;
    try {
      ({ RetellWebClient } = await import(SDK_URL));
    } catch {
      throw new Error("No se pudo cargar el asistente. Revise su conexion.");
    }

    const llamada = await crearLlamada();

    // Cada listener ignora los eventos de un cliente ya descartado: stopCall()
    // emite "call_ended", que de otro modo taparia el mensaje de un error.
    const actual = new RetellWebClient();
    cliente = actual;
    actual.on("call_started", () => {
      if (cliente === actual) mostrar("escuchando");
    });
    actual.on("call_ended", () => {
      if (cliente !== actual) return;
      cliente = null;
      mostrar("terminado");
    });
    actual.on("error", () => {
      if (cliente === actual) fallar("Se perdio la conexion con el asistente.");
    });

    // /v3/create-web-call responde con transporte "gateway", que exige el
    // call_id y los servidores ICE ademas del token.
    await actual.startCall({
      accessToken: llamada.access_token,
      transport: llamada.transport,
      callId: llamada.call_id,
      iceServers: llamada.ice_servers,
    });
  } catch (error) {
    fallar(error.message || "No se pudo iniciar el asistente.");
  }
}

function colgar() {
  if (cliente) {
    const actual = cliente;
    cliente = null;
    actual.stopCall();
  }
  mostrar("terminado");
}

boton.addEventListener("click", () => {
  if (estado === "escuchando") {
    colgar();
  } else if (estado !== "conectando") {
    iniciar();
  }
});

// Cerrar o salir de la pagina corta la llamada y libera el microfono.
window.addEventListener("pagehide", () => {
  if (cliente) cliente.stopCall();
});
