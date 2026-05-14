# FlotasMultimarca AS

Sistema de gestion de flotas de mercadeo para Astara.

Estado actual:
- FastAPI + SQLite + SQLAlchemy + Jinja2
- autenticacion basica con sesiones
- roles: admin, fleet_supervisor, coordinator, operator, viewer
- scopes por equipo
- inventario, prestamos, devoluciones, evidencia e historial
- transferencias de vehiculos entre equipos

## Inicio rapido

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
python -m app.seed
uvicorn app.main:app --reload
```

Abre:

```text
http://127.0.0.1:8000
```

## Entradas utiles

- `README.md`: punto de entrada
- `PROJECT_BRIEF.md`: objetivo vigente del producto
- `ARCHITECTURE.md`: arquitectura actual
- `DECISIONS.md`: decisiones ya cerradas
- `TASK.md`: roadmap incremental

## Datos demo

La semilla crea equipos, usuarios demo, vehiculos y prestamos distribuidos por equipo para probar scopes y transferencias.

```powershell
python -m app.seed
```

Usuarios demo:
- `admin` / `demo123`
- `fleet_supervisor` / `demo123`
- `coordinator` / `demo123`
- `operator` / `demo123`
- `viewer` / `demo123`

## Flujo por rol

- `admin` y `fleet_supervisor`: vista global y gestion administrativa
- `coordinator`: vista acotada a su equipo
- `operator`: vista operativa en `/operator/vehicles`
- `viewer`: solo lectura dentro de su equipo

## Docker

```bash
docker compose up -d --build
```

El contenedor usa SQLite persistente en `/app/data/fleet.db` y uploads persistentes en `/app/uploads`.
