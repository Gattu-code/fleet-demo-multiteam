from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .auth import hash_password
from .database import Base, SessionLocal, engine
from .models import (
    Loan,
    LoanAsset,
    LoanCategory,
    LoanChecklistItem,
    OperationalChecklist,
    OperationalIssue,
    Team,
    TeamConfig,
    User,
    Vehicle,
    VehicleTransfer,
)


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"

DEMO_USERS = [
    {"username": "admin", "role": "admin", "password": "demo123", "team_name": None},
    {"username": "fleet_supervisor", "role": "fleet_supervisor", "password": "demo123", "team_name": None},
    {"username": "coordinator", "role": "coordinator", "password": "demo123", "team_name": "Marketing Volvo"},
    {"username": "operator", "role": "operator", "password": "demo123", "team_name": "Operaciones Demo"},
    {"username": "viewer", "role": "viewer", "password": "demo123", "team_name": "Administrativo"},
]

DEMO_TEAMS = [
    "Marketing Volvo",
    "Marketing Hyundai",
    "Marketing Peugeot",
    "Comercial A",
    "Operaciones Demo",
    "Administrativo",
]

LOAN_CATEGORY_NAMES = [
    "Influencer / creador",
    "Produccion de contenido",
    "Evento / activacion",
    "Prensa / medios",
    "Cliente VIP",
    "Uso interno marketing",
    "Agencia / proveedor",
    "Test drive / experiencia de marca",
    "Logistica / traslado",
    "Mantenimiento / preparacion",
    "Otro",
]

DEMO_VEHICLE_TEAMS = {
    "VOL-101": "Marketing Volvo",
    "VOL-202": "Marketing Hyundai",
    "VOL-303": "Marketing Peugeot",
    "VOL-404": "Comercial A",
    "VOL-505": "Operaciones Demo",
    "VOL-606": "Administrativo",
}

TEAM_CONFIG_DEFAULTS = {
    "Marketing Volvo": {
        "allows_transfers": True,
        "requires_delivery_photos": True,
        "requires_return_photos": True,
        "default_loan_category": "Evento / activacion",
    },
    "Marketing Hyundai": {
        "allows_transfers": True,
        "requires_delivery_photos": True,
        "requires_return_photos": False,
        "default_loan_category": "Produccion de contenido",
    },
    "Marketing Peugeot": {
        "allows_transfers": True,
        "requires_delivery_photos": False,
        "requires_return_photos": True,
        "default_loan_category": "Prensa / medios",
    },
    "Comercial A": {
        "allows_transfers": True,
        "requires_delivery_photos": False,
        "requires_return_photos": False,
        "default_loan_category": None,
    },
    "Operaciones Demo": {
        "allows_transfers": False,
        "requires_delivery_photos": True,
        "requires_return_photos": True,
        "default_loan_category": "Logistica / traslado",
    },
    "Administrativo": {
        "allows_transfers": True,
        "requires_delivery_photos": False,
        "requires_return_photos": False,
        "default_loan_category": "Uso interno marketing",
    },
}

