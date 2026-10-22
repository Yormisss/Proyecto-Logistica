// Desplegables del menu (<details data-menu-desplegable>). El teclado ya funciona
// sin este script (Tab, Enter y Espacio sobre el <summary>); aqui se agrega lo que
// <details> no hace solo: cerrar con Escape devolviendo el foco, y cerrar al hacer
// clic fuera o al salir con Tab.
document.addEventListener("DOMContentLoaded", function () {
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
