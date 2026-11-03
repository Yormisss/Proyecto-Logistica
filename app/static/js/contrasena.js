// RNF5: la contrasena se ve solo mientras se mantiene presionado el boton del ojo.
// Cada campo de contrasena (login, perfil, restablecer, alta de cuentas y apartado
// Contrasenas) recibe el boton. Al soltarlo, al salir de el con el puntero, al
// cambiar de ventana o al enviar el formulario, el campo vuelve a enmascararse.
// Mientras se ve, el campo sigue sin dejar copiar ni cortar su contenido, igual
// que cuando esta enmascarado.
document.addEventListener("DOMContentLoaded", function () {
  var OJO = '<svg viewBox="0 0 24 24" aria-hidden="true">' +
    '<path d="M2 12s3.5-7 10-7 10 7 10 7-3.5 7-10 7S2 12 2 12z"/>' +
    '<circle cx="12" cy="12" r="3"/></svg>';
  var ocultadores = [];

  document.querySelectorAll('input[type="password"]').forEach(function (campo) {
    // Mover el campo le quita el foco (p. ej. el autofocus de Restablecer).
    var teniaFoco = document.activeElement === campo;
    var envoltura = document.createElement("div");
    envoltura.className = "campo-contrasena";
    campo.parentNode.insertBefore(envoltura, campo);
    envoltura.appendChild(campo);
    if (teniaFoco) campo.focus();

    var boton = document.createElement("button");
    boton.type = "button";
    boton.className = "ver-contrasena";
    boton.title = "Mantener presionado para ver la contraseña";
    boton.setAttribute("aria-label", boton.title);
    boton.innerHTML = OJO;
    envoltura.appendChild(boton);

    function mostrar() {
      campo.type = "text";
      boton.classList.add("viendo");
    }
    function ocultar() {
      campo.type = "password";
      boton.classList.remove("viendo");
    }
    ocultadores.push(ocultar);

    // Raton, dedo o lapiz: solo mientras el boton sigue presionado.
    boton.addEventListener("pointerdown", function (evento) {
      if (evento.button === 0) mostrar();
    });
    ["pointerup", "pointerleave", "pointercancel"].forEach(function (tipo) {
      boton.addEventListener(tipo, ocultar);
    });
    // Que el clic no le quite el foco al campo: se sigue escribiendo donde iba.
    boton.addEventListener("mousedown", function (evento) { evento.preventDefault(); });
    // Sostener el dedo abre el menu contextual en algunos moviles.
    boton.addEventListener("contextmenu", function (evento) { evento.preventDefault(); });

    // Teclado: Espacio o Enter sostenidos sobre el boton.
    boton.addEventListener("keydown", function (evento) {
      if (evento.key === " " || evento.key === "Enter") {
        evento.preventDefault();
        mostrar();
      }
    });
    boton.addEventListener("keyup", ocultar);
    boton.addEventListener("blur", ocultar);

    // El valor no sale del campo aunque se este viendo.
    ["copy", "cut", "dragstart"].forEach(function (tipo) {
      campo.addEventListener(tipo, function (evento) { evento.preventDefault(); });
    });

    // Los gestores de contrasenas del navegador la reconocen por su tipo al enviar.
    if (campo.form) campo.form.addEventListener("submit", ocultar);
  });

  // Cambiar de ventana o de pestana con el boton presionado no la deja a la vista.
  function ocultarTodas() {
    ocultadores.forEach(function (ocultar) { ocultar(); });
  }
  window.addEventListener("blur", ocultarTodas);
  document.addEventListener("visibilitychange", ocultarTodas);
});
