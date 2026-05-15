# Decisiones cerradas

## Plataforma

- Se mantiene FastAPI.
- Se mantiene SQLite inicialmente.
- Se mantiene Docker para despliegue local y demo.
- No se introduce React.
- No se hace rewrite de la aplicacion.

## Estrategia

- Se avanza por fases pequenas y revisables.
- Se reutilizan templates, estilos y rutas existentes cuando es posible.
- Se prioriza compatibilidad con el demo actual.
- Se conserva el historial operativo.

## Dominio

- `Team` es la unidad de visibilidad.
- `User.team_id` define la pertenencia principal del usuario.
- `Vehicle.team_id` representa el equipo actual del vehiculo.
- `Loan.team_id` es un snapshot historico y no debe reescribirse.
- `VehicleTransfer` registra movimientos entre equipos.
- `LoanCategory` es un catalogo administrable, pero el prestamo conserva su texto historico.
- `TeamConfig` controla reglas operativas por equipo.

## Roles y experiencia

- `admin` y `fleet_supervisor` tienen visibilidad global.
- `coordinator` ve su equipo.
- `viewer` ve solo lectura de su equipo.
- `operator` usa una experiencia operativa separada.

## Operacion vs administracion

- La operacion no debe mezclarse con la edicion administrativa.
- Las transferencias viven en la edicion administrativa.
- El detalle historico muestra trazabilidad, no acciones de cambio.

## UX

- Se separo listado y edicion en administracion de categorias.
- Se simplificaron banners pesados para categoria sugerida.
- Se agregaron hints discretos para reglas operativas.
- Se mantuvo una vista operativa ligera para `operator`.
- Se normalizo `Estado comodato` a indicadores compactos y semantica simple.
- Se mantuvieron labels mas claros para entrega y devolucion.
- Se redujo ruido visual en tablas administrativas.
- Se aclaro la semantica de `Novedad` en el listado historico de prestamos.

## Pendiente

- estabilizacion final de Fase 2B
- KPIs operativos de Fase 2C
- backlog de UX integral para operator
- rediseño de la tabla de `loans`
- formulario o flujo separado para crear equipos
- operator multi-equipo
- permisos granulares tipo RBAC enterprise
