# Arquitectura actual

## Objetivo

FlotasMultimarca AS es una aplicacion interna para controlar una flota de mercadeo con foco en operacion diaria, trazabilidad y separacion por equipos.

## Stack

- FastAPI
- SQLite
- SQLAlchemy
- Jinja2
- sesiones con `SessionMiddleware`
- Docker para despliegue local y de demo

## Capas

### Dominio

- `Team`
- `User`
- `Vehicle`
- `Loan`
- `LoanAsset`
- `VehicleTransfer`

### Flujo operativo

- `operator` trabaja en `/operator/vehicles`
- entrega, devolucion y novedades se mantienen como flujo operativo
- el detalle historico del prestamo usa `loan.team_id` como snapshot

### Flujo administrativo

- inventario
- edicion de vehiculos
- transferencias entre equipos
- visibilidad global para `admin` y `fleet_supervisor`

## Scopes

- `admin` y `fleet_supervisor` ven todo
- `coordinator` ve su equipo
- `operator` ve la experiencia operativa de su equipo
- `viewer` ve solo lectura dentro de su equipo

## Trazabilidad

- `Vehicle.team_id` representa el equipo actual del vehiculo
- `Loan.team_id` conserva el equipo historico al momento del prestamo
- `VehicleTransfer` guarda de donde a donde se movio un vehiculo

## Principios

- evolucion incremental, no rewrite
- reutilizar templates y estilos existentes
- mantener compatibilidad con SQLite
- no perder historial operativo
- separar operacion de administracion

## Pendiente

- datos maestros y configuracion administrativa de Fase 2
- catalogos formales
- mejoras de gobierno sin afectar el flujo diario
