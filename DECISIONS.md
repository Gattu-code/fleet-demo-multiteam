# Decisiones cerradas

## Plataforma

- Se mantiene FastAPI.
- Se mantiene SQLite inicialmente.
- Se mantiene Docker como forma de despliegue.
- No se introduce React.
- No se reescribe la aplicacion desde cero.

## Evolucion

- Se avanza por fases pequenas.
- Se reutilizan templates, estilos y rutas existentes cuando es posible.
- Se prioriza compatibilidad con el demo actual.

## Dominio

- Se usa `Team` como unidad principal de visibilidad.
- Se usa `User.team_id` para la pertenencia del usuario a un equipo.
- Se usa `Vehicle.team_id` como equipo actual del vehiculo.
- Se usa `Loan.team_id` como snapshot historico del equipo al momento del prestamo.
- Las transferencias se registran con `VehicleTransfer`.

## Roles y experiencia

- `admin` y `fleet_supervisor` conservan visibilidad global.
- `coordinator` trabaja dentro de su equipo.
- `operator` usa una experiencia operativa separada en `/operator/vehicles`.
- `viewer` queda en lectura acotada.

## Operacion vs administracion

- El flujo operativo no debe mezclarse con edicion administrativa.
- La transferencia de vehiculos vive en la edicion administrativa.
- El detalle historico muestra trazabilidad, no acciones de cambio.

## No decidido todavia

- catalogos maestros definitivos
- configuracion por equipo avanzada
- aprobaciones
- campanas
- permisos granulares tipo RBAC enterprise
