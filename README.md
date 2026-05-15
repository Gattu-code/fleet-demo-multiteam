# FlotasMultimarca AS

Sistema interno de gestion de flotas de mercadeo para Astara.

Estado actual:
- Fase 1 cerrada: autenticacion basica, roles, scopes por equipo, operador operativo y transferencias
- Fase 2A cerrada: administracion basica de categorias, equipos, usuarios y configuracion por equipo
- Fase 2B avanzada y en estabilizacion: checklist operativo, evidencia agrupada, novedades estructuradas, validaciones por fotos y limpieza UX

## Que hace hoy

- inventario de vehiculos
- prestamos y devoluciones
- evidencia fotografica y documental
- transferencias historicas entre equipos
- administracion basica de equipos, usuarios, categorias y configuracion
- vista operativa separada para `operator`
- labels operativos mas claros para entrega, devolucion y comodato
- indicadores compactos de estado comodato en prestamos

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

## Usuarios demo

- `admin` / `demo123`
- `fleet_supervisor` / `demo123`
- `coordinator` / `demo123`
- `operator` / `demo123`
- `viewer` / `demo123`

## Modulos principales

- `Dashboard`: lectura ejecutiva
- `Inventario`: vehiculos y edicion administrativa
- `Prestamos`: listado historico y control documental
- `Operador`: flujo operativo rapido y refinado
- `Administracion`: equipos, usuarios, categorias y configuracion por equipo

## Datos demo

La semilla crea equipos, usuarios, vehiculos y prestamos distribuidos para probar scopes, operaciones y transferencias.

```powershell
python -m app.seed
```

## Docker

```bash
docker compose up -d --build
```

El contenedor usa SQLite persistente en `/app/data/fleet.db` y uploads persistentes en `/app/uploads`.

## Pendientes documentados

- rediseño de la tabla `loans`
- revision integral de la UX de `operator`
- flujo separado para crear equipos
- `operator` multi-equipo
- Fase 2C con KPIs operativos

## Documentacion

- `PROJECT_BRIEF.md`: brief vigente del producto
- `ARCHITECTURE.md`: arquitectura actual
- `DECISIONS.md`: decisiones ya cerradas
- `TASK.md`: roadmap y pendientes
