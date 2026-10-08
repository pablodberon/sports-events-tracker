"""
Genera docs/index.html: un dashboard estatico con los proximos eventos
leidos directamente de Airtable. Pensado para publicarse con GitHub Pages
(Settings > Pages > Source: Deploy from branch > /docs).

Variables de entorno requeridas:
  AIRTABLE_API_KEY
  AIRTABLE_BASE_ID  (default: appqesyHMwFB4XOv0)
"""

import os
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import requests

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

# La "jornada deportiva" arranca a las 4am: un evento que empieza a la 1, 2 o 3
# de la madrugada se agrupa con el dia anterior (asi quedan juntos los partidos
# nocturnos de EEUU/Europa con el resto de esa fecha).
JORNADA_START_HOUR = 4

WEEKDAYS_ES = ["Lunes", "Martes", "Miércoles", "Jueves", "Viernes", "Sábado", "Domingo"]
MONTHS_ES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def jornada_date(dt):
    """Fecha (sin hora) de la jornada a la que pertenece dt, en horario Argentina."""
    if dt.hour < JORNADA_START_HOUR:
        return (dt - timedelta(days=1)).date()
    return dt.date()


def jornada_label(d):
    return f"{WEEKDAYS_ES[d.weekday()]} {d.day} de {MONTHS_ES[d.month - 1]}"

AIRTABLE_API_KEY = os.environ["AIRTABLE_API_KEY"]
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appqesyHMwFB4XOv0")
AIRTABLE_API = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}"}


def airtable_list(table, params=None):
    records = []
    url = f"{AIRTABLE_API}/{table}"
    params = dict(params or {})
    while True:
        resp = requests.get(url, headers=HEADERS, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        records.extend(data.get("records", []))
        offset = data.get("offset")
        if not offset:
            break
        params["offset"] = offset
    return records


def esc(s):
    return (
        str(s or "")
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def main():
    comps = airtable_list("Competitions")
    comp_name_by_id = {c["id"]: c["fields"].get("Nombre", "") for c in comps}

    events = airtable_list(
        "Events",
        {"sort[0][field]": "Date", "sort[0][direction]": "asc"},
    )

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(hours=2)  # mantenemos eventos que arrancaron hasta hace 2hs
    rows = []
    for r in events:
        f = r["fields"]
        date_str = f.get("Date")
        if not date_str:
            continue
        try:
            d = datetime.fromisoformat(date_str.replace("Z", "+00:00"))
        except ValueError:
            continue
        if d < cutoff:
            continue
        comp_ids = f.get("Competition") or []
        rows.append(
            {
                "title": f.get("Title", ""),
                "date": d.astimezone(AR_TZ),
                "competition": ", ".join(comp_name_by_id.get(cid, "") for cid in comp_ids),
                "importance": f.get("Importance", "Normal"),
                "featured": bool(f.get("Featured")),
                "round": f.get("Round", ""),
                "status": f.get("Status", ""),
            }
        )
    rows.sort(key=lambda x: x["date"])
    rows = rows[:400]

    # Agrupar por jornada (4am a 3:59am del dia siguiente), preservando el
    # orden cronologico ya aplicado arriba.
    groups = {}
    for e in rows:
        jd = jornada_date(e["date"])
        groups.setdefault(jd, []).append(e)

    def row_html(e):
        cls = "featured" if e["featured"] else ""
        badge = "&#9733; destacado" if e["featured"] else ""
        return (
            f"<tr class=\"{cls}\">"
            f"<td>{e['date'].strftime('%H:%M')}</td>"
            f"<td>{esc(e['title'])}</td>"
            f"<td>{esc(e['competition'])}</td>"
            f"<td>{esc(e['round'])}</td>"
            f"<td>{esc(e['importance'])}</td>"
            f"<td class=\"badge\">{badge}</td>"
            "</tr>"
        )

    def day_box_html(jd, evs):
        featured_in_day = sum(1 for e in evs if e["featured"])
        rows_html = "\n".join(row_html(e) for e in evs)
        return f"""
    <section class="day">
      <h2>{jornada_label(jd)}</h2>
      <div class="day-meta">{len(evs)} eventos &middot; {featured_in_day} destacados</div>
      <table>
        <thead><tr><th>Hora (ART)</th><th>Evento</th><th>Competencia</th><th>Instancia</th><th>Importancia</th><th></th></tr></thead>
        <tbody>
          {rows_html}
        </tbody>
      </table>
    </section>"""

    html_days = "\n".join(day_box_html(jd, evs) for jd, evs in groups.items())
    updated = now.astimezone(AR_TZ).strftime("%Y-%m-%d %H:%M ART")
    featured_count = sum(1 for e in rows if e["featured"])

    html = f"""<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<title>Agenda Deportiva</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root {{ color-scheme: dark; }}
  body {{ font-family: -apple-system, Segoe UI, Roboto, Helvetica, Arial, sans-serif;
          background:#0f1117; color:#e8e8ea; margin:0; padding:28px 20px 60px; }}
  h1 {{ font-size:22px; margin:0 0 4px; }}
  .meta {{ color:#9aa0ab; font-size:13px; margin-bottom:18px; }}
  table {{ width:100%; border-collapse:collapse; font-size:14px; }}
  th, td {{ text-align:left; padding:9px 10px; border-bottom:1px solid #22252d; }}
  th {{ color:#9aa0ab; font-weight:600; text-transform:uppercase; font-size:11px; letter-spacing:.04em; }}
  tr.featured {{ background:#17241a; }}
  tr.featured td.badge {{ color:#4ade80; font-weight:600; white-space:nowrap; }}
  .wrap {{ max-width:1000px; margin:0 auto; }}
  section.day {{ background:#171920; border:1px solid #262a35; border-radius:10px;
                 padding:16px 18px; margin-bottom:18px; }}
  section.day h2 {{ margin:0 0 2px; font-size:16px; text-transform:capitalize; }}
  .day-meta {{ color:#9aa0ab; font-size:12px; margin-bottom:10px; }}
</style>
</head>
<body>
<div class="wrap">
  <h1>Agenda deportiva</h1>
  <div class="meta">Actualizado {updated} &middot; {len(rows)} eventos proximos &middot; {featured_count} destacados &middot; la jornada va de 4am a 3:59am del dia siguiente (hora Argentina)</div>
  {html_days}
</div>
</body>
</html>"""

    os.makedirs("docs", exist_ok=True)
    with open("docs/index.html", "w", encoding="utf-8") as fh:
        fh.write(html)
    # Evita que GitHub Pages intente procesar la carpeta con Jekyll
    # (si no, falla buscando assets/css/style.scss que no existe).
    open("docs/.nojekyll", "w").close()
    print(f"Dashboard generado: {len(rows)} eventos, {featured_count} destacados.")


if __name__ == "__main__":
    main()
