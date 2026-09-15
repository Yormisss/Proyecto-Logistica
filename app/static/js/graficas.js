/* Capa de interaccion de las graficas: tooltip al pasar el cursor.
   Cada marca declara su texto en data-tooltip, de modo que el valor exacto
   siempre esta disponible sin saturar el grafico con etiquetas. */
(function () {
  const marcas = document.querySelectorAll("[data-tooltip]");
  if (!marcas.length) return;

  const globo = document.createElement("div");
  globo.className = "tooltip-grafica";
  globo.setAttribute("role", "status");
  globo.hidden = true;
  document.body.appendChild(globo);

  function mostrar(evento) {
    const texto = evento.currentTarget.getAttribute("data-tooltip");
    if (!texto) return;
    globo.textContent = texto;
    globo.hidden = false;
    ubicar(evento);
  }

  function ubicar(evento) {
    const caja = globo.getBoundingClientRect();
    let x = evento.clientX + 14;
    let y = evento.clientY - caja.height - 10;
    // No dejar que el globo se salga de la ventana.
    if (x + caja.width > window.innerWidth - 8) x = evento.clientX - caja.width - 14;
    if (y < 8) y = evento.clientY + 18;
    globo.style.left = x + "px";
    globo.style.top = y + "px";
  }

  function ocultar() { globo.hidden = true; }

  marcas.forEach(function (marca) {
    marca.addEventListener("mouseenter", mostrar);
    marca.addEventListener("mousemove", ubicar);
    marca.addEventListener("mouseleave", ocultar);
    // Accesible por teclado: la marca recibe foco y anuncia el mismo texto.
    marca.setAttribute("tabindex", "0");
    marca.addEventListener("focus", function () {
      globo.textContent = marca.getAttribute("data-tooltip");
      globo.hidden = false;
      const caja = marca.getBoundingClientRect();
      globo.style.left = caja.left + "px";
      globo.style.top = (caja.top - 34) + "px";
    });
    marca.addEventListener("blur", ocultar);
  });

  window.addEventListener("scroll", ocultar, { passive: true });
})();
