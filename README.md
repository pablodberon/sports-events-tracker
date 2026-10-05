# Agenda Deportiva — setup

Base de Airtable: https://airtable.com/appqesyHMwFB4XOv0

## Que hace esto

- **Airtable** guarda las competencias, equipos y eventos (`Sports`, `Competitions`, `Teams`, `Events`).
- **GitHub Actions** corre `update_events.py` una vez por dia: lee las competencias activas, busca los proximos partidos/carreras en ESPN o TheSportsDB segun corresponda, y los carga/actualiza en `Events`. Despues corre `generate_dashboard.py`, que arma `docs/index.html` con los proximos eventos.
- **GitHub Pages** publica `docs/index.html` como una pagina web que podes abrir cuando quieras.
- **Automatizaciones de Airtable** (ya creadas, estan apagadas por default) mandan los mails:
  - *Aviso 10-15 dias*: corre todos los dias a las 08:00 (Argentina), busca eventos destacados que caen entre 10 y 15 dias desde hoy y todavia no fueron avisados, te manda un mail a pablo.d.beron@disney.com y los marca como notificados.
  - *Agenda mensual*: corre el ultimo dia de cada mes, arma la agenda de eventos destacados del mes siguiente agrupada por competencia y te la manda por mail.

## 1. Crear el repo en GitHub

1. Entra a github.com y cre a un repositorio nuevo (puede ser privado).
2. Subi estos archivos manteniendo la estructura de carpetas:
   - `update_events.py`
   - `generate_dashboard.py`
   - `requirements.txt`
   - `.github/workflows/update.yml`
   - (podes borrar el `update.yml` suelto en la raiz, es un duplicado — el que importa es el de `.github/workflows/`)

## 2. Configurar los secrets

En el repo: **Settings > Secrets and variables > Actions > New repository secret**, cargar:

| Secret | Valor |
|---|---|
| `AIRTABLE_API_KEY` | Un Personal Access Token creado en https://airtable.com/create/tokens con scopes `data.records:read`, `data.records:write`, `schema.bases:read`, con acceso a esta base. |
| `AIRTABLE_BASE_ID` | `appqesyHMwFB4XOv0` |
| `SPORTSDB_KEY` | Opcional. Tu key gratuita de https://www.thesportsdb.com/api.php. Si no la cargas, se usa la key publica de pruebas `"3"` (mas limitada). |

## 3. Activar GitHub Pages

**Settings > Pages > Source: Deploy from a branch > Branch: `main`, carpeta `/docs`.**

Despues de la primera corrida del workflow vas a tener el dashboard en `https://<tu-usuario>.github.io/<repo>/`.

## 4. Primera corrida manual

Anda a la pestaña **Actions**, elegi "Actualizar agenda deportiva" y tocá **Run workflow** para poblar la base ya mismo (si no, se ejecuta solo una vez al dia por el cron).

## 5. Activar las automatizaciones de email en Airtable

Las automatizaciones ya estan creadas pero **apagadas** (quedan asi por seguridad hasta que las revises):

- Aviso 10-15 dias: https://airtable.com/appqesyHMwFB4XOv0/wflvHYg97REz7u4S6
- Agenda mensual: https://airtable.com/appqesyHMwFB4XOv0/wflTeykK87GRVlB1S

Abri cada link, revisa el trigger y las acciones, y tocá **"Turn on"** arriba a la derecha.

⚠️ Para la agenda mensual, el trigger esta configurado para disparar "el ultimo dia del mes" usando una opcion de Airtable que no pude probar en vivo (`fromEndOfMonth`). Antes de activarla, abrila y confirmá en el panel del trigger que efectivamente dice "last day of the month" — si no, cambialo ahi mismo (es un selector visual, un par de clicks).

Si en algun momento te empieza a llegar el mail de "10-15 dias" incluso en dias sin nada destacado y te molesta, podes agregarle un paso **"Only continue if..."** (condicion: `count` de la automatizacion es mayor a 0) antes del paso de enviar mail — es un boton que aparece al abrir la automatizacion en el editor.

## Como ampliarlo (sin tocar codigo)

- **Agregar un equipo a seguir**: fila nueva en `Teams`, tildar `Watched`. Sus partidos van a contar como destacados automaticamente (si el nombre del equipo en ESPN/TheSportsDB coincide razonablemente con el `Nombre` que cargues; si no coincide, completa `API Team Code` con el nombre exacto que usa la API).
- **Agregar una competencia/liga nueva**: fila nueva en `Competitions`:
  - `Fuente API`: `ESPN` o `TheSportsDB`.
  - Para ESPN: `API Sport Path` es la familia (`soccer`, `basketball`, `football`, `hockey`, `baseball`, `racing`...) y `API League Code` el codigo de liga (ej. `uefa.champions`, `nba`, `nfl`, `mens-college-basketball`). Podes deducirlos mirando la URL de `https://www.espn.com/<deporte>/scoreboard` o buscando "espn hidden api <liga>".
  - Para TheSportsDB: `API League Code` es el ID numerico de la liga (buscalo en thesportsdb.com/api.php o con el endpoint `search_all_leagues.php`).
  - `Activa` = true, y opcionalmente `Default Importance` si queres que sus eventos nazcan marcados como destacados (por ejemplo "Gran Premio" para automovilismo, "Relevancia Continental" para copas internacionales).
  - No hace falta tocar el script: la proxima corrida del workflow la toma sola.
- **Marcar un clasico/evento puntual como destacado a mano**: entra al evento en `Events` y cambia `Importance`. El script nunca pisa ese campo en eventos que ya existen, asi que tu edicion queda.

## Limitaciones a tener en cuenta

- ESPN no tiene una API publica oficial — es la que usa espn.com internamente. Funciona bien hoy pero podria cambiar sin aviso.
- El MotoGP quedo cargado con un League Code de ejemplo (`motogp`) en TheSportsDB: confirma el ID real en thesportsdb.com/api.php antes de que dependas de el (dejé una nota en el campo `Notas` de esa fila).
- El emparejamiento de nombres de equipos (ESPN/TheSportsDB vs. tu tabla `Teams`) es por texto; si un equipo nunca aparece como destacado por "watched", probablemente el nombre no matchea — completa `API Team Code` con el nombre tal cual lo devuelve la API.
