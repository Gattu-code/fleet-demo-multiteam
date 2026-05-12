# Fleet Demo

Demo interno para controlar préstamos de vehículos de marketing con FastAPI, SQLite, SQLAlchemy, Jinja2 y TailwindCSS.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
uvicorn app.main:app --reload
```

Luego abre:

```text
http://127.0.0.1:8000
```

## Estructura

```text
app/
  database.py
  main.py
  models.py
templates/
  base.html
  vehicles/
static/
  css/app.css
uploads/
  loan_photos/
```

La base SQLite local se crea automáticamente como `./data/fleet.db` al arrancar la app.

## Datos demo

```powershell
python -m app.seed
```

## Docker production

El contenedor usa:

- SQLite persistente en `/app/data/fleet.db`
- Uploads persistentes en `/app/uploads`
- Uvicorn en `0.0.0.0:8000`
- Caddy como reverse proxy para `fleet.nxtsln.cloud`

```bash
docker compose up -d --build
```

Antes de levantar en produccion, apunta el DNS de `fleet.nxtsln.cloud` al servidor donde corre Docker. Caddy gestionara HTTPS automaticamente.
