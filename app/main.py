from datetime import UTC, datetime, timedelta
import os
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from .auth import AuthenticationRequired, get_session_user, require_user, verify_password
from .database import Base, SessionLocal, engine, get_db
from .models import Loan, LoanAsset, Team, User, Vehicle
from .seed import ensure_default_users as seed_ensure_default_users


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"
UPLOAD_DIR = Path(os.getenv("UPLOAD_DIR", str(BASE_DIR / "uploads")))
VEHICLE_IMAGE_DIR = UPLOAD_DIR / "vehicle_refs"
LOAN_ASSET_DIR = UPLOAD_DIR / "loan_assets"

STATIC_DIR.mkdir(exist_ok=True)
VEHICLE_IMAGE_DIR.mkdir(parents=True, exist_ok=True)
LOAN_ASSET_DIR.mkdir(parents=True, exist_ok=True)
Base.metadata.create_all(bind=engine)


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


ensure_schema()


def ensure_default_team():
    db = SessionLocal()
    try:
        team = db.scalar(select(Team).where(Team.name == "Marketing Demo"))
        if team is None:
            team = Team(name="Marketing Demo")
            db.add(team)
            db.flush()

        for vehicle in db.scalars(select(Vehicle).where(Vehicle.team_id.is_(None))).all():
            vehicle.team_id = team.id

        for loan in db.scalars(select(Loan).where(Loan.team_id.is_(None))).all():
            loan.team_id = team.id

        db.commit()
    finally:
        db.close()


ensure_default_team()


def ensure_demo_users():
    db = SessionLocal()
    try:
        seed_ensure_default_users(db)
        db.commit()
    finally:
        db.close()


ensure_demo_users()

app = FastAPI(title="Gestión de flotas (Marketing) :: Demo")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=BASE_DIR / "uploads"), name="uploads")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


@app.middleware("http")
async def attach_current_user(request: Request, call_next):
    session_data = request.scope.get("session") or {}
    user_id = session_data.get("user_id")
    if user_id is None:
        request.state.current_user = None
    else:
        db = SessionLocal()
        try:
            request.state.current_user = db.get(User, user_id)
        finally:
            db.close()
    return await call_next(request)


@app.exception_handler(AuthenticationRequired)
def authentication_required_handler(request: Request, exc: AuthenticationRequired):
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev-session-secret"))