OPERATIONAL_CHECKLISTS = [
    ("delivery", 1, "Documentacion del vehiculo", "Verificar placa, estado general y equipo asignado."),
    ("delivery", 2, "Datos del receptor", "Confirmar identidad y datos de contacto del receptor."),
    ("delivery", 3, "Evidencia de entrega", "Registrar evidencia visual y observaciones iniciales."),
    ("return", 1, "Estado de retorno", "Revisar kilometraje, combustible y estado general."),
    ("return", 2, "Evidencia de recepcion", "Registrar fotos o video del retorno."),
    ("return", 3, "Novedades", "Documentar incidencias detectadas en la devolucion."),
]


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def ensure_schema():
    with engine.begin() as connection:
        vehicle_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(vehicles)")
        }
        if "team_id" not in vehicle_columns:
            connection.exec_driver_sql("ALTER TABLE vehicles ADD COLUMN team_id INTEGER")
        if "reference_image_path" not in vehicle_columns:
            connection.exec_driver_sql(
                "ALTER TABLE vehicles ADD COLUMN reference_image_path VARCHAR(255)"
            )
        if "has_open_issue" not in vehicle_columns:
            connection.exec_driver_sql(
                "ALTER TABLE vehicles ADD COLUMN has_open_issue BOOLEAN NOT NULL DEFAULT 0"
            )
        team_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(teams)")
        }
        if "is_active" not in team_columns:
            connection.exec_driver_sql(
                "ALTER TABLE teams ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
            )

        loan_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(loans)")
        }
        if "team_id" not in loan_columns:
            connection.exec_driver_sql("ALTER TABLE loans ADD COLUMN team_id INTEGER")
        if "return_has_issues" not in loan_columns:
            connection.exec_driver_sql(
                "ALTER TABLE loans ADD COLUMN return_has_issues BOOLEAN NOT NULL DEFAULT 0"
            )
        if "delivery_operator" not in loan_columns:
            connection.exec_driver_sql(
                "ALTER TABLE loans ADD COLUMN delivery_operator VARCHAR(120)"
            )
        if "return_operator" not in loan_columns:
            connection.exec_driver_sql(
                "ALTER TABLE loans ADD COLUMN return_operator VARCHAR(120)"
            )
        if "loan_category" not in loan_columns:
            connection.exec_driver_sql(
                "ALTER TABLE loans ADD COLUMN loan_category VARCHAR(80)"
            )
        category_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(loan_categories)")
        }
        if "is_active" not in category_columns:
            connection.exec_driver_sql(
                "ALTER TABLE loan_categories ADD COLUMN is_active BOOLEAN NOT NULL DEFAULT 1"
            )

        user_columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(users)")
        }
        if "team_id" not in user_columns:
            connection.exec_driver_sql("ALTER TABLE users ADD COLUMN team_id INTEGER")


def first_upload(pattern: str) -> str | None:
    matches = sorted((UPLOAD_DIR / "loan_assets").glob(pattern))
    if not matches:
        return None
    return f"/uploads/loan_assets/{matches[0].name}"


def first_vehicle_image() -> str | None:
    matches = sorted((UPLOAD_DIR / "vehicle_refs").glob("vehicle-*"))
    if not matches:
        return None
    return f"/uploads/vehicle_refs/{matches[0].name}"


def ensure_team(db, name: str) -> Team:
    team = db.scalar(select(Team).where(Team.name == name))
    if team is None:
        team = Team(name=name, is_active=True)
        db.add(team)
        db.flush()
    else:
        team.is_active = True
    return team


def ensure_team_config(db, team: Team, defaults: dict | None = None) -> TeamConfig:
    config = db.scalar(select(TeamConfig).where(TeamConfig.team_id == team.id))
    if config is None:
        config = TeamConfig(team_id=team.id)
        db.add(config)
        db.flush()
    defaults = defaults or {}
    config.allows_transfers = defaults.get("allows_transfers", True)
    config.requires_delivery_photos = defaults.get("requires_delivery_photos", False)
    config.requires_return_photos = defaults.get("requires_return_photos", False)
    default_category_name = defaults.get("default_loan_category")
    if default_category_name:
        category = db.scalar(select(LoanCategory).where(LoanCategory.name == default_category_name))
        config.default_loan_category_id = category.id if category else None
    else:
        config.default_loan_category_id = None
    return config


def ensure_operational_checklists(db):
    existing = {
        (item.kind, item.name)
        for item in db.scalars(select(OperationalChecklist)).all()
    }
    for kind, sort_order, name, description in OPERATIONAL_CHECKLISTS:
        if (kind, name) not in existing:
            db.add(
                OperationalChecklist(
                    kind=kind,
                    name=name,
                    description=description,
                    sort_order=sort_order,
                    is_active=True,
                )
            )


