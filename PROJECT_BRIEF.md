# FlotasMultimarca AS

Sistema de gestion de flotas de mercadeo para Astara.

Objetivo:
Controlar vehiculos asignados a diferentes equipos de mercadeo y operacion, incluyendo prestamos, entregas, devoluciones, evidencia fotografica, comodatos e historial operativo.

La aplicacion reutiliza el proyecto fleet-demo existente y lo evoluciona de forma incremental.

Prioridades:
- reutilizar estilos y templates existentes
- mantener SQLite inicialmente
- mantener FastAPI
- mantener Docker deployment actual
- evolucionar sin romper el demo

Conceptos ya presentes:
- equipos
- usuarios
- roles
- scopes por equipo
- transferencias de vehiculos
- supervisor transversal

Roles:
- admin
- fleet_supervisor
- coordinator
- operator
- viewer

Reglas:
- una placa no puede existir dos veces
- un vehiculo no puede tener dos prestamos activos
- no borrar historial
- los prestamos mantienen trazabilidad historica con `loan.team_id`

Separacion funcional:
- operacion: entrega, devolucion, novedades y vista operativa
- administracion: configuracion, inventario y transferencia de vehiculos

Estado:
- Fase 1 cerrada: equipos, auth basica, scopes, operador operativo y transferencias
- Fase 2 pendiente: datos maestros y configuracion administrativa
