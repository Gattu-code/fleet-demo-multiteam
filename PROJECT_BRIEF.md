# FlotasMultimarca AS

Sistema de gestion de flotas de mercadeo para Astara.

Objetivo:
Controlar vehiculos asignados a distintos equipos de mercadeo y operacion, incluyendo prestamos, entregas, devoluciones, evidencia fotografica, comodatos, transferencias e historial operativo.

La aplicacion reutiliza el proyecto fleet-demo existente y evoluciona de forma incremental.

## Estado del producto

- Fase 1 cerrada: autenticacion, roles, scopes, operador operativo y transferencias
- Fase 2A cerrada: equipos, usuarios, categorias y configuracion por equipo
- Fase 2B avanzada y en estabilizacion: checklist operativo, evidencia, novedades, validaciones por fotos y cleanup UX

## Prioridades

- reutilizar estilos y templates existentes
- mantener SQLite inicialmente
- mantener FastAPI
- mantener Docker deployment actual
- evolucionar sin romper el demo

## Conceptos presentes

- equipos
- usuarios
- roles
- scopes por equipo
- configuracion por equipo
- categorias de prestamo
- transferencias de vehiculos
- checklist operativo
- evidencia agrupada
- novedades estructuradas
- labels operativos normalizados
- indicadores compactos de comodato

## Roles

- admin
- fleet_supervisor
- coordinator
- operator
- viewer

## Reglas

- una placa no puede existir dos veces
- un vehiculo no puede tener dos prestamos activos
- no borrar historial
- los prestamos mantienen trazabilidad historica con `Loan.team_id`

## Separacion funcional

- operacion: entrega, devolucion, novedades y vista operativa
- administracion: equipos, usuarios, categorias, configuracion e inventario

## Pendientes

- estabilizacion final de Fase 2B
- UX integral de `operator`
- rediseño de la tabla de `loans`
- creacion separada de equipos como flujo dedicado
- operator multi-equipo
- Fase 2C con KPIs operativos
