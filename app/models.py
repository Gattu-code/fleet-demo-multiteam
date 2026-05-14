from datetime import UTC, datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .database import Base


def utc_now():
    return datetime.now(UTC).replace(tzinfo=None)


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=True)
    brand: Mapped[str] = mapped_column(String(80), nullable=False)
    model: Mapped[str] = mapped_column(String(80), nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=True)
    plate: Mapped[str] = mapped_column(String(30), unique=True, index=True, nullable=False)
    color: Mapped[str] = mapped_column(String(50), nullable=True)
    reference_image_path: Mapped[str] = mapped_column(String(255), nullable=True)
    has_open_issue: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    status: Mapped[str] = mapped_column(String(20), default="available", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    loans: Mapped[list["Loan"]] = relationship(
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="Loan.created_at.desc()",
    )
    transfer_history: Mapped[list["VehicleTransfer"]] = relationship(
        back_populates="vehicle",
        cascade="all, delete-orphan",
        order_by="VehicleTransfer.created_at.desc()",
    )
    team: Mapped["Team"] = relationship(back_populates="vehicles")


class Loan(Base):
    __tablename__ = "loans"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=True)
    borrower_name: Mapped[str] = mapped_column(String(120), nullable=False)
    phone: Mapped[str] = mapped_column(String(40), nullable=True)
    email: Mapped[str] = mapped_column(String(120), nullable=True)
    instagram: Mapped[str] = mapped_column(String(120), nullable=True)
    loan_category: Mapped[str] = mapped_column(String(80), nullable=True)
    delivery_operator: Mapped[str] = mapped_column(String(120), nullable=True)
    return_operator: Mapped[str] = mapped_column(String(120), nullable=True)
    delivery_mileage: Mapped[int] = mapped_column(Integer, nullable=False)
    return_mileage: Mapped[int] = mapped_column(Integer, nullable=True)
    fuel_level: Mapped[str] = mapped_column(String(40), nullable=True)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    agreement_signed: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    return_has_issues: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    delivery_photo_path: Mapped[str] = mapped_column(String(255), nullable=True)
    return_photo_path: Mapped[str] = mapped_column(String(255), nullable=True)
    delivered_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    returned_at: Mapped[datetime] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    vehicle: Mapped[Vehicle] = relationship(back_populates="loans")
    team: Mapped["Team"] = relationship(back_populates="loans")
    assets: Mapped[list["LoanAsset"]] = relationship(
        back_populates="loan",
        cascade="all, delete-orphan",
        order_by="LoanAsset.created_at.desc()",
    )


class LoanAsset(Base):
    __tablename__ = "loan_assets"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    loan_id: Mapped[int] = mapped_column(ForeignKey("loans.id"), nullable=False)
    category: Mapped[str] = mapped_column(String(30), nullable=False)
    file_path: Mapped[str] = mapped_column(String(255), nullable=False)
    original_filename: Mapped[str] = mapped_column(String(255), nullable=True)
    content_type: Mapped[str] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    loan: Mapped[Loan] = relationship(back_populates="assets")


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    vehicles: Mapped[list[Vehicle]] = relationship(back_populates="team")
    loans: Mapped[list[Loan]] = relationship(back_populates="team")
    users: Mapped[list["User"]] = relationship(back_populates="team")
    config: Mapped["TeamConfig"] = relationship(
        back_populates="team",
        cascade="all, delete-orphan",
        single_parent=True,
        uselist=False,
    )
    transfers_from: Mapped[list["VehicleTransfer"]] = relationship(
        back_populates="from_team",
        foreign_keys="VehicleTransfer.from_team_id",
    )
    transfers_to: Mapped[list["VehicleTransfer"]] = relationship(
        back_populates="to_team",
        foreign_keys="VehicleTransfer.to_team_id",
    )


class LoanCategory(Base):
    __tablename__ = "loan_categories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)


class TeamConfig(Base):
    __tablename__ = "team_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), unique=True, nullable=False)
    allows_transfers: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    requires_delivery_photos: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    requires_return_photos: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    default_loan_category_id: Mapped[int] = mapped_column(ForeignKey("loan_categories.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, onupdate=utc_now, nullable=False)

    team: Mapped["Team"] = relationship(back_populates="config")
    default_loan_category: Mapped["LoanCategory"] = relationship(foreign_keys=[default_loan_category_id])


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(40), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    team: Mapped["Team"] = relationship(back_populates="users")
    vehicle_transfers: Mapped[list["VehicleTransfer"]] = relationship(
        back_populates="transferred_by_user",
    )


class VehicleTransfer(Base):
    __tablename__ = "vehicle_transfers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    vehicle_id: Mapped[int] = mapped_column(ForeignKey("vehicles.id"), nullable=False)
    from_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    to_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    transferred_by_user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False)
    notes: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utc_now, nullable=False)

    vehicle: Mapped[Vehicle] = relationship(back_populates="transfer_history")
    from_team: Mapped["Team"] = relationship(foreign_keys=[from_team_id], back_populates="transfers_from")
    to_team: Mapped["Team"] = relationship(foreign_keys=[to_team_id], back_populates="transfers_to")
    transferred_by_user: Mapped[User] = relationship(back_populates="vehicle_transfers")
