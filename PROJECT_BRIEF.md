# FlotasMultimarca AS

Sistema de gestión de flotas de mercadeo para Astara.

Objetivo:
Controlar vehículos asignados a diferentes equipos de mercadeo y operación, incluyendo préstamos, entregas, devoluciones, evidencia fotográfica, comodatos e historial operativo.

La aplicación debe reutilizar el proyecto fleet-demo existente.

Prioridades:
- Reutilizar estilos y templates existentes
- Mantener SQLite inicialmente
- Mantener FastAPI
- Mantener Docker deployment actual
- Evolucionar incrementalmente

Nuevos conceptos:
- Equipos
- Usuarios
- Roles
- Permisos por equipo
- Transferencias de vehículos
- Campañas pendientes
- Supervisor transversal

Roles:
- admin
- fleet_supervisor
- coordinator
- operator
- viewer

Reglas:
- Una placa no puede existir dos veces
- Un vehículo no puede tener dos préstamos activos
- No borrar historial
- Los préstamos mantienen trazabilidad histórica