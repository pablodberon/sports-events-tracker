"""
Genera docs/index.html: un dashboard estatico con los proximos eventos
leidos directamente de Airtable. Pensado para publicarse con GitHub Pages
(Settings > Pages > Source: Deploy from branch > /docs).

Variables de entorno requeridas:
  AIRTABLE_API_KEY
  AIRTABLE_BASE_ID  (default: appqesyHMwFB4XOv0)
"""

import os
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import requests

AR_TZ = ZoneInfo("America/Argentina/Buenos_Aires")

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
        if d < now:
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

    def row_html(e):
        cls = "featured" if e["featured"] else ""
        badge = "&#9733; destacado" if e["featured"] else ""
        return (
            f"<tr class=\"{cls}\">"
            f"<td>{e['date'].strftime('%Y-%m-%d %H:%M')} ART</td>"
            f"<td>{esc(e['title'])}</td>"
            f"<td>{esc(e['competition'])}</td>"
            f"<td>{esc(e['round'])}</td>"
            f"<td>{esc(e['importance'])}</td>"
            f"<td class=\"badge\">{badge}</td>"
            "</tr>"
        )

    html_rows = "\n".join(row_html(e) for e in rows[:400])
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
</style>
</head>
<body>
<div class="wrap">
  <h1>Agenda deportiva</h1>
  <div class="meta">Actualizado {updated} &middot; {len(rows)} eventos proximos &middot; {featured_count} destacados</div>
  <table>
    <thead><tr><th>Fecha (Argentina)</th><th>Evento</th><th>Competencia</th><th>Instancia</th><th>Importancia</th><th></th></tr></thead>
    <tbody>
      {html_rows}
    </tbody>
  </table>
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
