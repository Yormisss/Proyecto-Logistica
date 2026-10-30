// RNF5: cierre de sesion por inactividad (ver app/services/sesiones.py).
// El servidor es quien cierra la sesion; esto solo lleva la pantalla al login
// cuando se cumple el tiempo, sin esperar a que la persona haga clic. Antes
// pregunta cuanto queda, porque otra pestana pudo mantener viva la sesion, y
// esa consulta no cuenta como actividad.
(function () {
  var script = document.currentScript;
  var urlEstado = script.dataset.estado;
  var urlLogin = script.dataset.login;

  function programar(segundos) {
    setTimeout(revisar, (segundos + 1) * 1000);
  }

  function revisar() {
    fetch(urlEstado, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (respuesta) { return respuesta.json(); })
      .then(function (datos) {
        if (datos.restante > 0) {
          programar(datos.restante);
        } else {
          // El login cierra la sesion vencida y explica por que.
          window.location.assign(urlLogin);
        }
      })
      .catch(function () { programar(30); });
  }

  programar(Number(script.dataset.segundos));
})();
