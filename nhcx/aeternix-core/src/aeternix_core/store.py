"""Persistence: cases, every message exchanged with NHCX, and a status history."""
from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, ForeignKey, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def _now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class CaseRow(Base):
    __tablename__ = "cases"
    case_id: Mapped[str] = mapped_column(String, primary_key=True)
    status: Mapped[str] = mapped_column(String)
    case_number: Mapped[str] = mapped_column(String, index=True)   # CLN on NHCX messages
    data: Mapped[dict] = mapped_column(JSON)          # ClaimCase (internal model) as JSON
    open_query: Mapped[dict | None] = mapped_column(JSON, nullable=True)   # insurer's pending document query
    last_decision: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)


class ExchangeRow(Base):
    """One HTTP message to or from NHCX."""
    __tablename__ = "exchanges"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    workflow: Mapped[str] = mapped_column(String)       # preauth | claim | paymentnotice
    direction: Mapped[str] = mapped_column(String)      # outbound | inbound
    action: Mapped[str] = mapped_column(String)         # submit | on_submit | request | on_request
    api_call_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    correlation_id: Mapped[str] = mapped_column(String, index=True)
    status: Mapped[str] = mapped_column(String)         # sending | acked | failed | received
    http_status: Mapped[int | None] = mapped_column(Integer, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    body: Mapped[dict] = mapped_column(JSON)            # exactly what went over the wire
    # Readable copy {headers, fhir}: what we encrypted (outbound) or decrypted (inbound).
    # NOTE(production): this is patient data at rest - decide on DB encryption/retention.
    plain: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def summary(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "workflow", "direction", "action", "api_call_id",
                                              "correlation_id", "status", "http_status", "error",
                                              "created_at")}


class EventRow(Base):
    __tablename__ = "case_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    case_id: Mapped[str] = mapped_column(ForeignKey("cases.case_id"), index=True)
    from_status: Mapped[str | None] = mapped_column(String, nullable=True)
    to_status: Mapped[str] = mapped_column(String)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)


class PatientRow(Base):
    """A hospital patient. One ABHA number belongs to exactly one patient
    (M1 TAGGING_UNIQUEPATIENTID_UNIQUEABHANUMBER), enforced by the unique index."""
    __tablename__ = "patients"
    patient_id: Mapped[str] = mapped_column(String, primary_key=True)
    abha_number: Mapped[str | None] = mapped_column(String, unique=True, index=True, nullable=True)
    abha_address: Mapped[str | None] = mapped_column(String, nullable=True)
    name: Mapped[str] = mapped_column(String)
    gender: Mapped[str | None] = mapped_column(String, nullable=True)
    dob: Mapped[str | None] = mapped_column(String, nullable=True)
    mobile: Mapped[str | None] = mapped_column(String, nullable=True)   # as ABDM returns it (masked)
    address: Mapped[str | None] = mapped_column(Text, nullable=True)
    source: Mapped[str] = mapped_column(String)          # created | verified | scan_share
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    def summary(self) -> dict:
        return {k: getattr(self, k) for k in ("patient_id", "abha_number", "abha_address", "name", "gender",
                                              "dob", "mobile", "source", "created_at", "updated_at")}


class ShareRow(Base):
    """One Scan & Share profile push from the ABDM gateway, and the OPD token we gave back."""
    __tablename__ = "scan_shares"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    request_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    hip_id: Mapped[str | None] = mapped_column(String, nullable=True)
    counter_id: Mapped[str | None] = mapped_column(String, nullable=True)
    abha_address: Mapped[str | None] = mapped_column(String, nullable=True)
    patient_id: Mapped[str | None] = mapped_column(String, nullable=True)
    token_number: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)          # received | acknowledged | ack_failed
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    # NOTE(production): patient demographics at rest - decide on DB encryption/retention.
    profile: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)

    def summary(self) -> dict:
        return {k: getattr(self, k) for k in ("id", "request_id", "counter_id", "abha_address", "patient_id",
                                              "token_number", "status", "error", "created_at")} | {
            "name": (self.profile or {}).get("name")}


SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str) -> None:
    global SessionLocal
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)


def session() -> Session:
    assert SessionLocal is not None, "init_db() not called"
    return SessionLocal()


def exchange_by_api_call_id(s: Session, api_call_id: str) -> ExchangeRow | None:
    return s.scalar(select(ExchangeRow).where(ExchangeRow.api_call_id == api_call_id))


def outbound_by_correlation(s: Session, correlation_id: str, workflow: str) -> ExchangeRow | None:
    return s.scalar(select(ExchangeRow).where(
        ExchangeRow.correlation_id == correlation_id, ExchangeRow.workflow == workflow,
        ExchangeRow.direction == "outbound").order_by(ExchangeRow.id.desc()))