def ensure_loan_checklist_items(db, loan: Loan):
    active_templates = db.scalars(
        select(OperationalChecklist)
        .where(OperationalChecklist.is_active.is_(True))
        .order_by(OperationalChecklist.sort_order, OperationalChecklist.name)
    ).all()
    existing_ids = {item.checklist_id for item in loan.checklist_items}
    for template in active_templates:
        if template.id in existing_ids:
            continue
        completed = template.kind == "delivery" or (template.kind == "return" and loan.returned_at is not None)
        db.add(
            LoanChecklistItem(
                loan=loan,
                checklist_id=template.id,
                completed=completed,
                completed_at=loan.delivered_at if template.kind == "delivery" else loan.returned_at if completed else None,
            )
        )


def ensure_operational_issues(db, loan: Loan):
    if loan.return_has_issues and not loan.issues:
        db.add(
            OperationalIssue(
                loan=loan,
                kind="return",
                severity="medium",
                title="Novedad en devolucion",
                description="La devolucion historica reporto novedades para revision.",
                resolved=False,
            )
        )


def ensure_loan_categories(db):
    existing_names = set(db.scalars(select(LoanCategory.name)).all())
    names_to_seed = list(LOAN_CATEGORY_NAMES)
    names_to_seed.extend(
        name
        for name in db.scalars(select(Loan.loan_category).where(Loan.loan_category.is_not(None))).all()
        if name
    )
    for name in dict.fromkeys(names_to_seed):
        if name not in existing_names:
            db.add(LoanCategory(name=name, is_active=True))


def get_or_create_vehicle(db, data):
    vehicle = db.scalar(select(Vehicle).where(Vehicle.plate == data["plate"]))
    if not vehicle:
        vehicle = Vehicle(**data)
        db.add(vehicle)
        db.flush()
        return vehicle

    if data.get("team_id") is not None:
        vehicle.team_id = data["team_id"]
    vehicle.brand = data["brand"]
    vehicle.model = data["model"]
    vehicle.year = data["year"]
    vehicle.color = data["color"]
    vehicle.status = data["status"]
    vehicle.has_open_issue = data["has_open_issue"]
    if data.get("reference_image_path"):
        vehicle.reference_image_path = data["reference_image_path"]
    return vehicle


def add_asset(db, loan, category, path, filename, content_type):
    if not path:
        return
    db.add(
        LoanAsset(
            loan=loan,
            category=category,
            file_path=path,
            original_filename=filename,
            content_type=content_type,
        )
    )


def ensure_default_users(db):
    for user_data in DEMO_USERS:
        user = db.scalar(select(User).where(User.username == user_data["username"]))
        team = ensure_team(db, user_data["team_name"]) if user_data["team_name"] else None
        password_hash = hash_password(user_data["password"])
        if user is None:
            db.add(
                User(
                    username=user_data["username"],
                    team_id=team.id if team else None,
                    role=user_data["role"],
                    password_hash=password_hash,
                    is_active=True,
                )
            )
            continue
        user.team_id = team.id if team else None
        user.role = user_data["role"]
        user.password_hash = password_hash
        user.is_active = True


def ensure_demo_teams(db):
    teams = {name: ensure_team(db, name) for name in DEMO_TEAMS}
    for team_name, defaults in TEAM_CONFIG_DEFAULTS.items():
        ensure_team_config(db, teams[team_name], defaults)
    for plate, team_name in DEMO_VEHICLE_TEAMS.items():
        vehicle = db.scalar(select(Vehicle).where(Vehicle.plate == plate))
        if vehicle is None:
            continue
        vehicle.team_id = teams[team_name].id
        for loan in vehicle.loans:
            loan.team_id = teams[team_name].id
            ensure_loan_checklist_items(db, loan)
            ensure_operational_issues(db, loan)


