from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from .auth import hash_password
from .database import Base, SessionLocal, engine
from .models import Loan, LoanAsset, Team, User, Vehicle


BASE_DIR = Path(__file__).resolve().parent.parent
UPLOAD_DIR = BASE_DIR / "uploads"

DEMO_USERS = [
    {"username": "admin", "role": "admin", "password": "demo123", "team_name": "Marketing Demo"},
    {"username": "fleet_supervisor", "role": "fleet_supervisor", "password": "demo123", "team_name": "Marketing Demo"},
    {"username": "coordinator", "role": "coordinator", "password": "demo123", "team_name": "Marketing Demo"},
    {"username": "operator", "role": "operator", "password": "demo123", "team_name": "Operaciones Demo"},
    {"username": "viewer", "role": "viewer", "password": "demo123", "team_name": "Operaciones Demo"},
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
        team = Team(name=name)
        db.add(team)
        db.flush()
    return team


def get_or_create_vehicle(db, data):
    vehicle = db.scalar(select(Vehicle).where(Vehicle.plate == data["plate"]))
    if not vehicle:
        vehicle = Vehicle(**data)
        db.add(vehicle)
        db.flush()
        return vehicle

    if data.get("team_id") is not None and vehicle.team_id is None:
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
        team = ensure_team(db, user_data["team_name"])
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


def ensure_demo_team_split(db):
    operations_team = ensure_team(db, "Operaciones Demo")

    operations_plates = {"VOL-505", "VOL-606"}
    vehicles = db.scalars(select(Vehicle).where(Vehicle.plate.in_(operations_plates))).all()
    for vehicle in vehicles:
        vehicle.team_id = operations_team.id
        for loan in vehicle.loans:
            loan.team_id = operations_team.id


def seed():
    Base.metadata.create_all(bind=engine)
    ensure_schema()

    db = SessionLocal()
    try:
        default_team = db.scalar(select(Team).where(Team.name == "Marketing Demo"))
        if default_team is None:
            default_team = Team(name="Marketing Demo")
            db.add(default_team)
            db.flush()

        ensure_default_users(db)
        ensure_demo_team_split(db)

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
                "team_id": default_team.id,
            },
            {
                "brand": "Volvo",
                "model": "EX40",
                "year": 2025,
                "plate": "VOL-202",
                "color": "Onyx Black",
                "status": "assigned",
                "has_open_issue": False,
                "reference_image_path": vehicle_image_path,
                "team_id": default_team.id,
            },
            {
                "brand": "Volvo",
                "model": "EC40",
                "year": 2024,
                "plate": "VOL-303",
                "color": "Fjord Blue",
                "status": "available",
                "has_open_issue": True,
                "reference_image_path": vehicle_image_path,
                "team_id": default_team.id,
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
                "team_id": default_team.id,
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
                "team_id": default_team.id,
            },
            {
                "brand": "Volvo",
                "model": "XC90",
                "year": 2025,
                "plate": "VOL-606",
                "color": "Denim Blue",
                "status": "available",
                "has_open_issue": False,
                "reference_image_path": vehicle_image_path,
                "team_id": default_team.id,
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
            loan_data["team_id"] = vehicle.team_id or default_team.id
            loan = Loan(vehicle_id=vehicle.id, **loan_data)
            db.add(loan)
            db.flush()

            if loan.agreement_signed:
                add_asset(db, loan, "agreement", agreement_path, "comodato-demo.pdf", "application/pdf")
            add_asset(db, loan, "delivery", delivery_path, "entrega-demo.png", "image/png")
            if loan.returned_at:
                add_asset(db, loan, "return", return_path, "devolucion-demo.png", "image/png")
            if loan.return_has_issues:
                add_asset(db, loan, "return_issue", issue_path, "novedad-demo.png", "image/png")

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
