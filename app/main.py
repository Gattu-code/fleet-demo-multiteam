from datetime import UTC, datetime, timedelta
import re
import os
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.responses import RedirectResponse
from starlette.middleware.sessions import SessionMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, selectinload

from .auth import AuthenticationRequired, get_session_user, hash_password, require_user, verify_password
from .database import Base, SessionLocal, engine, get_db
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
from .seed import (
    ensure_default_users as seed_ensure_default_users,
    ensure_demo_teams,
    ensure_loan_categories as seed_ensure_loan_categories,
)


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
            row[1]
            for row in connection.exec_driver_sql("PRAGMA table_info(loan_categories)")
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


ensure_schema()


def ensure_team_config(db: Session, team: Team) -> TeamConfig:
    config = db.scalar(select(TeamConfig).where(TeamConfig.team_id == team.id))
    if config is None:
        config = TeamConfig(
            team_id=team.id,
            allows_transfers=True,
            requires_delivery_photos=False,
            requires_return_photos=False,
            default_loan_category_id=None,
        )
        db.add(config)
        db.flush()
    return config


def ensure_default_team():
    db = SessionLocal()
    try:
        team = db.scalar(select(Team).where(Team.name == "Marketing Demo", Team.is_active.is_(True)))
        if team is None:
            active_team = db.scalars(select(Team).where(Team.is_active.is_(True)).order_by(Team.name)).first()
            if active_team is not None:
                team = active_team
            else:
                team = db.scalar(select(Team).where(Team.name == "Marketing Demo"))
                if team is not None:
                    team.is_active = True
        if team is None:
            team = Team(name="Marketing Demo", is_active=True)
            db.add(team)
            db.flush()

        ensure_team_config(db, team)

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
        seed_ensure_loan_categories(db)
        seed_ensure_default_users(db)
        ensure_demo_teams(db)
        db.commit()
    finally:
        db.close()


ensure_demo_users()

app = FastAPI(title="Gestión de flotas (Marketing) :: Demo")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
app.mount("/uploads", StaticFiles(directory=BASE_DIR / "uploads"), name="uploads")
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))


def normalize_plate_value(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = re.sub(r"[^A-Z0-9]", "", value.strip().upper())
    if not cleaned:
        return None
    if len(cleaned) == 6:
        return f"{cleaned[:3]}-{cleaned[3:]}"
    return cleaned


def format_plate_value(value: str | None) -> str:
    normalized = normalize_plate_value(value)
    return normalized or ""


def normalize_plate_search(value: str | None) -> str | None:
    normalized = normalize_plate_value(value)
    if normalized is None:
        return None
    return normalized.replace("-", "")


templates.env.filters["format_plate"] = format_plate_value


@app.middleware("http")
async def attach_current_user(request: Request, call_next):
    session_data = request.scope.get("session") or {}
    request.state.flash_messages = list(session_data.get("flash_messages", []))
    user_id = session_data.get("user_id")
    if user_id is None:
        request.state.current_user = None
    else:
        db = SessionLocal()
        try:
            request.state.current_user = db.get(User, user_id)
        finally:
            db.close()
    response = await call_next(request)
    if response.status_code < 300 or response.status_code >= 400:
        if "flash_messages" in session_data:
            session_data.pop("flash_messages", None)
    return response


@app.exception_handler(AuthenticationRequired)
def authentication_required_handler(request: Request, exc: AuthenticationRequired):
    flash_message(request, "warning", "Debes iniciar sesion para continuar.")
    next_url = request.url.path
    if request.url.query:
        next_url = f"{next_url}?{request.url.query}"
    return RedirectResponse(url=f"/login?next={next_url}", status_code=303)


app.add_middleware(SessionMiddleware, secret_key=os.getenv("SESSION_SECRET", "dev-session-secret"))


def load_loan_categories(db: Session, active_only: bool = True):
    query = select(LoanCategory)
    if active_only:
        query = query.where(LoanCategory.is_active.is_(True))
    return db.scalars(query.order_by(LoanCategory.name)).all()


def load_team_config(db: Session, team: Team | None) -> TeamConfig | None:
    if team is None:
        return None
    return db.scalar(
        select(TeamConfig)
        .options(selectinload(TeamConfig.default_loan_category))
        .where(TeamConfig.team_id == team.id)
    )


def load_operational_checklists(db: Session, kind: str):
    return db.scalars(
        select(OperationalChecklist)
        .where(OperationalChecklist.kind == kind, OperationalChecklist.is_active.is_(True))
        .order_by(OperationalChecklist.sort_order, OperationalChecklist.name)
    ).all()


def seed_loan_checklist_items(db: Session, loan: Loan):
    existing_checklist_ids = {item.checklist_id for item in loan.checklist_items}
    active_templates = db.scalars(
        select(OperationalChecklist)
        .where(OperationalChecklist.is_active.is_(True))
        .order_by(OperationalChecklist.sort_order, OperationalChecklist.name)
    ).all()
    for template in active_templates:
        if template.id in existing_checklist_ids:
            continue
        completed = template.kind == "delivery" or (template.kind == "return" and loan.returned_at is not None)
        db.add(
            LoanChecklistItem(
                loan_id=loan.id,
                checklist_id=template.id,
                completed=completed,
                notes=None,
                completed_at=loan.delivered_at if completed and template.kind == "delivery" else loan.returned_at if completed else None,
            )
        )


def load_loan_operational_context(db: Session, loan: Loan):
    return {
        "delivery_checklists": load_operational_checklists(db, "delivery"),
        "return_checklists": load_operational_checklists(db, "return"),
        "loan_checklist_items": loan.checklist_items,
        "loan_issues": loan.issues,
    }


def group_loan_assets(loan: Loan | None):
    grouped = {
        "delivery_photo": [],
        "return_photo": [],
        "agreement_doc": [],
        "issue_photo": [],
        "other": [],
    }
    if loan is None:
        return grouped

    for asset in loan.assets:
        if asset.category == "delivery":
            grouped["delivery_photo"].append(asset)
        elif asset.category == "return":
            grouped["return_photo"].append(asset)
        elif asset.category == "agreement":
            grouped["agreement_doc"].append(asset)
        elif asset.category == "return_issue":
            grouped["issue_photo"].append(asset)
        else:
            grouped["other"].append(asset)
    return grouped


def count_rows(db: Session, model):
    return db.scalar(select(func.count()).select_from(model)) or 0


def flash_message(request: Request, level: str, message: str, persist: bool = True):
    flash_entry = {"level": level, "message": message}
    flash_messages = getattr(request.state, "flash_messages", None)
    if flash_messages is None:
        flash_messages = []
        request.state.flash_messages = flash_messages
    flash_messages.append(flash_entry)
    if persist:
        session_messages = request.session.setdefault("flash_messages", [])
        session_messages.append(flash_entry)


def flash_redirect(
    request: Request,
    url: str,
    level: str,
    message: str,
    status_code: int = 303,
) -> RedirectResponse:
    flash_message(request, level, message)
    return RedirectResponse(url=url, status_code=status_code)


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


def has_uploaded_files(files: list[UploadFile] | None) -> bool:
    return any(file and file.filename for file in files or [])


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
            "asset_groups": group_loan_assets(loan),
        }
        for loan in loans
    ]


def build_query_suffix(params: dict[str, str | None]):
    query = {key: value for key, value in params.items() if value}
    return f"?{urlencode(query)}" if query else ""


