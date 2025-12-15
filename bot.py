import json
import logging
import os
import re
from datetime import datetime
from pathlib import Path

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

DATA_DIR = Path("data")
METADATA_FILE = "pet.json"
DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def slugify(text: str) -> str:
    """Convert a pet name to a file-system-friendly slug."""
    text = text.strip().lower()
    text = re.sub(r"[^a-z0-9]+", "-", text)
    return text.strip("-") or "pet"


def ensure_data_dir() -> None:
    DATA_DIR.mkdir(exist_ok=True)


def pet_path(name: str) -> Path:
    ensure_data_dir()
    return DATA_DIR / slugify(name)


def load_pet(name: str) -> dict | None:
    folder = pet_path(name)
    metadata_file = folder / METADATA_FILE
    if not metadata_file.exists():
        return None
    with metadata_file.open("r", encoding="utf-8") as file:
        return json.load(file)


def save_pet(pet: dict) -> None:
    folder = pet_path(pet["name"])
    folder.mkdir(parents=True, exist_ok=True)
    with (folder / METADATA_FILE).open("w", encoding="utf-8") as file:
        json.dump(pet, file, ensure_ascii=False, indent=2)


def ensure_pet(name: str) -> dict:
    pet = load_pet(name)
    if pet is None:
        pet = {
            "name": name.strip(),
            "created_at": datetime.utcnow().strftime(DATE_FORMAT),
            "weights": [],
            "treatments": [],
            "vaccines": [],
            "events": [],
        }
        save_pet(pet)
    return pet


def record_entry(pet: dict, key: str, value: str) -> None:
    pet[key].append({
        "value": value,
        "timestamp": datetime.utcnow().strftime(DATE_FORMAT),
    })
    save_pet(pet)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = (
        "Привет! Я бот дневника питомцев.\n"
        "Добавь питомца: /addpet Имя\n"
        "Вес: /weight Имя 5.3\n"
        "Обработка: /care Имя обработка\n"
        "Вакцина: /vaccine Имя вакцина\n"
        "Событие: /event Имя событие\n"
        "Список питомцев: /listpets\n"
        "Информация: /petinfo Имя"
    )
    await update.message.reply_text(message)


async def add_pet(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Укажите имя питомца: /addpet Имя")
        return
    name = " ".join(context.args).strip()
    pet = ensure_pet(name)
    await update.message.reply_text(f"Питомец {pet['name']} добавлен. Папка: {pet_path(name)}")


async def add_weight(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /weight Имя 5.3")
        return
    name = " ".join(context.args[:-1])
    weight = context.args[-1]
    pet = ensure_pet(name)
    record_entry(pet, "weights", weight)
    await update.message.reply_text(f"Вес {weight} сохранён для {pet['name']}")


async def add_care(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /care Имя описание")
        return
    name = context.args[0]
    note = " ".join(context.args[1:])
    pet = ensure_pet(name)
    record_entry(pet, "treatments", note)
    await update.message.reply_text(f"Обработка сохранена для {pet['name']}")


async def add_vaccine(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /vaccine Имя вакцина")
        return
    name = context.args[0]
    note = " ".join(context.args[1:])
    pet = ensure_pet(name)
    record_entry(pet, "vaccines", note)
    await update.message.reply_text(f"Вакцинация сохранена для {pet['name']}")


async def add_event(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if len(context.args) < 2:
        await update.message.reply_text("Использование: /event Имя событие")
        return
    name = context.args[0]
    note = " ".join(context.args[1:])
    pet = ensure_pet(name)
    record_entry(pet, "events", note)
    await update.message.reply_text(f"Событие сохранено для {pet['name']}")


async def list_pets(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    ensure_data_dir()
    pets: list[str] = []
    for folder in sorted(DATA_DIR.iterdir()):
        if not folder.is_dir():
            continue
        metadata_file = folder / METADATA_FILE
        if metadata_file.exists():
            with metadata_file.open("r", encoding="utf-8") as file:
                data = json.load(file)
                pets.append(data.get("name", folder.name))
        else:
            pets.append(folder.name)

    if not pets:
        await update.message.reply_text("Питомцы ещё не добавлены")
        return

    await update.message.reply_text("Питомцы:\n" + "\n".join(pets))


def format_entries(entries: list[dict], title: str) -> str:
    if not entries:
        return f"{title}: пока пусто"
    lines = [f"{entry['timestamp']}: {entry['value']}" for entry in entries]
    return f"{title}:\n" + "\n".join(lines)


async def pet_info(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not context.args:
        await update.message.reply_text("Укажите имя: /petinfo Имя")
        return
    name = " ".join(context.args)
    pet = load_pet(name)
    if pet is None:
        await update.message.reply_text("Питомец не найден. Сначала добавьте его через /addpet")
        return
    parts = [
        f"Питомец: {pet['name']}",
        format_entries(pet.get("weights", []), "Вес"),
        format_entries(pet.get("treatments", []), "Обработки"),
        format_entries(pet.get("vaccines", []), "Вакцины"),
        format_entries(pet.get("events", []), "События"),
    ]
    await update.message.reply_text("\n\n".join(parts))


def build_application(token: str) -> Application:
    application = Application.builder().token(token).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("addpet", add_pet))
    application.add_handler(CommandHandler("weight", add_weight))
    application.add_handler(CommandHandler("care", add_care))
    application.add_handler(CommandHandler("vaccine", add_vaccine))
    application.add_handler(CommandHandler("event", add_event))
    application.add_handler(CommandHandler("listpets", list_pets))
    application.add_handler(CommandHandler("petinfo", pet_info))

    return application


def main() -> None:
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        raise RuntimeError("TELEGRAM_TOKEN не задан. Установите переменную окружения.")

    application = build_application(token)
    logger.info("Запуск бота...")

    application.run_polling(close_loop=False)


if __name__ == "__main__":
    try:
        main()
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен")
