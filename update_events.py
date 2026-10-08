"""
Actualiza la tabla Events de Airtable con los proximos partidos/carreras
de las competencias activas. Segun lo configurado en Competitions, cada
competencia se trae de:
  - NHL Oficial / MLB Oficial: API propia de NHL.com / MLB.com (confiables).
  - F1 Oficial: se scrapea la pagina oficial de calendario de formula1.com.
  - ESPN: API publica no oficial de espn.com (fallback para el resto:
    ligas europeas, CONMEBOL, NBA, NCAA, etc. que bloquean o son muy
    pesadas de scrapear desde su sitio oficial).
  - TheSportsDB: fallback generico para lo que no entra en lo anterior.

Variables de entorno requeridas:
  AIRTABLE_API_KEY   Personal Access Token de Airtable
  AIRTABLE_BASE_ID   ID de la base (default: appqesyHMwFB4XOv0)
  SPORTSDB_KEY       Key de TheSportsDB (default: "3", key publica de pruebas)
  DAYS_AHEAD         Dias hacia adelante a buscar (default: 60)
"""

import base64
import json
import os
import re
import time
from datetime import datetime, timedelta

import requests

BROWSER_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
    )
}

AIRTABLE_API_KEY = os.environ["AIRTABLE_API_KEY"]
AIRTABLE_BASE_ID = os.environ.get("AIRTABLE_BASE_ID", "appqesyHMwFB4XOv0")
SPORTSDB_KEY = os.environ.get("SPORTSDB_KEY", "3")
DAYS_AHEAD = int(os.environ.get("DAYS_AHEAD", "60"))

AIRTABLE_API = f"https://api.airtable.com/v0/{AIRTABLE_BASE_ID}"
HEADERS = {"Authorization": f"Bearer {AIRTABLE_API_KEY}", "Content-Type": "application/json"}

TABLE_COMPETITIONS = "Competitions"
TABLE_TEAMS = "Teams"
TABLE_EVENTS = "Events"

ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"


# ---------------------------------------------------------------------------
# Helpers de Airtable
# ---------------------------------------------------------------------------

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


def airtable_write(table, records, method):
    url = f"{AIRTABLE_API}/{table}"
    out = []
    for i in range(0, len(records), 10):
        batch = records[i : i + 10]
        resp = method(url, headers=HEADERS, json={"records": batch, "typecast": True}, timeout=30)
        if not resp.ok:
            print(f"[ERROR] {resp.status_code} {resp.text[:500]}")
            resp.raise_for_status()
        out.extend(resp.json()["records"])
        time.sleep(0.2)
    return out


def airtable_create(table, records):
    return airtable_write(table, records, requests.post)


def airtable_update(table, records):
    return airtable_write(table, records, requests.patch)


# ---------------------------------------------------------------------------
# Lectura de configuracion (Competitions / Teams)
# ---------------------------------------------------------------------------

def get_active_competitions():
    recs = airtable_list(TABLE_COMPETITIONS, {"filterByFormula": "{Activa}=1"})
    comps = []
    for r in recs:
        f = r["fields"]
        source = f.get("Fuente API")
        if not source:
            continue
        if source in ("ESPN", "TheSportsDB") and not f.get("API League Code"):
            continue
        comps.append(
            {
                "id": r["id"],
                "name": f.get("Nombre", ""),
                "source": f.get("Fuente API"),
                "sport_path": f.get("API Sport Path", ""),
                "league_code": f.get("API League Code", ""),
                "default_importance": f.get("Default Importance") or "Normal",
            }
        )
    return comps


def get_teams():
    recs = airtable_list(TABLE_TEAMS)
    teams = []
    for r in recs:
        f = r["fields"]
        name = f.get("Nombre", "")
        if not name:
            continue
        teams.append(
            {
                "id": r["id"],
                "name": name,
                "watched": bool(f.get("Watched")),
                "api_code": (f.get("API Team Code") or "").strip(),
            }
        )
    return teams