def seed():
    Base.metadata.create_all(bind=engine)
    ensure_schema()

    db = SessionLocal()
    try:
        for team_name in DEMO_TEAMS:
            ensure_team(db, team_name)
        default_team = db.scalar(select(Team).where(Team.name == "Marketing Demo"))
        if default_team is None:
            default_team = Team(name="Marketing Demo")
            db.add(default_team)
            db.flush()
        ensure_team_config(
            db,
            default_team,
            {
                "allows_transfers": True,
                "requires_delivery_photos": False,
                "requires_return_photos": False,
                "default_loan_category": None,
            },
        )

        ensure_loan_categories(db)
        ensure_operational_checklists(db)
        ensure_default_users(db)
        ensure_demo_teams(db)

        agreement_path = first_upload("agreement-*")
        delivery_path = first_upload("delivery-*")
        return_path = first_upload("return-*")
        issue_path = first_upload("return_issue-*")
        vehicle_image_path = first_vehicle_image()

        old_demo_plates = ["MKT-101", "MKT-202", "MKT-303", "MKT-404"]
        old_demo_vehicles = db.scalars(
            select(Vehicle)
            .options(selectinload(Vehicle.loans).selectinload(Loan.assets))
            .where(Vehicle.plate.in_(old_demo_plates))
        ).all()
        for vehicle in old_demo_vehicles:
            db.delete(vehicle)
        db.flush()

        vehicles = [
            {
                "brand": "Volvo",
                "model": "EX30",
                "year": 2024,
                "plate": "VOL-101",
                "color": "Cloud Blue",
                "status": "available",
                "has_open_issue": False,
                "reference_image_path": vehicle_image_path,
                "team_id": db.scalar(select(Team).where(Team.name == DEMO_VEHICLE_TEAMS["VOL-101"])).id,
            },
            {
                "brand": "Hyundai",
                "model": "IONIQ 5",
                "year": 2025,
                "plate": "VOL-202",
                "color": "Gravity Gold",
                "status": "assigned",
                "has_open_issue": False,
                "reference_image_path": vehicle_image_path,
                "team_id": db.scalar(select(Team).where(Team.name == DEMO_VEHICLE_TEAMS["VOL-202"])).id,
            },
            {
                "brand": "Peugeot",
                "model": "e-2008",
                "year": 2024,
                "plate": "VOL-303",
                "color": "Vertigo Blue",
                "status": "available",
                "has_open_issue": True,
                "reference_image_path": vehicle_image_path,
                "team_id": db.scalar(select(Team).where(Team.name == DEMO_VEHICLE_TEAMS["VOL-303"])).id,
            },
            {
                "brand": "Volvo",
                "model": "EX90",
                "year": 2025,
                "plate": "VOL-404",
                "color": "Platinum Grey",
                "status": "available",
                "has_open_issue": True,
                "reference_image_path": vehicle_image_path,
                "team_id": db.scalar(select(Team).where(Team.name == DEMO_VEHICLE_TEAMS["VOL-404"])).id,
            },
            {
                "brand": "Volvo",
                "model": "XC60",
                "year": 2025,
                "plate": "VOL-505",
                "color": "Crystal White",
                "status": "available",
                "has_open_issue": False,
                "reference_image_path": vehicle_image_path,
                "team_id": db.scalar(select(Team).where(Team.name == DEMO_VEHICLE_TEAMS["VOL-505"])).id,
            },
            {
                "brand": "Peugeot",
                "model": "3008",
                "year": 2025,
                "plate": "VOL-606",
                "color": "Pearl White",
                "status": "available",
                "has_open_issue": False,
                "reference_image_path": vehicle_image_path,
                "team_id": db.scalar(select(Team).where(Team.name == DEMO_VEHICLE_TEAMS["VOL-606"])).id,
            },
        ]

        created = {
            item["plate"]: get_or_create_vehicle(db, item)
            for item in vehicles
        }
        db.flush()

        demo_vehicles = db.scalars(
            select(Vehicle)
            .options(selectinload(Vehicle.loans).selectinload(Loan.assets))
            .where(Vehicle.plate.in_(created.keys()))
        ).all()
        for vehicle in demo_vehicles:
            for loan in list(vehicle.loans):
                db.delete(loan)
        db.flush()

        now = utc_now()
        names = [
            "Carlos Medina",
            "Laura Prieto",
            "Valentina Rojas",
            "Sofia Vargas",
            "Giovanny Torres",
            "Daniela Ortiz",
            "Mateo Alvarez",
            "Camila Suarez",
            "Andres Cano",
            "Isabella Mora",
            "Julian Restrepo",
            "Paula Gomez",
            "Nicolas Mejia",
            "Mariana Leon",
            "Felipe Torres",
        ]
        categories = [
            "Influencer / creador",
            "Produccion de contenido",
            "Evento / activacion",
            "Prensa / medios",
            "Cliente VIP",
            "Uso interno marketing",
            "Agencia / proveedor",
            "Test drive / experiencia de marca",
            "Logistica / traslado",
            "Mantenimiento / preparacion",
        ]
        operators = [
            "Andrea Lopez",
            "Miguel Rios",
            "Camila Duarte",
            "Paula Gomez",
            "Santiago Perez",
            "Nicolas Mejia",
        ]
        fuel_levels = ["Full", "3/4", "1/2", "1/4"]
        plates = ["VOL-101", "VOL-202", "VOL-303", "VOL-404", "VOL-505", "VOL-606"]
        base_mileage = {
            "VOL-101": 9000,
            "VOL-202": 18000,
            "VOL-303": 15000,
            "VOL-404": 10000,
            "VOL-505": 12000,
            "VOL-606": 16000,
        }

        demo_loans = []
        for index in range(60):
            plate = plates[index % len(plates)]
            name = names[index % len(names)]
            duration_days = (index % 9) + 1
            delivered_days_ago = 180 - (index * 3)
            delivered_at = now - timedelta(days=max(delivered_days_ago, 1), hours=index % 7)
            is_active = index in {57, 58, 59}
            return_has_issues = (index % 11) in {0, 5}
            delivery_mileage = base_mileage[plate] + (index * 137)
            mileage_used = 45 + ((index * 83) % 950)
            returned_at = None if is_active else delivered_at + timedelta(days=duration_days, hours=(index % 5) + 1)

            demo_loans.append(
                {
                    "plate": plate,
                    "borrower_name": name,
                    "phone": f"+57 3{index % 10}0 555 {1000 + index}",
                    "email": f"{name.lower().replace(' ', '.')}@example.com",
                    "instagram": f"@{name.lower().replace(' ', '')}",
                    "loan_category": categories[index % len(categories)],
                    "delivery_operator": operators[index % len(operators)],
                    "return_operator": None if is_active else operators[(index + 2) % len(operators)],
                    "delivery_mileage": delivery_mileage,
                    "return_mileage": None if is_active else delivery_mileage + mileage_used,
                    "fuel_level": fuel_levels[index % len(fuel_levels)],
                    "notes": (
                        f"Prestamo demo #{index + 1} para {categories[index % len(categories)].lower()}."
                        if not return_has_issues
                        else f"Prestamo demo #{index + 1}.\n\nReturn notes: Novedad reportada en recepcion para revision."
                    ),
                    "agreement_signed": index % 8 != 0,
                    "return_has_issues": False if is_active else return_has_issues,
                    "delivered_at": delivered_at,
                    "returned_at": returned_at,
                }
            )

        for item in demo_loans:
            loan_data = item.copy()
            vehicle = created[loan_data.pop("plate")]
            loan_data["team_id"] = vehicle.team_id
            loan = Loan(vehicle_id=vehicle.id, **loan_data)
            db.add(loan)
            db.flush()
            ensure_loan_checklist_items(db, loan)

            if loan.agreement_signed:
                add_asset(db, loan, "agreement", agreement_path, "comodato-demo.pdf", "application/pdf")
            add_asset(db, loan, "delivery", delivery_path, "entrega-demo.png", "image/png")
            if loan.returned_at:
                add_asset(db, loan, "return", return_path, "devolucion-demo.png", "image/png")
            if loan.return_has_issues:
                add_asset(db, loan, "return_issue", issue_path, "novedad-demo.png", "image/png")
                ensure_operational_issues(db, loan)

        active_plates = {
            item["plate"]
            for item in demo_loans
            if item["returned_at"] is None
        }
        issue_plates = {
            item["plate"]
            for item in demo_loans
            if item["return_has_issues"]
        }
        for plate, vehicle in created.items():
            vehicle.status = "assigned" if plate in active_plates else "available"
            vehicle.has_open_issue = plate in issue_plates

        db.commit()
    finally:
        db.close()


if __name__ == "__main__":
    seed()
    print("Demo data reset and loaded.")