def load_teams(db: Session, active_only: bool = True):
    query = select(Team)
    if active_only:
        query = query.where(Team.is_active.is_(True))
    return db.scalars(query.order_by(Team.name)).all()


def parse_optional_int(value: str | None) -> int | None:
    if not value:
        return None
    try:
        return int(value)
    except ValueError:
        return None


GLOBAL_ROLES = {"admin", "fleet_supervisor"}


def is_global_user(user: User | None) -> bool:
    return user is None or user.role in GLOBAL_ROLES


def can_transfer_vehicle(user: User | None, vehicle: Vehicle) -> bool:
    if user is None:
        return False
    if user.role in GLOBAL_ROLES:
        return True
    return user.role == "coordinator" and user.team_id is not None and user.team_id == vehicle.team_id


def can_admin_vehicle(user: User | None, vehicle: Vehicle | None = None) -> bool:
    if user is None:
        return False
    if user.role in GLOBAL_ROLES:
        return True
    if user.role != "coordinator" or user.team_id is None or vehicle is None:
        return False
    return vehicle.team_id == user.team_id


def admin_vehicle_redirect(user: User | None) -> str:
    if user is not None and user.role == "operator":
        return "/operator/vehicles"
    return "/vehicles"


def load_visible_teams(db: Session, user: User | None):
    if is_global_user(user):
        return load_teams(db, active_only=True)
    if user.team_id is None:
        return []
    team = db.get(Team, user.team_id)
    return [team] if team else []


def resolve_team_scope(requested_team_id: str | None, user: User | None) -> int | None:
    if is_global_user(user):
        return parse_optional_int(requested_team_id)
    return user.team_id


def apply_team_scope(query, user: User | None, column):
    if is_global_user(user):
        return query
    if user.team_id is None:
        return query.where(column == -1)
    return query.where(column == user.team_id)


def get_default_team(db: Session) -> Team:
    team = db.scalar(
        select(Team).where(Team.name == "Marketing Demo", Team.is_active.is_(True))
    )
    if team is not None:
        return team
    team = db.scalars(select(Team).where(Team.is_active.is_(True)).order_by(Team.name)).first()
    if team is not None:
        return team
    team = db.scalar(select(Team).where(Team.name == "Marketing Demo"))
    if team is not None:
        team.is_active = True
        return team
    team = Team(name="Marketing Demo", is_active=True)
    db.add(team)
    db.flush()
    return team


def pct(part: int | float, total: int | float) -> float:
    if not total:
        return 0
    return round((part / total) * 100, 1)


def build_dashboard_context(
    request: Request,
    period: str | None,
    team_id: str | None,
    db: Session,
    include_idle_rows: bool = False,
):
    current_user = request.state.current_user
    selected_team_id = resolve_team_scope(team_id, current_user)
    teams = load_visible_teams(db, current_user)
    vehicle_query = select(Vehicle).options(selectinload(Vehicle.loans), selectinload(Vehicle.team))
    loan_query = (
        select(Loan)
        .options(selectinload(Loan.vehicle), selectinload(Loan.assets))
        .order_by(Loan.delivered_at.desc())
    )
    vehicle_query = apply_team_scope(vehicle_query, current_user, Vehicle.team_id)
    loan_query = apply_team_scope(loan_query, current_user, Loan.team_id)
    if is_global_user(current_user) and selected_team_id is not None:
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
    fleet_avg_age_days = round(
        sum(max((utc_now() - vehicle.created_at).days, 0) for vehicle in active_fleet) / total_vehicles,
        1,
    ) if total_vehicles else 0
    closed_loans_count = sum(1 for loan in loans if loan.returned_at is not None)
    issue_loans = sum(1 for loan in loans if loan.return_has_issues)
    issue_vehicle_count = sum(1 for vehicle in vehicles if vehicle.has_open_issue)
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
    missing_agreements = sum(
        1
        for loan in loans
        if loan.returned_at is None
        and not (
            loan.agreement_signed
            and any(asset.category == "agreement" for asset in loan.assets)
        )
    )

    idle_vehicle_rows = []
    idle_vehicle_count = 0
    now = utc_now()
    for vehicle in vehicles:
        active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
        if vehicle.status != "available" or active_loan is not None:
            continue
        last_returned_at = max(
            (loan.returned_at for loan in vehicle.loans if loan.returned_at is not None),
            default=None,
        )
        baseline = last_returned_at or vehicle.created_at
        idle_days = max((now - baseline).days, 0)
        if idle_days <= 10:
            continue
        idle_vehicle_count += 1
        if include_idle_rows:
            idle_vehicle_rows.append(
                {
                    "vehicle": vehicle,
                    "team_name": vehicle.team.name if vehicle.team else "-",
                    "idle_days": idle_days,
                }
            )
    if include_idle_rows:
        idle_vehicle_rows.sort(key=lambda row: row["idle_days"], reverse=True)

    closed_rows = [row for row in loan_rows if row["loan"].returned_at is not None]
    total_days = sum(row["days_on_loan"] for row in loan_rows)
    total_km = sum(row["mileage_used"] or 0 for row in closed_rows)
    avg_days = round(total_days / len(loan_rows), 1) if loan_rows else 0
    avg_km = round(total_km / closed_loans_count, 1) if closed_loans_count else 0

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
    )[:10]

    context = {
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
        "available_rate": pct(available_vehicles, total_vehicles),
        "issue_loans": issue_loans,
        "issue_vehicle_count": issue_vehicle_count,
        "issue_rate": pct(issue_vehicle_count, total_vehicles),
        "signed_with_file": signed_with_file,
        "signed_without_file": signed_without_file,
        "not_signed": not_signed,
        "missing_agreements": missing_agreements,
        "missing_agreement_rate": pct(missing_agreements, active_loans),
        "agreement_complete_rate": pct(signed_with_file, total_loans),
        "utilization_rate": pct(assigned_vehicles, total_vehicles),
        "idle_rate": pct(idle_vehicle_count, total_vehicles),
        "total_km": total_km,
        "avg_km": avg_km,
        "avg_days": avg_days,
        "fleet_avg_age_days": fleet_avg_age_days,
        "by_category": sorted(by_category.items(), key=lambda item: item[1], reverse=True),
        "issues_by_category": sorted(issues_by_category.items(), key=lambda item: item[1], reverse=True),
        "by_model": sorted(by_model.items(), key=lambda item: item[1], reverse=True),
        "by_month": sorted(by_month.items()),
        "top_vehicles": sorted(top_vehicles.items(), key=lambda item: item[1]["count"], reverse=True)[:5],
        "delivery_operators": sorted(delivery_operators.items(), key=lambda item: item[1], reverse=True)[:6],
        "return_operators": sorted(return_operators.items(), key=lambda item: item[1], reverse=True)[:6],
        "longest_loans": longest_loans,
    }
    if include_idle_rows:
        context["idle_vehicle_rows"] = idle_vehicle_rows[:8]
    context["idle_vehicle_count"] = idle_vehicle_count
    return context


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


def login_redirect_target(next_url: str | None = None, user: User | None = None):
    if user is not None and user.role == "operator":
        if next_url and next_url.startswith("/operator"):
            return next_url
        return "/operator/vehicles"
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
        return RedirectResponse(url=login_redirect_target(next, request.state.current_user), status_code=303)
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
        flash_message(request, "error", "Credenciales invalidas", persist=False)
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "next": login_redirect_target(next),
            },
            status_code=401,
        )

    request.session["user_id"] = user.id
    flash_message(request, "success", f"Bienvenido, {user.username}")
    return RedirectResponse(url=login_redirect_target(next, user), status_code=303)