LOAN_CATEGORIES = [
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


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


def save_upload(file: UploadFile | None, prefix: str, directory: Path) -> str | None:
    if not file or not file.filename:
        return None

    directory.mkdir(parents=True, exist_ok=True)
    content = file.file.read()
    if not content:
        return None

    suffix = Path(file.filename).suffix.lower()
    filename = f"{prefix}-{uuid4().hex}{suffix}"
    path = directory / filename

    with path.open("wb") as buffer:
        buffer.write(content)

    relative = path.relative_to(UPLOAD_DIR).as_posix()
    return f"/uploads/{relative}"


def add_loan_assets(
    db: Session,
    loan: Loan,
    files: list[UploadFile] | None,
    category: str,
):
    for file in files or []:
        file_path = save_upload(file, category, LOAN_ASSET_DIR)
        if not file_path:
            continue

        db.add(
            LoanAsset(
                loan=loan,
                category=category,
                file_path=file_path,
                original_filename=file.filename,
                content_type=file.content_type,
            )
        )


def clean_optional(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip()
    return cleaned or None


def enrich_loans(loans: list[Loan]):
    now = utc_now()
    return [
        {
            "loan": loan,
            "days_on_loan": max(((loan.returned_at or now) - loan.delivered_at).days, 0),
            "mileage_used": (
                loan.return_mileage - loan.delivery_mileage
                if loan.return_mileage is not None
                else None
            ),
        }
        for loan in loans
    ]


def load_teams(db: Session):
    return db.scalars(select(Team).order_by(Team.name)).all()


def parse_optional_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def pct(part: int | float, total: int | float) -> float:
    if not total:
        return 0
    return round((part / total) * 100, 1)


def period_range(period: str | None):
    now = utc_now()
    current_month = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    current_quarter_month = ((now.month - 1) // 3) * 3 + 1
    current_quarter = now.replace(month=current_quarter_month, day=1, hour=0, minute=0, second=0, microsecond=0)
    current_year = now.replace(month=1, day=1, hour=0, minute=0, second=0, microsecond=0)

    if period == "month":
        return current_month, None
    if period == "last_month":
        last_month_end = current_month
        if current_month.month == 1:
            last_month_start = current_month.replace(year=current_month.year - 1, month=12)
        else:
            last_month_start = current_month.replace(month=current_month.month - 1)
        return last_month_start, last_month_end
    if period == "quarter":
        return current_quarter, None
    if period == "last_quarter":
        last_quarter_end = current_quarter
        if current_quarter.month == 1:
            last_quarter_start = current_quarter.replace(year=current_quarter.year - 1, month=10)
        else:
            last_quarter_start = current_quarter.replace(month=current_quarter.month - 3)
        return last_quarter_start, last_quarter_end
    if period == "year":
        return current_year, None
    if period == "last_year":
        return current_year.replace(year=current_year.year - 1), current_year
    return None, None


def login_redirect_target(next_url: str | None = None):
    if not next_url:
        return "/dashboard"
    if not next_url.startswith("/"):
        return "/dashboard"
    return next_url


@app.get("/")
def home():
    return RedirectResponse(url="/dashboard", status_code=303)


@app.get("/login")
def login(request: Request, next: str | None = None):
    if request.state.current_user:
        return RedirectResponse(url=login_redirect_target(next), status_code=303)
    return templates.TemplateResponse(
        "login.html",
        {
            "request": request,
            "next": login_redirect_target(next),
        },
    )


@app.post("/login")
def login_submit(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    next: str | None = Form(None),
    db: Session = Depends(get_db),
):
    user = db.scalar(select(User).where(User.username == username.strip(), User.is_active.is_(True)))
    if user is None or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": login_redirect_target(next),
                "error": "Credenciales invalidas",
            },
            status_code=401,
        )

    request.session["user_id"] = user.id
    return RedirectResponse(url=login_redirect_target(next), status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.pop("user_id", None)
    return RedirectResponse(url="/login", status_code=303)


@app.get("/dashboard")
def dashboard(
    request: Request,
    period: str | None = "all",
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    selected_team_id = parse_optional_int(team_id)
    teams = load_teams(db)
    vehicle_query = select(Vehicle).options(selectinload(Vehicle.loans))
    loan_query = (
        select(Loan)
        .options(selectinload(Loan.vehicle), selectinload(Loan.assets))
        .order_by(Loan.delivered_at.desc())
    )
    if selected_team_id is not None:
        vehicle_query = vehicle_query.where(Vehicle.team_id == selected_team_id)
        loan_query = loan_query.where(Loan.team_id == selected_team_id)

    vehicles = db.scalars(vehicle_query).all()
    all_loans = db.scalars(loan_query).all()
    start, end = period_range(period)
    loans = [
        loan for loan in all_loans
        if (start is None or loan.delivered_at >= start)
        and (end is None or loan.delivered_at < end)
    ]
    loan_rows = enrich_loans(loans)

    total_loans = len(loans)
    active_loans = sum(1 for loan in loans if loan.returned_at is None)
    returned_loans = total_loans - active_loans
    active_fleet = [vehicle for vehicle in vehicles if vehicle.status != "retired"]
    total_vehicles = len(active_fleet)
    retired_vehicles = sum(1 for vehicle in vehicles if vehicle.status == "retired")
    available_vehicles = sum(1 for vehicle in active_fleet if vehicle.status == "available")
    assigned_vehicles = sum(1 for vehicle in active_fleet if vehicle.status == "assigned")
    issue_loans = sum(1 for loan in loans if loan.return_has_issues)
    signed_with_file = sum(
        1
        for loan in loans
        if loan.agreement_signed
        and any(asset.category == "agreement" for asset in loan.assets)
    )
    signed_without_file = sum(
        1
        for loan in loans
        if loan.agreement_signed
        and not any(asset.category == "agreement" for asset in loan.assets)
    )
    not_signed = sum(1 for loan in loans if not loan.agreement_signed)
    missing_agreements = signed_without_file + not_signed

    closed_rows = [row for row in loan_rows if row["loan"].returned_at is not None]
    total_days = sum(row["days_on_loan"] for row in loan_rows)
    total_km = sum(row["mileage_used"] or 0 for row in closed_rows)
    avg_days = round(total_days / total_loans, 1) if total_loans else 0
    avg_km = round(total_km / len(closed_rows), 1) if closed_rows else 0

    by_category = {}
    issues_by_category = {}
    for loan in loans:
        category = loan.loan_category or "Sin categoria"
        by_category[category] = by_category.get(category, 0) + 1
        if loan.return_has_issues:
            issues_by_category[category] = issues_by_category.get(category, 0) + 1

    by_model = {}
    top_vehicles = {}
    for row in loan_rows:
        loan = row["loan"]
        vehicle = loan.vehicle
        model = vehicle.model
        by_model[model] = by_model.get(model, 0) + 1
        key = f"{vehicle.brand} {vehicle.model} / {vehicle.plate}"
        stats = top_vehicles.setdefault(
            key,
            {"count": 0, "days": 0, "km": 0, "vehicle_id": vehicle.id},
        )
        stats["count"] += 1
        stats["days"] += row["days_on_loan"]
        stats["km"] += row["mileage_used"] or 0

    by_month = {}
    for loan in loans:
        month = loan.delivered_at.strftime("%Y-%m")
        by_month[month] = by_month.get(month, 0) + 1

    delivery_operators = {}
    return_operators = {}
    for loan in loans:
        if loan.delivery_operator:
            delivery_operators[loan.delivery_operator] = delivery_operators.get(loan.delivery_operator, 0) + 1
        if loan.return_operator:
            return_operators[loan.return_operator] = return_operators.get(loan.return_operator, 0) + 1

    longest_loans = sorted(
        loan_rows,
        key=lambda row: row["days_on_loan"],
        reverse=True,
    )[:8]

    return templates.TemplateResponse(
        "dashboard.html",
        {
            "request": request,
            "selected_period": period or "all",
            "selected_team_id": selected_team_id,
            "teams": teams,
            "total_loans": total_loans,
            "active_loans": active_loans,
            "returned_loans": returned_loans,
            "total_vehicles": total_vehicles,
            "retired_vehicles": retired_vehicles,
            "available_vehicles": available_vehicles,
            "assigned_vehicles": assigned_vehicles,
            "issue_loans": issue_loans,
            "issue_rate": pct(issue_loans, returned_loans),
            "signed_with_file": signed_with_file,
            "signed_without_file": signed_without_file,
            "not_signed": not_signed,
            "missing_agreements": missing_agreements,
            "missing_agreement_rate": pct(missing_agreements, total_loans),
            "agreement_complete_rate": pct(signed_with_file, total_loans),
            "total_km": total_km,
            "avg_km": avg_km,
            "avg_days": avg_days,
            "by_category": sorted(by_category.items(), key=lambda item: item[1], reverse=True),
            "issues_by_category": sorted(issues_by_category.items(), key=lambda item: item[1], reverse=True),
            "by_model": sorted(by_model.items(), key=lambda item: item[1], reverse=True),
            "by_month": sorted(by_month.items()),
            "top_vehicles": sorted(top_vehicles.items(), key=lambda item: item[1]["count"], reverse=True)[:8],
            "delivery_operators": sorted(delivery_operators.items(), key=lambda item: item[1], reverse=True)[:6],
            "return_operators": sorted(return_operators.items(), key=lambda item: item[1], reverse=True)[:6],
            "longest_loans": longest_loans,
        },
    )


@app.get("/vehicles")
def list_vehicles(
    request: Request,
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    selected_team_id = parse_optional_int(team_id)
    teams = load_teams(db)
    vehicle_query = (
        select(Vehicle)
        .options(selectinload(Vehicle.loans).selectinload(Loan.assets))
        .order_by(Vehicle.created_at.desc())
    )
    if selected_team_id is not None:
        vehicle_query = vehicle_query.where(Vehicle.team_id == selected_team_id)

    vehicles = db.scalars(vehicle_query).all()
    vehicle_rows = []
    for vehicle in vehicles:
        active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
        last_issue_loan = next((loan for loan in vehicle.loans if loan.return_has_issues), None)
        agreement_asset = None
        if active_loan:
            agreement_asset = next(
                (asset for asset in active_loan.assets if asset.category == "agreement"),
                None,
            )
        vehicle_rows.append(
            {
                "vehicle": vehicle,
                "active_loan": active_loan,
                "last_issue_loan": last_issue_loan,
                "agreement_asset": agreement_asset,
            }
        )

    return templates.TemplateResponse(
        "vehicles/list.html",
        {
            "request": request,
            "vehicle_rows": vehicle_rows,
            "teams": teams,
            "selected_team_id": selected_team_id,
        },
    )


@app.get("/loans")
def list_loans(
    request: Request,
    status: str | None = None,
    category: str | None = None,
    agreement: str | None = None,
    vehicle_id: int | None = None,
    saved: int | None = None,
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    selected_team_id = parse_optional_int(team_id)
    teams = load_teams(db)
    loans_query = (
        select(Loan)
        .options(selectinload(Loan.vehicle), selectinload(Loan.assets))
        .order_by(Loan.created_at.desc())
    )
    if selected_team_id is not None:
        loans_query = loans_query.where(Loan.team_id == selected_team_id)
    loans = db.scalars(loans_query).all()

    loan_rows = []
    for loan in loans:
        if status == "active" and loan.returned_at is not None:
            continue
        if status == "returned" and loan.returned_at is None:
            continue
        if status == "issues" and not loan.return_has_issues:
            continue
        if category and loan.loan_category != category:
            continue
        if vehicle_id and loan.vehicle_id != vehicle_id:
            continue
        agreement_asset = next(
            (asset for asset in loan.assets if asset.category == "agreement"),
            None,
        )
        has_complete_agreement = loan.agreement_signed and agreement_asset is not None
        if agreement == "missing" and has_complete_agreement:
            continue

        row = enrich_loans([loan])[0]
        row["agreement_asset"] = agreement_asset
        loan_rows.append(row)

    return templates.TemplateResponse(
        "loans/list.html",
        {
            "request": request,
            "loan_rows": loan_rows,
            "loan_categories": LOAN_CATEGORIES,
            "teams": teams,
            "selected_team_id": selected_team_id,
            "selected_status": status or "",
            "selected_category": category or "",
            "selected_agreement": agreement or "",
            "selected_vehicle_id": vehicle_id,
            "saved_loan_id": saved,
        },
    )


@app.get("/loans/{loan_id}")
def loan_detail(loan_id: int, request: Request, db: Session = Depends(get_db)):
    loan = db.scalar(
        select(Loan)
        .options(selectinload(Loan.vehicle), selectinload(Loan.assets))
        .where(Loan.id == loan_id)
    )
    if loan is None:
        return RedirectResponse(url="/loans", status_code=303)

    row = enrich_loans([loan])[0]
    row["agreement_asset"] = next(
        (asset for asset in loan.assets if asset.category == "agreement"),
        None,
    )
    return templates.TemplateResponse(
        "loans/detail.html",
        {"request": request, "row": row, "loan_categories": LOAN_CATEGORIES},
    )


@app.get("/operator/vehicles")
def operator_vehicles(request: Request, db: Session = Depends(get_db)):
    vehicles = db.scalars(
        select(Vehicle)
        .options(selectinload(Vehicle.loans))
        .where(Vehicle.status != "retired")
        .order_by(Vehicle.status.desc(), Vehicle.created_at.desc())
    ).all()
    vehicle_rows = [
        {
            "vehicle": vehicle,
            "active_loan": next(
                (loan for loan in vehicle.loans if loan.returned_at is None),
                None,
            ),
        }
        for vehicle in vehicles
    ]
    return templates.TemplateResponse(
        "operator/list.html",
        {"request": request, "vehicle_rows": vehicle_rows},
    )


@app.get("/operator/vehicles/{vehicle_id}")
def operator_vehicle_detail(
    vehicle_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    vehicle = db.scalar(
        select(Vehicle)
        .options(selectinload(Vehicle.loans).selectinload(Loan.assets))
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None:
        return RedirectResponse(url="/operator/vehicles", status_code=303)

    active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
    return templates.TemplateResponse(
        "operator/detail.html",
        {"request": request, "vehicle": vehicle, "active_loan": active_loan},
    )


@app.get("/vehicles/new")
def new_vehicle(request: Request, current_user: User = Depends(require_user)):
    return templates.TemplateResponse("vehicles/new.html", {"request": request})


@app.post("/vehicles")
def create_vehicle(
    brand: str = Form(...),
    model: str = Form(...),
    year: int | None = Form(None),
    plate: str = Form(...),
    color: str | None = Form(None),
    reference_image: UploadFile | None = File(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    reference_image_path = save_upload(reference_image, "vehicle", VEHICLE_IMAGE_DIR)
    default_team = db.scalar(select(Team).where(Team.name == "Marketing Demo"))
    if default_team is None:
        default_team = Team(name="Marketing Demo")
        db.add(default_team)
        db.flush()
    vehicle = Vehicle(
        team_id=default_team.id,
        brand=brand.strip(),
        model=model.strip(),
        year=year,
        plate=plate.strip().upper(),
        color=color.strip() if color else None,
        reference_image_path=reference_image_path,
        status="available",
    )
    db.add(vehicle)
    db.commit()
    return RedirectResponse(url="/vehicles", status_code=303)


@app.get("/vehicles/{vehicle_id}/edit")
def edit_vehicle(
    vehicle_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    vehicle = db.scalar(
        select(Vehicle)
        .options(selectinload(Vehicle.loans))
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None:
        return RedirectResponse(url="/vehicles", status_code=303)

    return templates.TemplateResponse(
        "vehicles/edit.html",
        {"request": request, "vehicle": vehicle},
    )


@app.post("/vehicles/{vehicle_id}/edit")
def update_vehicle(
    vehicle_id: int,
    brand: str = Form(...),
    model: str = Form(...),
    year: int | None = Form(None),
    plate: str = Form(...),
    color: str | None = Form(None),
    has_open_issue: bool = Form(False),
    is_retired: bool = Form(False),
    reference_image: UploadFile | None = File(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    vehicle = db.scalar(
        select(Vehicle)
        .options(selectinload(Vehicle.loans))
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None:
        return RedirectResponse(url="/vehicles", status_code=303)

    reference_image_path = save_upload(reference_image, "vehicle", VEHICLE_IMAGE_DIR)
    vehicle.brand = brand.strip()
    vehicle.model = model.strip()
    vehicle.year = year
    vehicle.plate = plate.strip().upper()
    vehicle.color = clean_optional(color)
    vehicle.has_open_issue = has_open_issue
    if is_retired:
        vehicle.status = "retired"
    else:
        has_active_loan = any(loan.returned_at is None for loan in vehicle.loans)
        vehicle.status = "assigned" if has_active_loan else "available"
    if reference_image_path:
        vehicle.reference_image_path = reference_image_path

    db.commit()
    return RedirectResponse(url=f"/vehicles/{vehicle.id}", status_code=303)


@app.get("/vehicles/{vehicle_id}")
def vehicle_detail(
    vehicle_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    vehicle = db.scalar(
        select(Vehicle)
        .options(selectinload(Vehicle.loans).selectinload(Loan.assets))
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None:
        return RedirectResponse(url="/vehicles", status_code=303)

    active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
    return templates.TemplateResponse(
        "vehicles/detail.html",
        {
            "request": request,
            "vehicle": vehicle,
            "active_loan": active_loan,
            "loan_rows": enrich_loans(vehicle.loans),
        },
    )


@app.post("/vehicles/{vehicle_id}/deliver")
def deliver_vehicle(
    vehicle_id: int,
    borrower_name: str = Form(...),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    instagram: str | None = Form(None),
    delivery_operator: str | None = Form(None),
    delivery_mileage: int = Form(...),
    fuel_level: str | None = Form(None),
    notes: str | None = Form(None),
    agreement_signed: bool = Form(False),
    delivery_files: list[UploadFile] = File(default=[]),
    agreement_file: UploadFile | None = File(None),
    redirect_to: str | None = Form(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    vehicle = db.get(Vehicle, vehicle_id)
    if vehicle is None or vehicle.status != "available":
        return RedirectResponse(url="/vehicles", status_code=303)

    if vehicle.team_id is None:
        default_team = db.scalar(select(Team).where(Team.name == "Marketing Demo"))
        if default_team is None:
            default_team = Team(name="Marketing Demo")
            db.add(default_team)
            db.flush()
        vehicle.team_id = default_team.id

    loan = Loan(
        vehicle_id=vehicle.id,
        team_id=vehicle.team_id,
        borrower_name=borrower_name.strip(),
        phone=clean_optional(phone),
        email=clean_optional(email),
        instagram=clean_optional(instagram),
        delivery_operator=clean_optional(delivery_operator),
        delivery_mileage=delivery_mileage,
        fuel_level=clean_optional(fuel_level),
        notes=clean_optional(notes),
        agreement_signed=agreement_signed,
    )
    vehicle.status = "assigned"
    db.add(loan)
    db.flush()
    add_loan_assets(db, loan, delivery_files, "delivery")
    add_loan_assets(db, loan, [agreement_file] if agreement_file else [], "agreement")
    db.commit()
    if redirect_to == "operator":
        return RedirectResponse(url=f"/operator/vehicles/{vehicle.id}", status_code=303)
    return RedirectResponse(url=f"/vehicles/{vehicle.id}", status_code=303)


@app.get("/loans/{loan_id}/edit-delivery")
def edit_delivery(
    loan_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    loan = db.scalar(
        select(Loan)
        .options(selectinload(Loan.assets), selectinload(Loan.vehicle))
        .where(Loan.id == loan_id)
    )
    if loan is None:
        return RedirectResponse(url="/vehicles", status_code=303)

    return templates.TemplateResponse(
        "loans/edit_delivery.html",
        {"request": request, "loan": loan},
    )


@app.post("/loans/{loan_id}/edit-delivery")
def update_delivery(
    loan_id: int,
    borrower_name: str = Form(...),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    instagram: str | None = Form(None),
    delivery_operator: str | None = Form(None),
    delivery_mileage: int = Form(...),
    fuel_level: str | None = Form(None),
    notes: str | None = Form(None),
    agreement_signed: bool = Form(False),
    delivery_files: list[UploadFile] = File(default=[]),
    agreement_file: UploadFile | None = File(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    loan = db.get(Loan, loan_id)
    if loan is None:
        return RedirectResponse(url="/vehicles", status_code=303)

    loan.borrower_name = borrower_name.strip()
    loan.phone = clean_optional(phone)
    loan.email = clean_optional(email)
    loan.instagram = clean_optional(instagram)
    loan.delivery_operator = clean_optional(delivery_operator)
    loan.delivery_mileage = delivery_mileage
    loan.fuel_level = clean_optional(fuel_level)
    loan.notes = clean_optional(notes)
    loan.agreement_signed = agreement_signed
    add_loan_assets(db, loan, delivery_files, "delivery")
    add_loan_assets(db, loan, [agreement_file] if agreement_file else [], "agreement")
    db.commit()
    return RedirectResponse(url=f"/vehicles/{loan.vehicle_id}", status_code=303)


@app.post("/loans/{loan_id}/category")
def update_loan_category(
    loan_id: int,
    loan_category: str | None = Form(None),
    filter_status: str | None = Form(None),
    filter_category: str | None = Form(None),
    filter_agreement: str | None = Form(None),
    filter_vehicle_id: int | None = Form(None),
    filter_team_id: int | None = Form(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    loan = db.get(Loan, loan_id)
    if loan is None:
        return RedirectResponse(url="/vehicles", status_code=303)

    loan.loan_category = clean_optional(loan_category)
    db.commit()
    query = {
        "saved": str(loan.id),
    }
    if filter_status:
        query["status"] = filter_status
    if filter_category:
        query["category"] = filter_category
    if filter_agreement:
        query["agreement"] = filter_agreement
    if filter_vehicle_id:
        query["vehicle_id"] = str(filter_vehicle_id)
    if filter_team_id:
        query["team_id"] = str(filter_team_id)
    return RedirectResponse(url=f"/loans?{urlencode(query)}", status_code=303)


@app.post("/loans/{loan_id}/return")
def return_vehicle(
    loan_id: int,
    return_mileage: int = Form(...),
    return_operator: str | None = Form(None),
    fuel_level: str | None = Form(None),
    return_has_issues: bool = Form(False),
    notes: str | None = Form(None),
    return_files: list[UploadFile] = File(default=[]),
    return_issue_files: list[UploadFile] = File(default=[]),
    redirect_to: str | None = Form(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    loan = db.get(Loan, loan_id)
    if loan is None:
        return RedirectResponse(url="/vehicles", status_code=303)

    loan.return_mileage = return_mileage
    loan.return_operator = clean_optional(return_operator)
    loan.returned_at = utc_now()
    loan.return_has_issues = return_has_issues
    add_loan_assets(db, loan, return_files, "return")
    add_loan_assets(db, loan, return_issue_files, "return_issue")
    cleaned_fuel_level = clean_optional(fuel_level)
    cleaned_notes = clean_optional(notes)
    if cleaned_fuel_level:
        loan.fuel_level = cleaned_fuel_level
    if cleaned_notes:
        loan.notes = f"{loan.notes or ''}\n\nReturn notes: {cleaned_notes}".strip()

    loan.vehicle.status = "available"
    if return_has_issues:
        loan.vehicle.has_open_issue = True
    db.commit()
    if redirect_to == "operator":
        return RedirectResponse(url=f"/operator/vehicles/{loan.vehicle_id}", status_code=303)
    return RedirectResponse(url=f"/vehicles/{loan.vehicle_id}", status_code=303)
