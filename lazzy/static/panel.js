// Ayudas «?»: se abren al pasar el mouse, al enfocar o al tocar, y se ubican dentro de la pantalla.
(function () {
  function colocar(b) {
    var t = b.querySelector('.tip-t'), r = b.getBoundingClientRect();
    t.style.left = '0px'; t.style.top = '0px';
    var w = t.offsetWidth, h = t.offsetHeight;
    var x = Math.min(Math.max(12, r.left + r.width / 2 - w / 2), window.innerWidth - w - 12);
    var y = r.bottom + 8;
    if (y + h > window.innerHeight - 8) y = Math.max(8, r.top - h - 8);
    t.style.left = x + 'px'; t.style.top = y + 'px';
  }
  function cerrar() { document.querySelectorAll('.tip.abierto').forEach(function (b) { b.classList.remove('abierto'); }); }
  function abrir(b) { cerrar(); b.classList.add('abierto'); colocar(b); }
  document.addEventListener('mouseover', function (e) { var b = e.target.closest('.tip'); if (b) abrir(b); });
  document.addEventListener('mouseout', function (e) { var b = e.target.closest('.tip'); if (b && !b.contains(e.relatedTarget)) b.classList.remove('abierto'); });
  document.addEventListener('focusin', function (e) { var b = e.target.closest('.tip'); if (b) abrir(b); });
  document.addEventListener('focusout', function (e) { var b = e.target.closest('.tip'); if (b) b.classList.remove('abierto'); });
  document.addEventListener('click', function (e) { var b = e.target.closest('.tip'); if (b) { e.preventDefault(); abrir(b); } else cerrar(); });
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape') cerrar(); });
  window.addEventListener('scroll', cerrar, true);
})();

// Estilo común de los gráficos.
window.LZ = {
  azul: '#2f6f9f', oro: '#b8860b', tinta: '#203548', tinta2: '#51636f', linea: '#eadfc4', papel: '#fffcf5',
  clp: function (v) { return (v < 0 ? '-' : '') + '$' + Math.abs(Math.round(v)).toLocaleString('es-CL'); },
  dosLineas: function (v) { var l = this.getLabelForValue(v); return (typeof l === 'string' && l.indexOf(' ') > 0) ? l.split(' ') : l; },
  corto: function (v) { var a = Math.abs(v); if (!a) return '$0'; return (v < 0 ? '-' : '') + '$' + (a >= 1e6 ? (a / 1e6).toLocaleString('es-CL', { maximumFractionDigits: 1 }) + ' M' : Math.round(a / 1000) + ' mil'); },
  base: function () {
    if (!window.Chart) return;
    Chart.defaults.font.family = '"DM Sans", system-ui, sans-serif';
    Chart.defaults.font.size = 13;
    Chart.defaults.color = this.tinta2;
    Chart.defaults.maintainAspectRatio = false;
    Chart.defaults.plugins.legend.position = 'top';
    Chart.defaults.plugins.legend.align = 'start';
    Chart.defaults.plugins.legend.labels.boxWidth = 12;
    Chart.defaults.plugins.legend.labels.boxHeight = 12;
    Chart.defaults.plugins.tooltip.backgroundColor = this.tinta;
    Chart.defaults.plugins.tooltip.padding = 10;
    Chart.defaults.plugins.tooltip.titleFont = { weight: '700' };
    Chart.defaults.scale.grid.color = this.linea;
    Chart.defaults.scale.border = { display: false };
  }
};