def match_team(name, teams):
    if not name:
        return None
    name_l = name.lower().strip()
    for t in teams:
        if t["api_code"] and t["api_code"].lower() == name_l:
            return t
    for t in teams:
        if t["name"].lower().strip() == name_l:
            return t
    for t in teams:
        tn = t["name"].lower()
        if tn and (tn in name_l or name_l in tn):
            return t
    return None


# ---------------------------------------------------------------------------
# Heuristica de importancia (no pisa valores ya cargados manualmente)
# ---------------------------------------------------------------------------

def guess_importance(default_importance, title, round_name):
    text = f"{title or ''} {round_name or ''}".lower()
    if any(k in text for k in ["semifinal", "cuartos de final", "quarterfinal", "wild card", "knockout", "eliminat", "repechaje"]):
        return "Playoff"
    if "final" in text and "semifinal" not in text:
        return "Final"
    if "playoff" in text or "postemporada" in text:
        return "Playoff"
    return default_importance or "Normal"


STATUS_MAP = {
    "STATUS_SCHEDULED": "Programado",
    "STATUS_FINAL": "Jugado",
    "STATUS_POSTPONED": "Pospuesto",
    "STATUS_CANCELED": "Cancelado",
    "STATUS_CANCELLED": "Cancelado",
}


# ---------------------------------------------------------------------------
# ESPN
# ---------------------------------------------------------------------------

def fetch_espn_events(sport_path, league_code, days_ahead):
    today = datetime.utcnow().date()
    end = today + timedelta(days=days_ahead)
    url = f"{ESPN_BASE}/{sport_path}/{league_code}/scoreboard"
    events = []
    try:
        resp = requests.get(
            url,
            params={"dates": f"{today:%Y%m%d}-{end:%Y%m%d}", "limit": 1000},
            timeout=20,
        )
        resp.raise_for_status()
        events = resp.json().get("events", [])
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] ESPN (rango) fallo para {sport_path}/{league_code}: {e}")

    if not events:
        # La API de ESPN no soporta rango de fechas en la mayoria de los deportes
        # (devuelve 400), asi que recorremos dia por dia todo el rango pedido.
        # Si el primer dia tira 400, es que sport_path/league_code esta mal
        # (no existe esa combinacion en ESPN) -> cortamos en vez de insistir
        # 60 veces con el mismo error.
        consecutive_400 = 0
        for i in range(days_ahead):
            d = today + timedelta(days=i)
            try:
                resp = requests.get(url, params={"dates": f"{d:%Y%m%d}"}, timeout=20)
                resp.raise_for_status()
                events.extend(resp.json().get("events", []))
                consecutive_400 = 0
            except requests.exceptions.HTTPError as e:
                if e.response is not None and e.response.status_code == 400:
                    consecutive_400 += 1
                    if consecutive_400 >= 2:
                        print(
                            f"[WARN] ESPN: {sport_path}/{league_code} devuelve 400 de forma "
                            "sistematica, el codigo de liga/deporte probablemente esta mal. "
                            "Cortando esta competencia."
                        )
                        break
                else:
                    print(f"[WARN] ESPN (dia {d}) fallo: {e}")
            except Exception as e:  # noqa: BLE001
                print(f"[WARN] ESPN (dia {d}) fallo: {e}")
            time.sleep(0.15)
    return events


def parse_espn_event(ev, teams):
    comp0 = (ev.get("competitions") or [{}])[0]
    status_name = comp0.get("status", {}).get("type", {}).get("name", "")
    round_name = ""
    notes = comp0.get("notes") or []
    if notes:
        round_name = notes[0].get("headline", "") or ""
    if not round_name:
        week = ev.get("week", {}).get("number")
        if week:
            round_name = f"Semana {week}"
    links = ev.get("links") or []
    source_url = links[0].get("href", "") if links else ""

    participant_ids = []
    for c in comp0.get("competitors") or []:
        team = c.get("team", {})
        nm = team.get("displayName") or team.get("shortDisplayName") or team.get("name")
        matched = match_team(nm, teams)
        if matched:
            participant_ids.append(matched["id"])

    return {
        "external_id": f"espn-{ev.get('id')}",
        "title": ev.get("name") or ev.get("shortName") or "",
        "date": ev.get("date"),
        "round": round_name,
        "source_url": source_url,
        "status": STATUS_MAP.get(status_name, "Programado"),
        "participant_ids": participant_ids,
    }


