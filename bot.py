import asyncio
import logging
import os
from datetime import datetime
from typing import Iterable
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, select
from sqlalchemy.engine.url import make_url
from sqlalchemy.ext.asyncio import AsyncAttrs, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# --------------------
# Database setup
# --------------------


class Base(AsyncAttrs, DeclarativeBase):
    pass


class Pet(Base):
    __tablename__ = "pets"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=datetime.utcnow, nullable=False
    )

    weights: Mapped[list["WeightEntry"]] = relationship(
        back_populates="pet",
        cascade="all, delete-orphan",
        order_by="WeightEntry.timestamp",
    )
    treatments: Mapped[list["TreatmentEntry"]] = relationship(
        back_populates="pet",
        cascade="all, delete-orphan",
        order_by="TreatmentEntry.timestamp",
    )
    vaccines: Mapped[list["VaccineEntry"]] = relationship(
        back_populates="pet",
        cascade="all, delete-orphan",
        order_by="VaccineEntry.timestamp",
    )
    events: Mapped[list["EventEntry"]] = relationship(
        back_populates="pet",
        cascade="all, delete-orphan",
        order_by="EventEntry.timestamp",
    )


class EntryBase:
    id: Mapped[int] = mapped_column(primary_key=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=False), default=datetime.utcnow, nullable=False
    )


class WeightEntry(EntryBase, Base):
    __tablename__ = "weights"

    pet_id: Mapped[int] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"))
    value: Mapped[float] = mapped_column(Float, nullable=False)

    pet: Mapped[Pet] = relationship(back_populates="weights")


class TreatmentEntry(EntryBase, Base):
    __tablename__ = "treatments"

    pet_id: Mapped[int] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text, nullable=False)

    pet: Mapped[Pet] = relationship(back_populates="treatments")


class VaccineEntry(EntryBase, Base):
    __tablename__ = "vaccines"

    pet_id: Mapped[int] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text, nullable=False)

    pet: Mapped[Pet] = relationship(back_populates="vaccines")


class EventEntry(EntryBase, Base):
    __tablename__ = "events"

    pet_id: Mapped[int] = mapped_column(ForeignKey("pets.id", ondelete="CASCADE"))
    description: Mapped[str] = mapped_column(Text, nullable=False)

    pet: Mapped[Pet] = relationship(back_populates="events")


def sanitize_db_url(url: str) -> str:
    """Remove query parameters incompatible with the async driver."""

    parsed_url = urlparse(url)
    query = dict(parse_qsl(parsed_url.query, keep_blank_values=True))
    query.pop("channel_binding", None)
    new_query = urlencode(query)
    return urlunparse(parsed_url._replace(query=new_query))


def _make_async_url(url: str) -> tuple[str, dict]:
    url_obj = make_url(sanitize_db_url(url))
    sslmode = url_obj.query.get("sslmode")

    query = dict(url_obj.query)
    query.pop("sslmode", None)

    ssl_required = bool(sslmode and sslmode.lower() != "disable")

    async_url = str(
        url_obj.set(drivername="postgresql+asyncpg", query=query)
        if url_obj.drivername.startswith("postgres")
        else url_obj
    )

    connect_args = {"ssl": ssl_required} if ssl_required else {}

    return async_url, connect_args


database_url = os.environ.get("DATABASE_URL")
if not database_url:
    raise RuntimeError("DATABASE_URL не задан. Установите переменную окружения.")


async_url, connect_args = _make_async_url(database_url)

engine = create_async_engine(
    async_url, echo=False, future=True, connect_args=connect_args
)
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


# --------------------
# Telegram UI helpers
# --------------------


MAIN_MENU = "MAIN_MENU"
ADD_PET = "ADD_PET"
LIST_PETS = "LIST_PETS"
PET_INFO = "PET_INFO"
ADD_WEIGHT = "ADD_WEIGHT"
ADD_CARE = "ADD_CARE"
ADD_VACCINE = "ADD_VACCINE"
ADD_EVENT = "ADD_EVENT"


