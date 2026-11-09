from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from app.config import settings


connect_args = {"check_same_thread": False} if settings.database_url.startswith("sqlite") else {}
engine = create_engine(settings.database_url, connect_args=connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def init_db(bind=None):
    from app import models  # noqa: F401
    database = bind or engine
    Base.metadata.create_all(bind=database)
    columns = {column["name"] for column in inspect(database).get_columns("memories")}
    if "confirmed" not in columns:
        with database.begin() as connection:
            connection.execute(text("ALTER TABLE memories ADD COLUMN confirmed BOOLEAN NOT NULL DEFAULT 1"))