# ---------------------------------------------------------------------------
# TheSportsDB
# ---------------------------------------------------------------------------

def fetch_sportsdb_events(league_code):
    url = f"https://www.thesportsdb.com/api/v1/json/{SPORTSDB_KEY}/eventsnextleague.php"
    try:
        resp = requests.get(url, params={"id": league_code}, timeout=20)
        resp.raise_for_status()
        return resp.json().get("events") or []
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] TheSportsDB fallo para liga {league_code}: {e}")
        return []


def parse_sportsdb_event(ev, teams):
    date_str = ev.get("dateEvent")
    time_str = ev.get("strTime") or "00:00:00"
    date_iso = None
    if date_str:
        try:
            date_iso = datetime.fromisoformat(f"{date_str}T{time_str}").isoformat()
        except ValueError:
            date_iso = f"{date_str}T00:00:00"

    participant_ids = []
    for nm in [ev.get("strHomeTeam"), ev.get("strAwayTeam")]:
        matched = match_team(nm, teams)
        if matched:
            participant_ids.append(matched["id"])

    return {
        "external_id": f"sportsdb-{ev.get('idEvent')}",
        "title": ev.get("strEvent") or "",
        "date": date_iso,
        "round": ev.get("strRound") or "",
        "source_url": ev.get("strVideo") or "",
        "status": "Programado",
        "participant_ids": participant_ids,
    }


# ---------------------------------------------------------------------------
# NHL (API oficial de NHL.com)
# ---------------------------------------------------------------------------

NHL_STATUS_MAP = {
    "FUT": "Programado",
    "PRE": "Programado",
    "LIVE": "Programado",
    "CRIT": "Programado",
    "OFF": "Jugado",
    "FINAL": "Jugado",
    "PPD": "Pospuesto",
    "CNCL": "Cancelado",
}


def fetch_nhl_events(days_ahead):
    events = []
    today = datetime.utcnow().date()
    seen_dates = set()
    d = today
    end = today + timedelta(days=days_ahead)
    while d <= end:
        try:
            resp = requests.get(f"https://api-web.nhle.com/v1/schedule/{d:%Y-%m-%d}", timeout=20)
            resp.raise_for_status()
            data = resp.json()
            for gw in data.get("gameWeek", []):
                try:
                    gw_date = datetime.strptime(gw["date"], "%Y-%m-%d").date()
                except (KeyError, ValueError):
                    continue
                if gw_date in seen_dates:
                    continue
                seen_dates.add(gw_date)
                events.extend(gw.get("games", []))
        except Exception as e:  # noqa: BLE001
            print(f"[WARN] NHL oficial fallo para semana de {d}: {e}")
        d += timedelta(days=7)
        time.sleep(0.2)
    return events


def parse_nhl_event(ev, teams):
    away = ev.get("awayTeam", {})
    home = ev.get("homeTeam", {})

    def team_name(t):
        place = (t.get("placeName") or {}).get("default", "")
        common = (t.get("commonName") or {}).get("default", "")
        return f"{place} {common}".strip()

    away_name, home_name = team_name(away), team_name(home)
    participant_ids = [t["id"] for t in (match_team(away_name, teams), match_team(home_name, teams)) if t]

    round_name = "Playoffs" if ev.get("gameType") == 3 else ""
    return {
        "external_id": f"nhl-{ev.get('id')}",
        "title": f"{away_name} at {home_name}".strip(),
        "date": ev.get("startTimeUTC"),
        "round": round_name,
        "source_url": f"https://www.nhl.com/gamecenter/{ev.get('id')}" if ev.get("id") else "",
        "status": NHL_STATUS_MAP.get(ev.get("gameState", ""), "Programado"),
        "participant_ids": participant_ids,
    }


# ---------------------------------------------------------------------------
# MLB (API oficial de MLB Advanced Media)
# ---------------------------------------------------------------------------