def main_menu_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        [
            [InlineKeyboardButton("Добавить питомца", callback_data=ADD_PET)],
            [InlineKeyboardButton("Список питомцев", callback_data=LIST_PETS)],
            [InlineKeyboardButton("Информация о питомце", callback_data=PET_INFO)],
            [InlineKeyboardButton("Добавить вес", callback_data=ADD_WEIGHT)],
            [InlineKeyboardButton("Добавить обработку", callback_data=ADD_CARE)],
            [InlineKeyboardButton("Добавить вакцинацию", callback_data=ADD_VACCINE)],
            [InlineKeyboardButton("Добавить событие", callback_data=ADD_EVENT)],
        ]
    )


def format_entries(entries: Iterable[EntryBase], title: str, formatter=str) -> str:
    entries_list = list(entries)
    if not entries_list:
        return f"{title}: пока пусто"

    lines = [
        f"{entry.timestamp.strftime('%Y-%m-%d %H:%M:%S')}: {formatter(entry)}"
        for entry in entries_list
    ]
    return f"{title}:\n" + "\n".join(lines)


async def fetch_pets(session: AsyncSession) -> list[Pet]:
    result = await session.execute(select(Pet).order_by(Pet.name))
    return list(result.scalars().all())


async def ensure_pet(session: AsyncSession, name: str) -> Pet:
    query = await session.execute(select(Pet).where(Pet.name == name))
    pet = query.scalar_one_or_none()
    if pet:
        return pet

    pet = Pet(name=name.strip())
    session.add(pet)
    await session.commit()
    await session.refresh(pet)
    return pet


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    context.user_data.clear()
    await update.message.reply_text(
        "Привет! Я бот дневника питомцев. Выберите действие:",
        reply_markup=main_menu_keyboard(),
    )


async def show_main_menu(update: Update, text: str) -> None:
    if update.callback_query:
        await update.callback_query.edit_message_text(text, reply_markup=main_menu_keyboard())
    else:
        await update.message.reply_text(text, reply_markup=main_menu_keyboard())


async def prompt_for_pet_name(update: Update) -> None:
    await update.callback_query.edit_message_text(
        "Введите имя питомца и отправьте сообщением."
    )


async def send_pet_selection(
    update: Update, action: str, session: AsyncSession, empty_message: str
) -> None:
    pets = await fetch_pets(session)
    if not pets:
        await update.callback_query.edit_message_text(empty_message, reply_markup=main_menu_keyboard())
        return

    buttons: list[list[InlineKeyboardButton]] = []
    for pet in pets:
        buttons.append(
            [InlineKeyboardButton(pet.name, callback_data=f"SELECT|{action}|{pet.id}")]
        )
    buttons.append([InlineKeyboardButton("Назад", callback_data=MAIN_MENU)])
    await update.callback_query.edit_message_text(
        "Выберите питомца:", reply_markup=InlineKeyboardMarkup(buttons)
    )


async def handle_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()

    data = query.data
    async with SessionLocal() as session:
        if data == MAIN_MENU:
            context.user_data.clear()
            await show_main_menu(update, "Выберите действие:")
        elif data == ADD_PET:
            context.user_data.clear()
            context.user_data["state"] = "ADD_PET_NAME"
            await prompt_for_pet_name(update)
        elif data == LIST_PETS:
            pets = await fetch_pets(session)
            if not pets:
                text = "Питомцы ещё не добавлены"
            else:
                text = "Питомцы:\n" + "\n".join(pet.name for pet in pets)
            await query.edit_message_text(text, reply_markup=main_menu_keyboard())
        elif data == PET_INFO:
            context.user_data.clear()
            await send_pet_selection(
                update, "INFO", session, "Питомцы ещё не добавлены"
            )
        elif data in {ADD_WEIGHT, ADD_CARE, ADD_VACCINE, ADD_EVENT}:
            context.user_data.clear()
            await send_pet_selection(update, data, session, "Сначала добавьте питомца")
        elif data.startswith("SELECT|"):
            try:
                _, action, pet_id_str = data.split("|", 2)
                pet_id = int(pet_id_str)
            except ValueError:
                await query.edit_message_text("Некорректные данные", reply_markup=main_menu_keyboard())
                return

            context.user_data["pet_id"] = pet_id
            context.user_data["state"] = action

            prompts = {
                "INFO": "Загружаю информацию...",
                ADD_WEIGHT: "Введите вес питомца в килограммах (например, 5.3)",
                ADD_CARE: "Опишите обработку (препарат, дозировка, причина)",
                ADD_VACCINE: "Укажите вакцинацию (название препарата, дата)",
                ADD_EVENT: "Опишите событие"
            }

            if action == "INFO":
                pet = await session.get(Pet, pet_id)
                if not pet:
                    await query.edit_message_text(
                        "Питомец не найден", reply_markup=main_menu_keyboard()
                    )
                    context.user_data.clear()
                    return

                await session.refresh(pet, attribute_names=["weights", "treatments", "vaccines", "events"])

                lines = [
                    f"Питомец: {pet.name}",
                    format_entries(pet.weights, "Вес", lambda e: f"{e.value} кг"),
                    format_entries(pet.treatments, "Обработки", lambda e: e.description),
                    format_entries(pet.vaccines, "Вакцины", lambda e: e.description),
                    format_entries(pet.events, "События", lambda e: e.description),
                ]
                await query.edit_message_text(
                    "\n\n".join(lines), reply_markup=main_menu_keyboard()
                )
                context.user_data.clear()
                return

            await query.edit_message_text(prompts.get(action, "Введите данные"))
        else:
            await query.edit_message_text("Неизвестная команда", reply_markup=main_menu_keyboard())


