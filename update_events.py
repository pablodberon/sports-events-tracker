"""
Actualiza la tabla Events de Airtable con los proximos partidos/carreras
de las competencias activas, usando ESPN (API publica no oficial) o
TheSportsDB segun lo configurado en la tabla Competitions.

Variables de entorno requeridas:
  AIRTABLE_API_KEY   Personal Access Token de Airtable
  AIRTABLE_BASE_ID   ID de la base (default: appqesyHMwFB4XOv0)
  SPORTSDB_KEY       Key de TheSportsDB (default: "3", key publica de pruebas)
  DAYS_AHEAD         Dias hacia adelante a buscar en ESPN (default: 60)
"""

import os
import time
from datetime import datetime, timedelta

import requests

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
        if not f.get("Fuente API") or not f.get("API League Code"):
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

    to_create, to_update = [], []

    for comp in comps:
        print(f"-> {comp['name']} ({comp['source']})")
        if comp["source"] == "ESPN":
            raw = fetch_espn_events(comp["sport_path"], comp["league_code"], DAYS_AHEAD)
            parsed = [parse_espn_event(ev, teams) for ev in raw]
        elif comp["source"] == "TheSportsDB":
            raw = fetch_sportsdb_events(comp["league_code"])
            parsed = [parse_sportsdb_event(ev, teams) for ev in raw]
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
                to_update.append({"id": existing_rec["id"], "fields": common_fields})
            else:
                fields_new = dict(common_fields)
                fields_new["External ID"] = pe["external_id"]
                fields_new["Importance"] = guess_importance(comp["default_importance"], pe["title"], pe["round"])
                fields_new["Notified 15 dias"] = False
                to_create.append({"fields": fields_new})
        time.sleep(0.3)

    if to_create:
        print(f"Creando {len(to_create)} eventos nuevos...")
        airtable_create(TABLE_EVENTS, to_create)
    if to_update:
        print(f"Actualizando {len(to_update)} eventos existentes...")
        airtable_update(TABLE_EVENTS, to_update)

    print("Listo.")


if __name__ == "__main__":
    run()