MLB_STATUS_MAP = {
    "Scheduled": "Programado",
    "Pre-Game": "Programado",
    "Warmup": "Programado",
    "In Progress": "Programado",
    "Final": "Jugado",
    "Game Over": "Jugado",
    "Postponed": "Pospuesto",
    "Suspended": "Pospuesto",
    "Cancelled": "Cancelado",
}


def fetch_mlb_events(days_ahead):
    today = datetime.utcnow().date()
    end = today + timedelta(days=days_ahead)
    events = []
    try:
        resp = requests.get(
            "https://statsapi.mlb.com/api/v1/schedule",
            params={"sportId": 1, "startDate": f"{today:%Y-%m-%d}", "endDate": f"{end:%Y-%m-%d}"},
            timeout=20,
        )
        resp.raise_for_status()
        for day in resp.json().get("dates", []):
            events.extend(day.get("games", []))
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] MLB oficial fallo: {e}")
    return events


def parse_mlb_event(ev, teams):
    teams_obj = ev.get("teams", {})
    away = teams_obj.get("away", {}).get("team", {}).get("name", "")
    home = teams_obj.get("home", {}).get("team", {}).get("name", "")
    participant_ids = [t["id"] for t in (match_team(away, teams), match_team(home, teams)) if t]
    status = ev.get("status", {}).get("detailedState", "")
    return {
        "external_id": f"mlb-{ev.get('gamePk')}",
        "title": f"{away} at {home}".strip(),
        "date": ev.get("gameDate"),
        "round": ev.get("seriesDescription") or "",
        "source_url": f"https://www.mlb.com/gameday/{ev.get('gamePk')}" if ev.get("gamePk") else "",
        "status": MLB_STATUS_MAP.get(status, "Programado"),
        "participant_ids": participant_ids,
    }


# ---------------------------------------------------------------------------
# F1 (se scrapea la pagina oficial de calendario de formula1.com)
# ---------------------------------------------------------------------------

F1_CONTEXT_RE = re.compile(r'data-f1rd-a7s-context="([^"]+)"')
F1_DATE_RE = re.compile(r">(\d{1,2}(?:\s*-\s*\d{1,2})?\s+[A-Za-z]{3,9})<")
F1_ROUND_RE = re.compile(r">ROUND\s+(\d+)<", re.I)
F1_MONTHS = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}


def _parse_f1_date(date_text, year):
    if not date_text:
        return None
    last_part = date_text.replace("–", "-").split("-")[-1].strip()
    bits = last_part.split()
    if len(bits) != 2:
        return None
    day_str, month_str = bits
    month = F1_MONTHS.get(month_str.strip()[:3].lower())
    if not month:
        return None
    try:
        day = int(day_str)
    except ValueError:
        return None
    return f"{year:04d}-{month:02d}-{day:02d}T14:00:00Z"