@app.post("/logout")
def logout(request: Request):
    request.session.pop("user_id", None)
    flash_message(request, "info", "Sesion cerrada correctamente.")
    return RedirectResponse(url="/login", status_code=303)


@app.get("/dashboard")
def dashboard(
    request: Request,
    period: str | None = "all",
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    current_user = request.state.current_user
    if current_user is not None and current_user.role == "operator":
        flash_message(request, "info", "Has sido enviado al panel operativo.")
        operator_target = "/operator/vehicles"
        if team_id:
            operator_target = f"{operator_target}?team_id={team_id}"
        return RedirectResponse(url=operator_target, status_code=303)
    context = build_dashboard_context(request, period, team_id, db, include_idle_rows=True)
    return templates.TemplateResponse(
        "dashboard.html",
        context,
    )


@app.get("/operations")
def operations_dashboard(
    request: Request,
    period: str | None = "all",
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    current_user = request.state.current_user
    if current_user is not None and current_user.role == "operator":
        flash_message(request, "info", "Has sido enviado al panel operativo.")
        operator_target = "/operator/vehicles"
        if team_id:
            operator_target = f"{operator_target}?team_id={team_id}"
        return RedirectResponse(url=operator_target, status_code=303)
    context = build_dashboard_context(request, period, team_id, db, include_idle_rows=True)
    return templates.TemplateResponse(
        "operations/dashboard.html",
        context,
    )


@app.get("/vehicles")
def list_vehicles(
    request: Request,
    team_id: str | None = None,
    plate: str | None = None,
    db: Session = Depends(get_db),
):
    current_user = request.state.current_user
    selected_team_id = resolve_team_scope(team_id, current_user)
    teams = load_visible_teams(db, current_user)
    plate_input = plate or request.query_params.get("plate")
    selected_plate = normalize_plate_search(plate_input)
    vehicle_query = (
        select(Vehicle)
        .options(selectinload(Vehicle.loans).selectinload(Loan.assets))
        .order_by(Vehicle.created_at.desc())
    )
    vehicle_query = apply_team_scope(vehicle_query, current_user, Vehicle.team_id)
    if selected_plate:
        normalized_plate_column = func.replace(func.replace(func.upper(Vehicle.plate), "-", ""), " ", "")
        vehicle_query = vehicle_query.where(normalized_plate_column.like(f"%{selected_plate}%"))
    if is_global_user(current_user) and selected_team_id is not None:
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
            "selected_plate": format_plate_value(plate_input),
        },
    )


@app.get("/admin")
def admin_index(request: Request, db: Session = Depends(get_db)):
    current_user = request.state.current_user
    if current_user is None or current_user.role not in {"admin", "fleet_supervisor"}:
        flash_message(request, "warning", "No tienes acceso al modulo administrativo.")
        return RedirectResponse(url="/dashboard", status_code=303)

    return templates.TemplateResponse(
        "admin/index.html",
        {
            "request": request,
            "team_count": count_rows(db, Team),
            "user_count": count_rows(db, User),
            "category_count": count_rows(db, LoanCategory),
        },
    )


@app.get("/admin/users")
def admin_users(request: Request, db: Session = Depends(get_db)):
    current_user = request.state.current_user
    if current_user is None or current_user.role not in {"admin", "fleet_supervisor"}:
        flash_message(request, "warning", "No tienes permisos para ver usuarios.")
        return RedirectResponse(url="/dashboard", status_code=303)

    users = db.scalars(
        select(User).options(selectinload(User.team)).order_by(User.username)
    ).all()
    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "users": users,
            "teams": load_teams(db, active_only=False),
            "can_edit_users": current_user.role == "admin",
        },
    )


@app.get("/admin/users/new")
def admin_new_user(request: Request, current_user: User = Depends(require_user), db: Session = Depends(get_db)):
    if current_user.role != "admin":
        flash_message(request, "warning", "Solo admin puede crear usuarios.")
        return RedirectResponse(url="/admin/users", status_code=303)
    return templates.TemplateResponse(
        "admin/user_form.html",
        {"request": request, "teams": load_teams(db, active_only=False), "user": None},
    )


