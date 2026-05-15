# Arquitectura actual

## Resumen

FlotasMultimarca AS esta construido como una aplicacion web interna con FastAPI, SQLite, SQLAlchemy y templates Jinja2. La evolucion es incremental: primero se consolido la operacion, luego la administracion basica, y ahora se estabiliza la operacion avanzada.

## Stack

- FastAPI
- SQLite
- SQLAlchemy
- Jinja2
- sesiones con `SessionMiddleware`
- Docker para despliegue local y demo

## Estado funcional

### Fase 1 cerrada

- autenticacion basica con sesiones
- roles demo
- scopes por equipo
- vista operativa separada para `operator`
- transferencias historicas entre equipos
- trazabilidad con `Loan.team_id`

### Fase 2A cerrada

- administracion basica de equipos
- administracion basica de usuarios
- catalogo administrable de categorias de prestamo
- `TeamConfig` por equipo

### Fase 2B en estabilizacion

- checklist operativo
- evidencia agrupada por tipo
- novedades estructuradas
- validaciones operativas por fotos
- resumen historico y operativo en vehiculo, prestamo y operador
- limpieza UX en formularios, listados y etiquetas operativas

## Dominio principal

- `Team`
- `User`
- `Vehicle`
- `Loan`
- `LoanAsset`
- `LoanCategory`
- `TeamConfig`
- `OperationalChecklist`
- `LoanChecklistItem`
- `OperationalIssue`
- `VehicleTransfer`

## Separacion funcional

### Operacion

- entrega
- devolucion
- novedades
- evidencia
- panel operativo de `operator`

### Administracion

- equipos
- usuarios
- categorias de prestamo
- configuracion por equipo
- inventario y edicion administrativa

## Trazabilidad

- `Vehicle.team_id` indica el equipo actual del vehiculo
- `Loan.team_id` conserva el equipo historico del prestamo
- `VehicleTransfer` registra cambios de equipo sin borrar historial
- `LoanAsset.category` organiza la evidencia por tipo

## Reglas operativas

- `TeamConfig.allows_transfers` bloquea transferencias
- `TeamConfig.requires_delivery_photos` y `requires_return_photos` ya impactan el flujo operativo
- `LoanCategory` alimenta los selects operativos
- los prestamos historicos siguen mostrando su texto guardado aunque el catalogo cambie
- la columna `Estado comodato` usa indicadores compactos con semantica de `Completo`, `Pendiente en préstamo activo` y `No cargado al cierre`
- la columna `Novedad` en `Prestamos` refleja el historial del propio `Loan`, no el estado abierto del vehiculo

## UX ya aplicada

- mensajes flash globales por sesion
- badges y avisos discretos para configuracion de equipo
- formularios de entrega y devolucion con categoria sugerida sin banner protagonista
- tabla de administracion de categorias separada de la edicion
- vista operativa de `operator` reducida a accion rapida
- labels de entrega y devolucion normalizados
- comodato y novedades con semantica mas compacta y consistente

## Principios

- evolucion incremental, no rewrite
- reutilizar templates y estilos existentes
- mantener compatibilidad con SQLite
- no perder historial operativo
- mantener separada la operacion de la administracion

## Siguiente etapa

- terminar la estabilizacion de Fase 2B
- avanzar a Fase 2C con KPIs operativos y lectura gerencial