def _fetch_f1_html_for_year(year):
    try:
        resp = requests.get(
            f"https://www.formula1.com/en/racing/{year}",
            headers=BROWSER_HEADERS,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.text
    except Exception as e:  # noqa: BLE001
        print(f"[WARN] F1 oficial fallo para {year}: {e}")
        return ""


def fetch_f1_events():
    today = datetime.utcnow()
    years = [today.year] if today.month < 11 else [today.year, today.year + 1]
    races = []
    for year in years:
        html = _fetch_f1_html_for_year(year)
        if not html:
            continue
        for m in F1_CONTEXT_RE.finditer(html):
            padded = m.group(1) + "=" * (-len(m.group(1)) % 4)
            try:
                payload = json.loads(base64.b64decode(padded))
            except Exception:  # noqa: BLE001
                continue
            race_name = payload.get("raceName", "")
            path = payload.get("path", "")
            if not race_name or not path.startswith("/en/racing/"):
                continue
            window = html[max(0, m.start() - 1500) : m.start()]
            date_matches = list(F1_DATE_RE.finditer(window))
            round_matches = list(F1_ROUND_RE.finditer(window))
            races.append(
                {
                    "race_name": race_name,
                    "path": path,
                    "date_text": date_matches[-1].group(1) if date_matches else None,
                    "round": f"Ronda {round_matches[-1].group(1)}" if round_matches else "",
                    "year": year,
                }
            )
    return races


def parse_f1_event(ev):
    date_iso = _parse_f1_date(ev.get("date_text"), ev.get("year"))
    if not date_iso:
        return None
    slug = ev.get("path", "").rstrip("/").split("/")[-1]
    return {
        "external_id": f"f1-{ev.get('year')}-{slug}",
        "title": (ev.get("race_name") or "").title(),
        "date": date_iso,
        "round": ev.get("round", ""),
        "source_url": f"https://www.formula1.com{ev.get('path')}",
        "status": "Programado",
        "participant_ids": [],
    }


# ---------------------------------------------------------------------------
# Upsert
# ---------------------------------------------------------------------------

def get_existing_events():
    recs = airtable_list(TABLE_EVENTS)
    by_external = {}
    for r in recs:
        ext = r["fields"].get("External ID")
        if ext:
            by_external[ext] = r
    return by_external


def run():
    comps = get_active_competitions()
    teams = get_teams()
    existing = get_existing_events()
    print(f"Competencias activas: {len(comps)} | Equipos cargados: {len(teams)} | Eventos existentes: {len(existing)}")

    # Claves por external_id para garantizar que cada evento aparezca una sola
    # vez en el batch final, aunque la fuente lo haya devuelto duplicado (pasa,
    # por ejemplo, con F1 oficial que repite la proxima carrera en un destacado
    # ademas de listarla en el calendario completo). Si no dedupe-amos, Airtable
    # rechaza el request entero con 422 "cannot update the same record twice".
    creates_by_external_id = {}
    updates_by_record_id = {}

    for comp in comps:
        print(f"-> {comp['name']} ({comp['source']})")
        if comp["source"] == "ESPN":
            raw = fetch_espn_events(comp["sport_path"], comp["league_code"], DAYS_AHEAD)
            parsed = [parse_espn_event(ev, teams) for ev in raw]
        elif comp["source"] == "TheSportsDB":
            raw = fetch_sportsdb_events(comp["league_code"])
            parsed = [parse_sportsdb_event(ev, teams) for ev in raw]
        elif comp["source"] == "NHL Oficial":
            raw = fetch_nhl_events(DAYS_AHEAD)
            parsed = [parse_nhl_event(ev, teams) for ev in raw]
        elif comp["source"] == "MLB Oficial":
            raw = fetch_mlb_events(DAYS_AHEAD)
            parsed = [parse_mlb_event(ev, teams) for ev in raw]
        elif comp["source"] == "F1 Oficial":
            raw = fetch_f1_events()
            parsed = [p for p in (parse_f1_event(ev) for ev in raw) if p]
        else:
            continue

        for pe in parsed:
            if not pe["external_id"] or not pe["date"]:
                continue
            common_fields = {
                "Title": pe["title"] or comp["name"],
                "Date": pe["date"],
                "Round": pe["round"],
                "Competition": [comp["id"]],
                "Participants": pe["participant_ids"],
                "Source URL": pe["source_url"] or None,
                "Status": pe["status"],
            }
            existing_rec = existing.get(pe["external_id"])
            if existing_rec:
                # No tocamos Importance ni Notified 15 dias: pueden haber sido editados a mano.
                updates_by_record_id[existing_rec["id"]] = {"id": existing_rec["id"], "fields": common_fields}
            else:
                fields_new = dict(common_fields)
                fields_new["External ID"] = pe["external_id"]
                fields_new["Importance"] = guess_importance(comp["default_importance"], pe["title"], pe["round"])
                fields_new["Notified 15 dias"] = False
                creates_by_external_id[pe["external_id"]] = {"fields": fields_new}
        time.sleep(0.3)

    to_create = list(creates_by_external_id.values())
    to_update = list(updates_by_record_id.values())

    if to_create:
        print(f"Creando {len(to_create)} eventos nuevos...")
        airtable_create(TABLE_EVENTS, to_create)
    if to_update:
        print(f"Actualizando {len(to_update)} eventos existentes...")
        airtable_update(TABLE_EVENTS, to_update)

    print("Listo.")


if __name__ == "__main__":
    run()