async def handle_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    state = context.user_data.get("state")
    if not state:
        await update.message.reply_text(
            "Выберите действие из меню.", reply_markup=main_menu_keyboard()
        )
        return

    text = update.message.text.strip()
    async with SessionLocal() as session:
        if state == "ADD_PET_NAME":
            if not text:
                await update.message.reply_text("Имя не может быть пустым")
                return
            pet = await ensure_pet(session, text)
            await update.message.reply_text(
                f"Питомец {pet.name} добавлен.", reply_markup=main_menu_keyboard()
            )
            context.user_data.clear()
            return

        pet_id = context.user_data.get("pet_id")
        if not pet_id:
            await update.message.reply_text(
                "Сначала выберите питомца через меню.", reply_markup=main_menu_keyboard()
            )
            context.user_data.clear()
            return

        pet = await session.get(Pet, pet_id)
        if not pet:
            await update.message.reply_text(
                "Питомец не найден", reply_markup=main_menu_keyboard()
            )
            context.user_data.clear()
            return

        if state == ADD_WEIGHT:
            try:
                value = float(text.replace(",", "."))
            except ValueError:
                await update.message.reply_text(
                    "Не удалось прочитать вес. Введите число, например 5.3"
                )
                return

            entry = WeightEntry(pet_id=pet.id, value=value)
            session.add(entry)
            await session.commit()
            await update.message.reply_text(
                f"Вес {value} кг сохранён для {pet.name}",
                reply_markup=main_menu_keyboard(),
            )
        elif state == ADD_CARE:
            entry = TreatmentEntry(pet_id=pet.id, description=text)
            session.add(entry)
            await session.commit()
            await update.message.reply_text(
                f"Обработка сохранена для {pet.name}", reply_markup=main_menu_keyboard()
            )
        elif state == ADD_VACCINE:
            entry = VaccineEntry(pet_id=pet.id, description=text)
            session.add(entry)
            await session.commit()
            await update.message.reply_text(
                f"Вакцинация сохранена для {pet.name}", reply_markup=main_menu_keyboard()
            )
        elif state == ADD_EVENT:
            entry = EventEntry(pet_id=pet.id, description=text)
            session.add(entry)
            await session.commit()
            await update.message.reply_text(
                f"Событие сохранено для {pet.name}", reply_markup=main_menu_keyboard()
            )
        else:
            await update.message.reply_text(
                "Неизвестная операция", reply_markup=main_menu_keyboard()
            )

    context.user_data.clear()


def build_application(token: str) -> Application:
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CallbackQueryHandler(handle_callback))
    application.add_handler(
        MessageHandler(filters.TEXT & (~filters.COMMAND), handle_user_message)
    )

    return application


def main() -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN не задан. Установите переменную окружения.")

    asyncio.run(init_db())

    application = build_application(token)
    logger.info("Запуск бота...")

    application.run_polling(close_loop=False)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
