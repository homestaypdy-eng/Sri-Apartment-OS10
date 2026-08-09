from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from app.core.config import settings

# pool_pre_ping: tests each connection before use, so a connection the DB
# server already closed (common after any idle timeout, DB restart, or a
# managed-Postgres provider's connection limits) is quietly replaced
# instead of surfacing as a random mid-request error.
# pool_recycle: proactively recycles connections older than 30 minutes —
# some managed database providers (and some network proxies/load
# balancers in front of Postgres) silently drop idle connections sooner
# than pool_pre_ping alone reliably catches; this is the standard fix for
# the classic "server closed the connection unexpectedly" production error.
engine = create_engine(settings.database_url, pool_pre_ping=True, pool_recycle=1800, future=True)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
