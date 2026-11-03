// Desplegables del menu (<details data-menu-desplegable>). El teclado ya funciona
// sin este script (Tab, Enter y Espacio sobre el <summary>); aqui se agrega lo que
// <details> no hace solo: cerrar con Escape devolviendo el foco, y cerrar al hacer
// clic fuera o al salir con Tab.
document.addEventListener("DOMContentLoaded", function () {
  // Boton "Menu" (tableta y movil): abre y cierra la navegacion plegada.
  var botonMenu = document.querySelector("[data-boton-menu]");
  if (botonMenu) {
    var barra = botonMenu.closest(".barra-superior");
    botonMenu.addEventListener("click", function () {
      var abierto = barra.classList.toggle("menu-abierto");
      botonMenu.setAttribute("aria-expanded", abierto ? "true" : "false");
    });
    document.addEventListener("keydown", function (evento) {
      if (evento.key === "Escape" && barra.classList.contains("menu-abierto")) {
        barra.classList.remove("menu-abierto");
        botonMenu.setAttribute("aria-expanded", "false");
        botonMenu.focus();
      }
    });
  }

  document.querySelectorAll("[data-menu-desplegable]").forEach(function (menu) {
    var resumen = menu.querySelector("summary");

    menu.addEventListener("keydown", function (evento) {
      if (evento.key === "Escape" && menu.open) {
        menu.open = false;
        resumen.focus();
      }
    });
    menu.addEventListener("focusout", function (evento) {
      if (evento.relatedTarget && !menu.contains(evento.relatedTarget)) {
        menu.open = false;
      }
    });
    document.addEventListener("click", function (evento) {
      if (menu.open && !menu.contains(evento.target)) {
        menu.open = false;
      }
    });
  });
});
