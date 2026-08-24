"""Menú lateral de navegación compartido por todas las páginas del backend.

Las páginas HTML del backend (diagnóstico, calibración de visión, calibración
del robot) son autocontenidas; ``with_nav()`` les inyecta el mismo menú fijo a
la izquierda para poder saltar entre ellas con el mouse sin escribir URLs.

La UI de exposición (React, ``/ui``) replica este menú en ``SideNav.jsx`` —
misma paleta y mismos destinos, pero arranca plegado para no molestar al
público durante la exposición.
"""

from __future__ import annotations

# (href, icono, etiqueta, descripción corta)
NAV_ITEMS: tuple[tuple[str, str, str, str], ...] = (
    ("/ui", "\u265e", "Partida", "UI de exposición"),
    ("/calibracion", "\U0001f4f7", "Visión", "Esquinas y entrenamiento"),
    ("/calibration", "\U0001f9be", "Robot", "Teach de puntos"),
    ("/", "\u2b1b", "Sensores", "Diagnóstico del tablero"),
    ("/docs", "\u2699", "API", "Documentación REST"),
)

NAV_STYLE = """
<style id="sidenav-style">
  :root { --nav-w: 208px; }
  html.nav-collapsed { --nav-w: 54px; }
  body { margin-left: var(--nav-w); }
  #sidenav {
    position: fixed; top: 0; left: 0; bottom: 0; width: var(--nav-w);
    background: #15151b; border-right: 1px solid #2c2c36; z-index: 900;
    display: flex; flex-direction: column; overflow: hidden;
    font-family: system-ui, sans-serif;
  }
  #sidenav .nav-head {
    display: flex; align-items: center; gap: .5rem; padding: .8rem .6rem;
    border-bottom: 1px solid #2c2c36; color: #ececf1; white-space: nowrap;
  }
  #sidenav .nav-head .nav-title { font-weight: 600; font-size: .95rem; }
  #sidenav .nav-toggle {
    background: transparent; border: 0; color: #9a9aa8; cursor: pointer;
    font-size: 1.15rem; line-height: 1; padding: .35rem .45rem; border-radius: 6px;
    margin: 0;
  }
  #sidenav .nav-toggle:hover { background: #24242e; color: #ececf1; }
  #sidenav nav { display: flex; flex-direction: column; padding: .5rem .4rem; gap: .15rem; }
  #sidenav a {
    display: flex; align-items: center; gap: .65rem; padding: .55rem .5rem;
    border-radius: 7px; color: #c9c9d4; text-decoration: none; white-space: nowrap;
    border-left: 3px solid transparent;
  }
  #sidenav a:hover { background: #24242e; color: #fff; }
  #sidenav a.active { background: #23334a; color: #fff; border-left-color: #4f9cf9; }
  #sidenav .nav-icon {
    flex: 0 0 1.5rem; text-align: center; font-size: 1.1rem; line-height: 1;
  }
  #sidenav .nav-label { display: flex; flex-direction: column; min-width: 0; }
  #sidenav .nav-label b { font-weight: 600; font-size: .92rem; }
  #sidenav .nav-label span { font-size: .72rem; color: #8b8b99; }
  html.nav-collapsed #sidenav .nav-label,
  html.nav-collapsed #sidenav .nav-title { display: none; }
  html.nav-collapsed #sidenav .nav-head { justify-content: center; padding: .8rem 0; }
  @media print { #sidenav { display: none; } body { margin-left: 0; } }
</style>
<script>
  // Antes del render para que el menú no "salte" al cargar.
  if (localStorage.getItem('chessNavCollapsed') === '1')
    document.documentElement.classList.add('nav-collapsed');
</script>
"""

_NAV_SCRIPT = """
<script>
  document.getElementById('nav-toggle').onclick = () => {
    const collapsed = document.documentElement.classList.toggle('nav-collapsed');
    localStorage.setItem('chessNavCollapsed', collapsed ? '1' : '0');
  };
</script>
"""


def nav_html(active: str) -> str:
    """Markup del menú, con el item ``active`` (href) resaltado."""
    links = []
    for href, icon, label, hint in NAV_ITEMS:
        cls = " class=\"active\"" if href == active else ""
        links.append(
            f'<a href="{href}"{cls} title="{label} — {hint}">'
            f'<span class="nav-icon">{icon}</span>'
            f'<span class="nav-label"><b>{label}</b><span>{hint}</span></span></a>'
        )
    return (
        '<aside id="sidenav">'
        '<div class="nav-head">'
        '<button id="nav-toggle" class="nav-toggle" title="Plegar/desplegar menú">\u2630</button>'
        '<span class="nav-title">Robot Ajedrecista</span>'
        "</div><nav>" + "".join(links) + "</nav></aside>" + _NAV_SCRIPT
    )


def with_nav(html: str, active: str) -> str:
    """Inyecta el menú lateral en una página HTML autocontenida."""
    if "</head>" not in html or "<body>" not in html:  # pragma: no cover - defensivo
        return html
    html = html.replace("</head>", NAV_STYLE + "</head>", 1)
    return html.replace("<body>", "<body>" + nav_html(active), 1)
