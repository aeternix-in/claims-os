from datetime import datetime, timezone

from sqlalchemy import JSON, DateTime, Integer, String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker


def _now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    api_call_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    correlation_id: Mapped[str] = mapped_column(String, index=True)
    workflow_id: Mapped[str] = mapped_column(String)
    request_id: Mapped[str] = mapped_column(String)
    workflow: Mapped[str] = mapped_column(String)        # preauth | claim | paymentnotice
    action: Mapped[str] = mapped_column(String)          # submit | request
    sender_code: Mapped[str] = mapped_column(String)
    recipient_code: Mapped[str] = mapped_column(String)
    scenario: Mapped[str] = mapped_column(String)
    status: Mapped[str] = mapped_column(String)          # business state
    callback_status: Mapped[str] = mapped_column(String, default="pending")
    callback_attempts: Mapped[int] = mapped_column(Integer, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    request_headers: Mapped[dict] = mapped_column(JSON)
    request_fhir: Mapped[dict] = mapped_column(JSON)
    response_headers: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    response_fhir: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now, onupdate=_now)

    def summary(self) -> dict:
        return {c.name: getattr(self, c.name) for c in self.__table__.columns}


SessionLocal: sessionmaker[Session] | None = None


def init_db(url: str) -> None:
    global SessionLocal
    engine = create_engine(url, connect_args={"check_same_thread": False} if url.startswith("sqlite") else {})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(engine, expire_on_commit=False)


def session() -> Session:
    assert SessionLocal is not None, "init_db() not called"
    return SessionLocal()


def get_by_api_call_id(s: Session, api_call_id: str) -> Transaction | None:
    return s.scalar(select(Transaction).where(Transaction.api_call_id == api_call_id))
