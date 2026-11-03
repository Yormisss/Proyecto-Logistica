// Boton "Actualizar" (_actualizar.html): vuelve a pedir la pagina y cambia el
// contenido de <main> por el nuevo, sin recargarla. Recargar la pagina que
// sostiene una llamada del asistente la cortaria (ver asistente.js); asi se ven
// los cambios hechos por voz sin colgar, sin perder el desplazamiento y sin
// sumar entradas al historial. Los scripts de la pagina que necesiten volver a
// enlazar sus eventos escuchan "contenido-actualizado" (ver graficas.js).
(function () {
  document.addEventListener("click", function (evento) {
    var boton = evento.target.closest("[data-actualizar]");
    if (boton && !boton.hasAttribute("aria-busy")) actualizar(boton);
  });

  function actualizar(boton) {
    var principal = document.querySelector("main.contenedor");
    var teniaFoco = document.activeElement === boton;
    boton.setAttribute("aria-busy", "true");
    boton.classList.add("actualizando");

    // Una vuelta minima del icono: en local la respuesta llega tan rapido que,
    // si nada cambio, no se notaria que se actualizo.
    var vuelta = new Promise(function (listo) { setTimeout(listo, 600); });
    var pedido = fetch(location.href, { credentials: "same-origin", headers: { Accept: "text/html" } });

    Promise.all([pedido, vuelta])
      .then(function (resultado) {
        var respuesta = resultado[0];
        var destino = new URL(respuesta.url);
        // Sesion vencida o cerrada desde otro equipo: el login explica por que.
        // Dentro del marco de una llamada, asistente.js lleva ahi la ventana entera.
        if (destino.pathname === boton.dataset.login) {
          location.assign(destino.href);
          return;
        }
        if (!respuesta.ok) throw new Error("HTTP " + respuesta.status);
        return respuesta.text().then(function (html) {
          var pagina = new DOMParser().parseFromString(html, "text/html");
          var nuevo = pagina.querySelector("main.contenedor");
          if (!nuevo) throw new Error("respuesta sin contenido");
          principal.innerHTML = nuevo.innerHTML;
          document.title = pagina.title;
          if (respuesta.redirected) history.replaceState(null, "", destino.href);
          if (teniaFoco) {
            var otro = principal.querySelector("[data-actualizar]");
            if (otro) otro.focus();
          }
          document.dispatchEvent(new CustomEvent("contenido-actualizado"));
        });
      })
      .catch(function () {
        boton.removeAttribute("aria-busy");
        boton.classList.remove("actualizando");
        avisarError(principal);
      });
  }

  function avisarError(principal) {
    var alertas = principal.querySelector(".alertas");
    if (!alertas) {
      alertas = document.createElement("div");
      alertas.className = "alertas";
      principal.prepend(alertas);
    }
    var aviso = alertas.querySelector("[data-error-actualizar]");
    if (!aviso) {
      aviso = document.createElement("div");
      aviso.className = "alerta alerta-error";
      aviso.setAttribute("role", "alert");
      aviso.setAttribute("data-error-actualizar", "");
      aviso.textContent = "No se pudo actualizar la información. Revise la conexión e intente de nuevo.";
      alertas.prepend(aviso);
    }
  }
})();