@app.post("/admin/users")
def create_user(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    role: str = Form(...),
    team_id: int | None = Form(None),
    is_active: bool = Form(False),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role != "admin":
        flash_message(request, "warning", "Solo admin puede administrar usuarios.")
        return RedirectResponse(url="/admin/users", status_code=303)

    cleaned_username = clean_optional(username)
    if not cleaned_username:
        flash_message(request, "error", "El username es obligatorio.")
        return RedirectResponse(url="/admin/users/new", status_code=303)

    if role not in {"admin", "fleet_supervisor", "coordinator", "operator", "viewer"}:
        flash_message(request, "error", "Selecciona un rol valido.")
        return RedirectResponse(url="/admin/users/new", status_code=303)

    if db.scalar(select(User).where(User.username == cleaned_username)):
        flash_message(request, "warning", "Ya existe un usuario con ese username.")
        return RedirectResponse(url="/admin/users/new", status_code=303)

    team = db.get(Team, team_id) if team_id else None
    if team_id and team is None:
        flash_message(request, "error", "Selecciona un equipo valido.")
        return RedirectResponse(url="/admin/users/new", status_code=303)

    db.add(
        User(
            username=cleaned_username,
            password_hash=hash_password(password),
            role=role,
            team_id=team.id if team else None,
            is_active=is_active,
        )
    )
    db.commit()
    flash_message(request, "success", f"Usuario creado: {cleaned_username}.")
    return RedirectResponse(url="/admin/users", status_code=303)


@app.get("/admin/users/{user_id}/edit")
def admin_edit_user(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role != "admin":
        flash_message(request, "warning", "Solo admin puede modificar usuarios.")
        return RedirectResponse(url="/admin/users", status_code=303)

    user = db.scalar(select(User).options(selectinload(User.team)).where(User.id == user_id))
    if user is None:
        flash_message(request, "error", "El usuario no existe.")
        return RedirectResponse(url="/admin/users", status_code=303)

    return templates.TemplateResponse(
        "admin/user_form.html",
        {"request": request, "teams": load_teams(db, active_only=False), "user": user},
    )


@app.post("/admin/users/{user_id}")
def update_user(
    user_id: int,
    request: Request,
    username: str = Form(...),
    role: str = Form(...),
    team_id: int | None = Form(None),
    is_active: bool = Form(False),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role != "admin":
        flash_message(request, "warning", "Solo admin puede administrar usuarios.")
        return RedirectResponse(url="/admin/users", status_code=303)

    user = db.get(User, user_id)
    if user is None:
        flash_message(request, "error", "El usuario no existe.")
        return RedirectResponse(url="/admin/users", status_code=303)

    cleaned_username = clean_optional(username)
    if not cleaned_username:
        flash_message(request, "error", "El username es obligatorio.")
        return RedirectResponse(url=f"/admin/users/{user_id}/edit", status_code=303)

    if role not in {"admin", "fleet_supervisor", "coordinator", "operator", "viewer"}:
        flash_message(request, "error", "Selecciona un rol valido.")
        return RedirectResponse(url=f"/admin/users/{user_id}/edit", status_code=303)

    duplicate = db.scalar(select(User).where(User.username == cleaned_username, User.id != user.id))
    if duplicate is not None:
        flash_message(request, "warning", "Ya existe un usuario con ese username.")
        return RedirectResponse(url=f"/admin/users/{user_id}/edit", status_code=303)

    team = db.get(Team, team_id) if team_id else None
    if team_id and team is None:
        flash_message(request, "error", "Selecciona un equipo valido.")
        return RedirectResponse(url=f"/admin/users/{user_id}/edit", status_code=303)

    user.username = cleaned_username
    user.role = role
    user.team_id = team.id if team else None
    user.is_active = is_active
    db.commit()
    flash_message(request, "success", f"Usuario guardado: {user.username}.")
    return RedirectResponse(url="/admin/users", status_code=303)


@app.post("/admin/users/{user_id}/reset-password")
def reset_user_password(
    user_id: int,
    request: Request,
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role != "admin":
        flash_message(request, "warning", "Solo admin puede resetear contraseñas.")
        return RedirectResponse(url="/admin/users", status_code=303)

    user = db.get(User, user_id)
    if user is None:
        flash_message(request, "error", "El usuario no existe.")
        return RedirectResponse(url="/admin/users", status_code=303)

    user.password_hash = hash_password("demo123")
    db.commit()
    flash_message(request, "info", f"Contraseña reiniciada para {user.username}.")
    return RedirectResponse(url="/admin/users", status_code=303)


@app.get("/admin/teams")
def admin_teams(request: Request, db: Session = Depends(get_db)):
    current_user = request.state.current_user
    if current_user is None or current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar equipos.")
        return RedirectResponse(url="/dashboard", status_code=303)

    teams = db.scalars(
        select(Team)
        .options(selectinload(Team.config).selectinload(TeamConfig.default_loan_category))
        .order_by(Team.name)
    ).all()
    return templates.TemplateResponse(
        "admin/teams.html",
        {
            "request": request,
            "teams": teams,
        },
    )


@app.post("/admin/teams")
def create_team(
    request: Request,
    name: str = Form(...),
    is_active: bool = Form(False),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar equipos.")
        return RedirectResponse(url="/dashboard", status_code=303)

    cleaned = clean_optional(name)
    if not cleaned:
        flash_message(request, "error", "El nombre del equipo es obligatorio.")
        return RedirectResponse(url="/admin/teams", status_code=303)

    existing = db.scalar(select(Team).where(Team.name == cleaned))
    if existing is None:
        team = Team(name=cleaned, is_active=is_active)
        db.add(team)
        db.flush()
        ensure_team_config(db, team)
        flash_message(request, "success", f"Equipo creado: {cleaned}.")
    else:
        existing.is_active = is_active
        ensure_team_config(db, existing)
        flash_message(request, "info", f"Equipo actualizado: {cleaned}.")
    db.commit()
    return RedirectResponse(url="/admin/teams", status_code=303)


@app.post("/admin/teams/{team_id}")
def update_team(
    team_id: int,
    request: Request,
    name: str = Form(...),
    is_active: bool = Form(False),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar equipos.")
        return RedirectResponse(url="/dashboard", status_code=303)

    team = db.get(Team, team_id)
    if team is None:
        flash_message(request, "error", "El equipo no existe.")
        return RedirectResponse(url="/admin/teams", status_code=303)

    cleaned = clean_optional(name)
    if cleaned:
        duplicate = db.scalar(select(Team).where(Team.name == cleaned, Team.id != team.id))
        if duplicate is not None:
            flash_message(request, "warning", "Ya existe un equipo con ese nombre.")
            return RedirectResponse(url="/admin/teams", status_code=303)
        team.name = cleaned
    team.is_active = is_active
    ensure_team_config(db, team)
    db.commit()
    flash_message(request, "success", f"Equipo guardado: {team.name}.")
    return RedirectResponse(url="/admin/teams", status_code=303)


@app.get("/admin/teams/{team_id}/config")
def admin_team_config(
    team_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = request.state.current_user
    if current_user is None or current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar configuracion de equipos.")
        return RedirectResponse(url="/dashboard", status_code=303)

    team = db.scalar(
        select(Team)
        .options(selectinload(Team.config).selectinload(TeamConfig.default_loan_category))
        .where(Team.id == team_id)
    )
    if team is None:
        flash_message(request, "error", "El equipo no existe.")
        return RedirectResponse(url="/admin/teams", status_code=303)

    config = team.config or TeamConfig(
        team_id=team.id,
        allows_transfers=True,
        requires_delivery_photos=False,
        requires_return_photos=False,
        default_loan_category_id=None,
    )
    categories = db.scalars(
        select(LoanCategory).order_by(LoanCategory.is_active.desc(), LoanCategory.name)
    ).all()
    return templates.TemplateResponse(
        "admin/team_config_form.html",
        {
            "request": request,
            "team": team,
            "config": config,
            "categories": categories,
        },
    )


@app.post("/admin/teams/{team_id}/config")
def update_team_config(
    team_id: int,
    request: Request,
    allows_transfers: bool = Form(False),
    requires_delivery_photos: bool = Form(False),
    requires_return_photos: bool = Form(False),
    default_loan_category_id: str | None = Form(None),
    db: Session = Depends(get_db),
):
    current_user = request.state.current_user
    if current_user is None or current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar configuracion de equipos.")
        return RedirectResponse(url="/dashboard", status_code=303)

    team = db.get(Team, team_id)
    if team is None:
        flash_message(request, "error", "El equipo no existe.")
        return RedirectResponse(url="/admin/teams", status_code=303)

    config = ensure_team_config(db, team)
    category_id = parse_optional_int(default_loan_category_id)
    if default_loan_category_id and category_id is None:
        flash_message(request, "error", "Selecciona una categoria valida.")
        return RedirectResponse(url=f"/admin/teams/{team.id}/config", status_code=303)

    if category_id is not None:
        category = db.get(LoanCategory, category_id)
        if category is None:
            flash_message(request, "error", "Selecciona una categoria valida.")
            return RedirectResponse(url=f"/admin/teams/{team.id}/config", status_code=303)
        config.default_loan_category_id = category.id
    else:
        config.default_loan_category_id = None
    config.allows_transfers = allows_transfers
    config.requires_delivery_photos = requires_delivery_photos
    config.requires_return_photos = requires_return_photos
    db.commit()
    flash_message(request, "success", f"Configuracion guardada para {team.name}.")
    return RedirectResponse(url=f"/admin/teams/{team.id}/config", status_code=303)


@app.get("/admin/loan-categories")
def admin_loan_categories(request: Request, db: Session = Depends(get_db)):
    current_user = request.state.current_user
    if current_user is None or current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar categorias.")
        return RedirectResponse(url="/dashboard", status_code=303)

    categories = db.scalars(
        select(LoanCategory).order_by(LoanCategory.is_active.desc(), LoanCategory.name)
    ).all()
    return templates.TemplateResponse(
        "admin/loan_categories.html",
        {
            "request": request,
            "categories": categories,
        },
    )


@app.get("/admin/loan-categories/{category_id}")
def edit_loan_category(
    category_id: int,
    request: Request,
    db: Session = Depends(get_db),
):
    current_user = request.state.current_user
    if current_user is None or current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar categorias.")
        return RedirectResponse(url="/dashboard", status_code=303)

    category = db.get(LoanCategory, category_id)
    if category is None:
        flash_message(request, "error", "La categoria no existe.")
        return RedirectResponse(url="/admin/loan-categories", status_code=303)

    return templates.TemplateResponse(
        "admin/loan_category_form.html",
        {
            "request": request,
            "category": category,
        },
    )


@app.post("/admin/loan-categories")
def create_loan_category(
    request: Request,
    name: str = Form(...),
    is_active: bool = Form(False),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar categorias.")
        return RedirectResponse(url="/dashboard", status_code=303)

    cleaned = clean_optional(name)
    if not cleaned:
        flash_message(request, "error", "El nombre de la categoria es obligatorio.")
        return RedirectResponse(url="/admin/loan-categories", status_code=303)

    existing = db.scalar(select(LoanCategory).where(LoanCategory.name == cleaned))
    if existing is None:
        db.add(LoanCategory(name=cleaned, is_active=is_active))
        flash_message(request, "success", f"Categoria creada: {cleaned}.")
    else:
        existing.is_active = is_active
        flash_message(request, "info", f"Categoria actualizada: {cleaned}.")
    db.commit()
    return RedirectResponse(url="/admin/loan-categories", status_code=303)


@app.post("/admin/loan-categories/{category_id}")
def update_loan_category_master(
    category_id: int,
    request: Request,
    name: str = Form(...),
    is_active: bool = Form(False),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para administrar categorias.")
        return RedirectResponse(url="/dashboard", status_code=303)

    category = db.get(LoanCategory, category_id)
    if category is None:
        flash_message(request, "error", "La categoria no existe.")
        return RedirectResponse(url="/admin/loan-categories", status_code=303)

    cleaned = clean_optional(name)
    if cleaned:
        duplicate = db.scalar(
            select(LoanCategory).where(
                LoanCategory.name == cleaned,
                LoanCategory.id != category.id,
            )
        )
        if duplicate is not None:
            flash_message(request, "warning", "Ya existe una categoria con ese nombre.")
            return RedirectResponse(url="/admin/loan-categories", status_code=303)
        category.name = cleaned
    category.is_active = is_active
    db.commit()
    flash_message(request, "success", f"Categoria guardada: {category.name}.")
    return RedirectResponse(url="/admin/loan-categories", status_code=303)


@app.get("/loans")
def list_loans(
    request: Request,
    status: str | None = None,
    category: str | None = None,
    agreement: str | None = None,
    vehicle_id: int | None = None,
    filter_vehicle_id: int | None = None,
    saved: int | None = None,
    team_id: str | None = None,
    db: Session = Depends(get_db),
):
    current_user = request.state.current_user
    selected_team_id = resolve_team_scope(team_id, current_user)
    teams = load_visible_teams(db, current_user)
    loans_query = (
        select(Loan)
        .options(selectinload(Loan.vehicle), selectinload(Loan.assets), selectinload(Loan.issues))
        .order_by(Loan.created_at.desc())
    )
    loans_query = apply_team_scope(loans_query, current_user, Loan.team_id)
    if is_global_user(current_user) and selected_team_id is not None:
        loans_query = loans_query.where(Loan.team_id == selected_team_id)
    loans = db.scalars(loans_query).all()
    selected_vehicle_id = vehicle_id or filter_vehicle_id
    selected_vehicle_label = None
    if selected_vehicle_id:
        selected_vehicle = db.scalar(select(Vehicle).where(Vehicle.id == selected_vehicle_id))
        if selected_vehicle is not None:
            selected_vehicle_label = format_plate_value(selected_vehicle.plate)
        else:
            selected_vehicle_label = f"#{selected_vehicle_id}"
    clear_vehicle_filter_params = {}
    if status:
        clear_vehicle_filter_params["status"] = status
    if category:
        clear_vehicle_filter_params["category"] = category
    if agreement:
        clear_vehicle_filter_params["agreement"] = agreement
    if selected_team_id is not None:
        clear_vehicle_filter_params["team_id"] = str(selected_team_id)
    clear_vehicle_filter_url = "/loans"
    if clear_vehicle_filter_params:
        clear_vehicle_filter_url = f"/loans?{urlencode(clear_vehicle_filter_params)}"

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
        if selected_vehicle_id and loan.vehicle_id != selected_vehicle_id:
            continue
        agreement_asset = next(
            (asset for asset in loan.assets if asset.category == "agreement"),
            None,
        )
        has_complete_agreement = loan.agreement_signed and agreement_asset is not None
        if agreement in {"missing", "pending"} and has_complete_agreement:
            continue
        if agreement == "attached" and not has_complete_agreement:
            continue

        row = enrich_loans([loan])[0]
        row["agreement_asset"] = agreement_asset
        loan_rows.append(row)

    return templates.TemplateResponse(
        "loans/list.html",
        {
            "request": request,
            "loan_rows": loan_rows,
            "loan_categories": load_loan_categories(db),
            "teams": teams,
            "selected_team_id": selected_team_id,
            "selected_status": status or "",
            "selected_category": category or "",
            "selected_agreement": agreement or "",
            "selected_vehicle_id": selected_vehicle_id,
            "selected_vehicle_label": selected_vehicle_label,
            "clear_vehicle_filter_url": clear_vehicle_filter_url,
            "saved_loan_id": saved,
        },
    )


@app.get("/loans/{loan_id}")
def loan_detail(loan_id: int, request: Request, db: Session = Depends(get_db)):
    current_user = request.state.current_user
    loan = db.scalar(
        select(Loan)
        .options(
            selectinload(Loan.vehicle),
            selectinload(Loan.assets),
            selectinload(Loan.team),
            selectinload(Loan.checklist_items).selectinload(LoanChecklistItem.checklist),
            selectinload(Loan.issues),
        )
        .where(Loan.id == loan_id)
    )
    if loan is None:
        flash_message(request, "error", "El prestamo no existe.")
        return RedirectResponse(url="/loans", status_code=303)
    if not is_global_user(current_user) and current_user.team_id != loan.team_id:
        flash_message(request, "warning", "No tienes acceso a este prestamo.")
        return RedirectResponse(url="/loans", status_code=303)

    row = enrich_loans([loan])[0]
    return templates.TemplateResponse(
        "loans/detail.html",
        {
            "request": request,
            "row": row,
            "loan_categories": load_loan_categories(db),
            "historical_team_name": loan.team.name if loan.team else "Sin equipo",
            "operational_checklists": load_loan_operational_context(db, loan),
            "asset_groups": group_loan_assets(loan),
        },
    )


@app.get("/operator/vehicles")
def operator_vehicles(
    request: Request,
    plate: str | None = None,
    db: Session = Depends(get_db),
):
    current_user = request.state.current_user
    plate_input = plate or request.query_params.get("plate")
    selected_plate = normalize_plate_search(plate_input)
    vehicle_query = (
        select(Vehicle)
        .options(
            selectinload(Vehicle.loans).selectinload(Loan.assets),
            selectinload(Vehicle.team).selectinload(Team.config).selectinload(TeamConfig.default_loan_category),
        )
        .where(Vehicle.status != "retired")
        .order_by(Vehicle.status.desc(), Vehicle.created_at.desc())
    )
    vehicle_query = apply_team_scope(vehicle_query, current_user, Vehicle.team_id)
    if selected_plate:
        normalized_plate_column = func.replace(func.replace(func.upper(Vehicle.plate), "-", ""), " ", "")
        vehicle_query = vehicle_query.where(normalized_plate_column.like(f"%{selected_plate}%"))
    vehicles = db.scalars(vehicle_query).all()
    vehicle_rows = []
    for vehicle in vehicles:
        active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
        team_config = load_team_config(db, vehicle.team)
        alerts = []
        if vehicle.has_open_issue:
            alerts.append({"label": "Novedad abierta", "tone": "rose"})
        if team_config and team_config.requires_delivery_photos and not active_loan:
            alerts.append({"label": "Fotos entrega requeridas", "tone": "amber"})
        if team_config and team_config.requires_return_photos and active_loan:
            alerts.append({"label": "Fotos devolucion requeridas", "tone": "amber"})
        if active_loan:
            agreement_asset = next((asset for asset in active_loan.assets if asset.category == "agreement"), None)
            if active_loan.agreement_signed and agreement_asset is None:
                alerts.append({"label": "Comodato pendiente", "tone": "amber"})
        status_label = "Prestado" if active_loan else "Disponible"
        vehicle_rows.append(
            {
                "vehicle": vehicle,
                "active_loan": active_loan,
                "status_label": status_label,
                "alerts": alerts,
                "is_available": active_loan is None,
                "responsible_text": active_loan.borrower_name if active_loan else "",
                "responsible_subtext": (
                    f"Km entrega: {active_loan.delivery_mileage}" if active_loan else ""
                ),
            }
        )
    operator_query_suffix = build_query_suffix(
        {
            "team_id": request.query_params.get("team_id"),
            "plate": format_plate_value(plate_input) or None,
        }
    )
    return templates.TemplateResponse(
        "operator/list.html",
        {
            "request": request,
            "vehicle_rows": vehicle_rows,
            "selected_plate": format_plate_value(plate_input),
            "operator_query_suffix": operator_query_suffix,
        },
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
        .options(
            selectinload(Vehicle.loans).selectinload(Loan.assets),
            selectinload(Vehicle.loans).selectinload(Loan.team),
            selectinload(Vehicle.loans).selectinload(Loan.checklist_items).selectinload(LoanChecklistItem.checklist),
            selectinload(Vehicle.loans).selectinload(Loan.issues),
            selectinload(Vehicle.team).selectinload(Team.config).selectinload(TeamConfig.default_loan_category),
        )
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None:
        flash_message(request, "error", "El vehiculo no existe.")
        return RedirectResponse(url="/operator/vehicles", status_code=303)
    if not is_global_user(request.state.current_user) and request.state.current_user.team_id != vehicle.team_id:
        flash_message(request, "warning", "No tienes acceso a este vehiculo.")
        return RedirectResponse(url="/operator/vehicles", status_code=303)

    active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
    team_config = load_team_config(db, vehicle.team)
    default_category_name = None
    if team_config and team_config.default_loan_category and team_config.default_loan_category.is_active:
        default_category_name = team_config.default_loan_category.name
    active_checklist_item_map = {
        item.checklist_id: item for item in active_loan.checklist_items
    } if active_loan else {}
    operator_query_suffix = build_query_suffix(
        {
            "team_id": request.query_params.get("team_id"),
            "plate": request.query_params.get("plate"),
        }
    )
    return templates.TemplateResponse(
        "operator/detail.html",
        {
            "request": request,
            "vehicle": vehicle,
            "active_loan": active_loan,
            "historical_team_name": active_loan.team.name if active_loan and active_loan.team else "Sin equipo",
            "team_config": team_config,
            "team_default_loan_category_name": default_category_name,
            "loan_categories": load_loan_categories(db),
            "active_loan_asset_groups": group_loan_assets(active_loan),
            "operational_checklists": load_loan_operational_context(db, active_loan) if active_loan else {"delivery_checklists": [], "return_checklists": [], "loan_checklist_items": [], "loan_issues": []},
            "active_checklist_item_map": active_checklist_item_map,
            "operator_query_suffix": operator_query_suffix,
        },
    )


@app.get("/vehicles/new")
def new_vehicle(request: Request, current_user: User = Depends(require_user)):
    if current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para crear vehiculos.")
        return RedirectResponse(url=admin_vehicle_redirect(current_user), status_code=303)
    return templates.TemplateResponse("vehicles/new.html", {"request": request})


@app.post("/vehicles")
def create_vehicle(
    request: Request,
    brand: str = Form(...),
    model: str = Form(...),
    year: int | None = Form(None),
    plate: str = Form(...),
    color: str | None = Form(None),
    reference_image: UploadFile | None = File(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    if current_user.role not in GLOBAL_ROLES:
        flash_message(request, "warning", "No tienes permisos para crear vehiculos.")
        return RedirectResponse(url=admin_vehicle_redirect(current_user), status_code=303)
    reference_image_path = save_upload(reference_image, "vehicle", VEHICLE_IMAGE_DIR)
    default_team = get_default_team(db)
    normalized_plate = normalize_plate_value(plate) or plate.strip().upper()
    vehicle = Vehicle(
        team_id=default_team.id,
        brand=brand.strip(),
        model=model.strip(),
        year=year,
        plate=normalized_plate,
        color=color.strip() if color else None,
        reference_image_path=reference_image_path,
        status="available",
    )
    db.add(vehicle)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash_message(request, "error", "Ya existe un vehiculo con esa placa.")
        return RedirectResponse(url=admin_vehicle_redirect(current_user), status_code=303)
    flash_message(request, "success", f"Vehiculo creado: {vehicle.plate}.")
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
        .options(
            selectinload(Vehicle.loans),
            selectinload(Vehicle.team)
            .selectinload(Team.config)
            .selectinload(TeamConfig.default_loan_category),
        )
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None:
        flash_message(request, "error", "El vehiculo no existe.")
        return RedirectResponse(url="/vehicles", status_code=303)
    if not can_admin_vehicle(current_user, vehicle):
        flash_message(request, "warning", "No tienes permisos para editar este vehiculo.")
        return RedirectResponse(url=admin_vehicle_redirect(current_user), status_code=303)

    active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
    team_config = load_team_config(db, vehicle.team)
    default_category_name = None
    if team_config and team_config.default_loan_category and team_config.default_loan_category.is_active:
        default_category_name = team_config.default_loan_category.name

    return templates.TemplateResponse(
        "vehicles/edit.html",
        {
            "request": request,
            "vehicle": vehicle,
            "transfer_teams": [team for team in load_teams(db) if team.id != vehicle.team_id],
            "can_transfer": can_transfer_vehicle(current_user, vehicle),
            "current_team_name": vehicle.team.name if vehicle.team else "Sin equipo",
            "active_loan": active_loan,
            "team_config": team_config,
            "team_default_loan_category_name": default_category_name,
            "loan_categories": load_loan_categories(db),
        },
    )


@app.post("/vehicles/{vehicle_id}/edit")
def update_vehicle(
    vehicle_id: int,
    request: Request,
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
        flash_message(request, "error", "El vehiculo no existe.")
        return RedirectResponse(url="/vehicles", status_code=303)
    if not can_admin_vehicle(current_user, vehicle):
        flash_message(request, "warning", "No tienes permisos para editar este vehiculo.")
        return RedirectResponse(url=admin_vehicle_redirect(current_user), status_code=303)

    reference_image_path = save_upload(reference_image, "vehicle", VEHICLE_IMAGE_DIR)
    normalized_plate = normalize_plate_value(plate) or plate.strip().upper()
    vehicle.brand = brand.strip()
    vehicle.model = model.strip()
    vehicle.year = year
    vehicle.plate = normalized_plate
    vehicle.color = clean_optional(color)
    vehicle.has_open_issue = has_open_issue
    if is_retired:
        vehicle.status = "retired"
    else:
        has_active_loan = any(loan.returned_at is None for loan in vehicle.loans)
        vehicle.status = "assigned" if has_active_loan else "available"
    if reference_image_path:
        vehicle.reference_image_path = reference_image_path

    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        flash_message(request, "error", "Ya existe un vehiculo con esa placa.")
        return RedirectResponse(url=f"/vehicles/{vehicle.id}/edit", status_code=303)
    flash_message(request, "success", f"Vehiculo guardado: {vehicle.plate}.")
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
        .options(
            selectinload(Vehicle.loans).selectinload(Loan.assets),
            selectinload(Vehicle.loans).selectinload(Loan.checklist_items).selectinload(LoanChecklistItem.checklist),
            selectinload(Vehicle.loans).selectinload(Loan.issues),
            selectinload(Vehicle.transfer_history).selectinload(VehicleTransfer.from_team),
            selectinload(Vehicle.transfer_history).selectinload(VehicleTransfer.to_team),
            selectinload(Vehicle.transfer_history).selectinload(VehicleTransfer.transferred_by_user),
            selectinload(Vehicle.team)
            .selectinload(Team.config)
            .selectinload(TeamConfig.default_loan_category),
        )
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None:
        flash_message(request, "error", "El vehiculo no existe.")
        return RedirectResponse(url="/vehicles", status_code=303)
    if current_user.role == "operator":
        flash_message(request, "info", "Se abrio la vista operativa del vehiculo.")
        team_id = request.query_params.get("team_id")
        target = f"/operator/vehicles/{vehicle.id}"
        if team_id:
            target = f"{target}?team_id={team_id}"
        return RedirectResponse(url=target, status_code=303)
    if not is_global_user(current_user) and current_user.team_id != vehicle.team_id:
        flash_message(request, "warning", "No tienes acceso a este vehiculo.")
        return RedirectResponse(url="/vehicles", status_code=303)

    active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
    team_config = load_team_config(db, vehicle.team)
    default_category_name = None
    if team_config and team_config.default_loan_category and team_config.default_loan_category.is_active:
        default_category_name = team_config.default_loan_category.name
    active_checklist_item_map = {
        item.checklist_id: item for item in active_loan.checklist_items
    } if active_loan else {}
    return templates.TemplateResponse(
        "vehicles/detail.html",
        {
            "request": request,
            "vehicle": vehicle,
            "active_loan": active_loan,
            "loan_rows": enrich_loans(vehicle.loans),
            "transfer_teams": [team for team in load_teams(db) if team.id != vehicle.team_id],
            "can_transfer": can_transfer_vehicle(current_user, vehicle),
            "team_config": team_config,
            "team_default_loan_category_name": default_category_name,
            "loan_categories": load_loan_categories(db),
            "active_loan_asset_groups": group_loan_assets(active_loan),
            "operational_checklists": load_loan_operational_context(db, active_loan) if active_loan else {"delivery_checklists": [], "return_checklists": [], "loan_checklist_items": [], "loan_issues": []},
            "active_checklist_item_map": active_checklist_item_map,
        },
    )


@app.post("/vehicles/{vehicle_id}/transfer")
def transfer_vehicle(
    vehicle_id: int,
    request: Request,
    to_team_id: int = Form(...),
    notes: str | None = Form(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    vehicle = db.scalar(
        select(Vehicle)
        .options(
            selectinload(Vehicle.loans),
            selectinload(Vehicle.team)
            .selectinload(Team.config)
            .selectinload(TeamConfig.default_loan_category),
        )
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None:
        flash_message(request, "error", "El vehiculo no existe.")
        return RedirectResponse(url="/vehicles", status_code=303)

    if not can_transfer_vehicle(current_user, vehicle):
        flash_message(request, "warning", "No tienes permisos para transferir este vehiculo.")
        transfer_team = request.query_params.get("team_id")
        suffix = f"?transfer_error=forbidden"
        if transfer_team:
            suffix = f"?team_id={transfer_team}&transfer_error=forbidden"
        return RedirectResponse(url=f"/vehicles/{vehicle.id}/edit{suffix}", status_code=303)

    active_loan = next((loan for loan in vehicle.loans if loan.returned_at is None), None)
    if active_loan is not None:
        flash_message(request, "error", "No es posible transferir este vehiculo porque tiene un prestamo activo.")
        transfer_team = request.query_params.get("team_id")
        suffix = f"?transfer_error=active_loan"
        if transfer_team:
            suffix = f"?team_id={transfer_team}&transfer_error=active_loan"
        return RedirectResponse(url=f"/vehicles/{vehicle.id}/edit{suffix}", status_code=303)

    if vehicle.team is not None and vehicle.team.config is not None and not vehicle.team.config.allows_transfers:
        flash_message(request, "warning", "Este equipo no permite transferencias.")
        transfer_team = request.query_params.get("team_id")
        suffix = f"?transfer_error=team_blocked"
        if transfer_team:
            suffix = f"?team_id={transfer_team}&transfer_error=team_blocked"
        return RedirectResponse(url=f"/vehicles/{vehicle.id}/edit{suffix}", status_code=303)

    target_team = db.get(Team, to_team_id)
    if target_team is None or not target_team.is_active or target_team.id == vehicle.team_id:
        flash_message(request, "error", "Selecciona un equipo destino valido.")
        transfer_team = request.query_params.get("team_id")
        suffix = f"?transfer_error=invalid_team"
        if transfer_team:
            suffix = f"?team_id={transfer_team}&transfer_error=invalid_team"
        return RedirectResponse(url=f"/vehicles/{vehicle.id}/edit{suffix}", status_code=303)

    from_team_id = vehicle.team_id
    if from_team_id is None:
        flash_message(request, "error", "El vehiculo no tiene un equipo actual.")
        transfer_team = request.query_params.get("team_id")
        suffix = f"?transfer_error=missing_team"
        if transfer_team:
            suffix = f"?team_id={transfer_team}&transfer_error=missing_team"
        return RedirectResponse(url=f"/vehicles/{vehicle.id}/edit{suffix}", status_code=303)

    transfer = VehicleTransfer(
        vehicle_id=vehicle.id,
        from_team_id=from_team_id,
        to_team_id=target_team.id,
        transferred_by_user_id=current_user.id,
        notes=clean_optional(notes),
    )
    vehicle.team_id = target_team.id
    db.add(transfer)
    db.commit()
    flash_message(request, "success", f"Vehiculo transferido correctamente a {target_team.name}.")

    query = {"transfer_success": "1", "transfer_team_name": target_team.name}
    transfer_team = request.query_params.get("team_id")
    if transfer_team:
        query["team_id"] = transfer_team
    return RedirectResponse(url=f"/vehicles/{vehicle.id}/edit?{urlencode(query)}", status_code=303)


@app.post("/vehicles/{vehicle_id}/deliver")
def deliver_vehicle(
    vehicle_id: int,
    request: Request,
    borrower_name: str = Form(...),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    instagram: str | None = Form(None),
    delivery_operator: str | None = Form(None),
    delivery_mileage: int = Form(...),
    fuel_level: str | None = Form(None),
    notes: str | None = Form(None),
    agreement_signed: bool = Form(False),
    loan_category: str | None = Form(None),
    delivery_files: list[UploadFile] = File(default=[]),
    agreement_file: UploadFile | None = File(None),
    redirect_to: str | None = Form(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    vehicle = db.scalar(
        select(Vehicle)
        .options(
            selectinload(Vehicle.team)
            .selectinload(Team.config)
            .selectinload(TeamConfig.default_loan_category),
        )
        .where(Vehicle.id == vehicle_id)
    )
    if vehicle is None or vehicle.status != "available":
        flash_message(request, "error", "No fue posible registrar la entrega.")
        return RedirectResponse(url="/vehicles", status_code=303)

    if vehicle.team_id is None:
        default_team = get_default_team(db)
        vehicle.team_id = default_team.id
        vehicle.team = default_team

    team_config = load_team_config(db, vehicle.team)
    default_category_name = None
    if team_config and team_config.default_loan_category and team_config.default_loan_category.is_active:
        default_category_name = team_config.default_loan_category.name
    chosen_category = clean_optional(loan_category) or default_category_name

    if team_config and team_config.requires_delivery_photos and not has_uploaded_files(delivery_files):
        flash_message(
            request,
            "warning",
            "Este equipo requiere fotos de entrega. Adjunta al menos un archivo de entrega.",
        )
        redirect_url = f"/vehicles/{vehicle.id}"
        if redirect_to == "operator":
            redirect_url = f"/operator/vehicles/{vehicle.id}"
        return RedirectResponse(url=redirect_url, status_code=303)

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
        loan_category=chosen_category,
    )
    vehicle.status = "assigned"
    db.add(loan)
    db.flush()
    seed_loan_checklist_items(db, loan)
    add_loan_assets(db, loan, delivery_files, "delivery")
    add_loan_assets(db, loan, [agreement_file] if agreement_file else [], "agreement")
    db.commit()
    if redirect_to == "operator":
        flash_message(request, "success", f"Entrega registrada para {vehicle.plate}.")
        return RedirectResponse(url=f"/operator/vehicles/{vehicle.id}", status_code=303)
    flash_message(request, "success", f"Entrega registrada para {vehicle.plate}.")
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
        flash_message(request, "error", "El prestamo no existe.")
        return RedirectResponse(url="/vehicles", status_code=303)

    team_config = load_team_config(db, loan.vehicle.team)
    default_category_name = None
    if team_config and team_config.default_loan_category and team_config.default_loan_category.is_active:
        default_category_name = team_config.default_loan_category.name

    return templates.TemplateResponse(
        "loans/edit_delivery.html",
        {
            "request": request,
            "loan": loan,
            "loan_categories": load_loan_categories(db),
            "team_default_loan_category_name": default_category_name,
            "selected_loan_category_name": loan.loan_category or default_category_name,
        },
    )


@app.post("/loans/{loan_id}/edit-delivery")
def update_delivery(
    loan_id: int,
    request: Request,
    borrower_name: str = Form(...),
    phone: str | None = Form(None),
    email: str | None = Form(None),
    instagram: str | None = Form(None),
    delivery_operator: str | None = Form(None),
    delivery_mileage: int = Form(...),
    fuel_level: str | None = Form(None),
    notes: str | None = Form(None),
    agreement_signed: bool = Form(False),
    loan_category: str | None = Form(None),
    delivery_files: list[UploadFile] = File(default=[]),
    agreement_file: UploadFile | None = File(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    loan = db.scalar(
        select(Loan)
        .options(
            selectinload(Loan.assets),
            selectinload(Loan.vehicle)
            .selectinload(Vehicle.team)
            .selectinload(Team.config)
            .selectinload(TeamConfig.default_loan_category),
        )
        .where(Loan.id == loan_id)
    )
    if loan is None:
        flash_message(request, "error", "El prestamo no existe.")
        return RedirectResponse(url="/vehicles", status_code=303)

    team_config = load_team_config(db, loan.vehicle.team)
    default_category_name = None
    if team_config and team_config.default_loan_category and team_config.default_loan_category.is_active:
        default_category_name = team_config.default_loan_category.name
    if team_config and team_config.requires_delivery_photos and not (
        has_uploaded_files(delivery_files)
        or any(asset.category == "delivery" for asset in loan.assets)
    ):
        flash_message(
            request,
            "warning",
            "Este equipo requiere fotos de entrega. Adjunta al menos un archivo de entrega.",
        )
        return RedirectResponse(url=f"/vehicles/{loan.vehicle_id}/edit", status_code=303)

    loan.borrower_name = borrower_name.strip()
    loan.phone = clean_optional(phone)
    loan.email = clean_optional(email)
    loan.instagram = clean_optional(instagram)
    loan.delivery_operator = clean_optional(delivery_operator)
    loan.delivery_mileage = delivery_mileage
    loan.fuel_level = clean_optional(fuel_level)
    loan.notes = clean_optional(notes)
    loan.agreement_signed = agreement_signed
    loan.loan_category = clean_optional(loan_category) or default_category_name
    add_loan_assets(db, loan, delivery_files, "delivery")
    add_loan_assets(db, loan, [agreement_file] if agreement_file else [], "agreement")
    db.commit()
    flash_message(request, "success", f"Entrega actualizada para {loan.borrower_name}.")
    return RedirectResponse(url=f"/vehicles/{loan.vehicle_id}", status_code=303)


@app.post("/loans/{loan_id}/category")
def update_loan_category(
    loan_id: int,
    request: Request,
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
        flash_message(request, "error", "El prestamo no existe.")
        return RedirectResponse(url="/vehicles", status_code=303)

    loan.loan_category = clean_optional(loan_category)
    seed_ensure_loan_categories(db)
    db.commit()
    flash_message(request, "success", f"Categoria actualizada para el prestamo de {loan.borrower_name}.")
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
    request: Request,
    return_mileage: int = Form(...),
    return_operator: str | None = Form(None),
    fuel_level: str | None = Form(None),
    return_has_issues: bool = Form(False),
    notes: str | None = Form(None),
    return_files: list[UploadFile] = File(default=[]),
    return_issue_files: list[UploadFile] = File(default=[]),
    redirect_to: str | None = Form(None),
    team_id: str | None = Form(None),
    current_user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    loan = db.scalar(
        select(Loan)
        .options(
            selectinload(Loan.assets),
            selectinload(Loan.vehicle)
            .selectinload(Vehicle.team)
            .selectinload(Team.config)
            .selectinload(TeamConfig.default_loan_category),
        )
        .where(Loan.id == loan_id)
    )
    if loan is None:
        flash_message(request, "error", "El prestamo no existe.")
        return RedirectResponse(url="/vehicles", status_code=303)

    team_config = load_team_config(db, loan.vehicle.team)
    if team_config and team_config.requires_return_photos and not has_uploaded_files(return_files):
        flash_message(
            request,
            "warning",
            "Este equipo requiere fotos de devolucion. Adjunta al menos un archivo de devolucion.",
        )
        redirect_target = "/operator/vehicles" if redirect_to == "operator" else "/vehicles"
        if team_id:
            redirect_target = f"{redirect_target}?team_id={team_id}"
        return RedirectResponse(url=redirect_target, status_code=303)

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
    redirect_target = "/operator/vehicles" if redirect_to == "operator" else "/vehicles"
    if team_id:
        redirect_target = f"{redirect_target}?team_id={team_id}"
    flash_message(request, "success", f"Devolucion registrada para {loan.borrower_name}.")
    return RedirectResponse(url=redirect_target, status_code=303)
