import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.ext.declarative import declarative_base

# Читаем URL базы данных из переменной окружения
# Для локальной разработки используем SQLite (по умолчанию)
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./game.db")

# Настройка движка в зависимости от типа базы данных
if DATABASE_URL.startswith("postgresql"):
    # Для Neon / PostgreSQL
    engine = create_engine(
        DATABASE_URL,
        pool_pre_ping=True,    # Проверяет соединение перед использованием
        pool_recycle=300,      # Пересоздаёт соединения каждые 5 минут
        pool_size=5,           # Максимум соединений в пуле
        max_overflow=0,        # Не создавать дополнительные соединения
    )
else:
    # Для SQLite (локальная разработка)
    engine = create_engine(
        DATABASE_URL,
        connect_args={"check_same_thread": False}
    )

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

Base = declarative_base()

def get_db():
    """Генератор для получения сессии базы данных."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
