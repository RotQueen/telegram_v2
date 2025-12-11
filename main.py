"""Telegram relay bot for connecting customer and executor chats per project.

Deployment on Railway (long polling):
1. Set environment variables:
   - BOT_TOKEN: Telegram bot token
   - ADMIN_USER_ID: (optional) Telegram user ID of the admin (defaults to 5386753143)
   - DATABASE_PATH: (optional) SQLite path, defaults to './projects.db'
2. Install dependencies: `pip install -r requirements.txt`
3. Run the bot: `python main.py`

The bot uses long polling (no webhooks) to suit Railway free tier.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from telegram import Message, Update
from telegram.constants import ParseMode
from telegram.ext import (
    AIORateLimiter,
    Application,
    ApplicationBuilder,
    CallbackContext,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import ADMIN_USER_ID, BOT_TOKEN, DATABASE_PATH
from storage import Project, SQLiteProjectStorage

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

storage = SQLiteProjectStorage(DATABASE_PATH)


def is_admin(user_id: Optional[int]) -> bool:
    return user_id == ADMIN_USER_ID


def admin_only(func):  # type: ignore[override]
    async def wrapper(update: Update, context: CallbackContext):
        user_id = update.effective_user.id if update.effective_user else None
        if not is_admin(user_id):
            await update.effective_message.reply_text("Эта команда доступна только администратору.")
            return
        return await func(update, context)

    return wrapper


async def create_project(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /create_project <slug>")
        return
    slug = context.args[0]
    chat_id = update.effective_chat.id
    try:
        project = storage.create_project(slug, executor_chat_id=chat_id)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"Проект {project.slug} создан. Теперь зайдите в чат с заказчиками и выполните /bind_customer {project.slug}."
    )


async def bind_customer(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /bind_customer <slug>")
        return
    slug = context.args[0]
    chat_id = update.effective_chat.id
    try:
        project = storage.bind_customer(slug, customer_chat_id=chat_id)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(f"Проект {project.slug}: чат заказчика успешно привязан.")


async def project_info(update: Update, context: CallbackContext) -> None:
    chat_id = update.effective_chat.id
    project = storage.find_by_chat(chat_id)
    if not project:
        await update.message.reply_text("Этот чат не привязан ни к одному проекту.")
        return
    if project.executor_chat_id == chat_id:
        chat_type = "чат исполнителей"
    else:
        chat_type = "чат заказчиков"
    status = "активен" if project.is_active else "неактивен"
    await update.message.reply_text(f"Проект: {project.slug}\nТип чата: {chat_type}\nСтатус: {status}")


async def list_projects(update: Update, context: CallbackContext) -> None:
    projects = storage.list_projects()
    if not projects:
        await update.message.reply_text("Проекты отсутствуют.")
        return
    lines = []
    for project in projects:
        customer_status = "привязан" if project.customer_chat_id else "не привязан"
        executor_status = "привязан" if project.executor_chat_id else "не привязан"
        active_status = "активен" if project.is_active else "неактивен"
        lines.append(
            f"{project.slug}: заказчик {customer_status}, исполнитель {executor_status}, статус {active_status}"
        )
    await update.message.reply_text("\n".join(lines))


async def unlink_project(update: Update, context: CallbackContext) -> None:
    if not context.args:
        await update.message.reply_text("Использование: /unlink_project <slug>")
        return
    slug = context.args[0]
    chat_id = update.effective_chat.id
    try:
        project = storage.unlink_chat(slug, chat_id=chat_id)
    except ValueError as exc:
        await update.message.reply_text(str(exc))
        return
    await update.message.reply_text(
        f"Проект {project.slug}: чат отвязан или проект деактивирован. Активен: {'да' if project.is_active else 'нет'}."
    )


action_labels = {
    "customer": "👤 Клиент:",
    "executor": "🧑‍🎨 Команда:",
}


async def relay_text(project: Project, message: Message, target_chat_id: int, role: str) -> None:
    prefix = action_labels[role]
    text = message.text or message.caption or ""
    await message.get_bot().send_message(chat_id=target_chat_id, text=f"{prefix} {text}")


async def relay_media(project: Project, message: Message, target_chat_id: int, role: str) -> None:
    bot = message.get_bot()
    caption = message.caption
    prefix = action_labels[role]
    caption_prefixed = f"{prefix} {caption}" if caption else None

    if message.photo:
        file_id = message.photo[-1].file_id
        await bot.send_photo(chat_id=target_chat_id, photo=file_id, caption=caption_prefixed)
    elif message.document:
        await bot.send_document(chat_id=target_chat_id, document=message.document.file_id, caption=caption_prefixed)
    elif message.voice:
        await bot.send_voice(chat_id=target_chat_id, voice=message.voice.file_id, caption=caption_prefixed)
    elif message.audio:
        await bot.send_audio(chat_id=target_chat_id, audio=message.audio.file_id, caption=caption_prefixed)
    elif message.video:
        await bot.send_video(chat_id=target_chat_id, video=message.video.file_id, caption=caption_prefixed)
    else:
        # If media type is not supported, fallback to sending text.
        text = message.text or caption or "(неизвестный тип сообщения)"
        await bot.send_message(chat_id=target_chat_id, text=f"{prefix} {text}")
        return


async def relay_message(update: Update, context: CallbackContext) -> None:
    message = update.effective_message
    if not message or not update.effective_chat:
        return
    # Prevent loops: ignore bot's own messages.
    if message.from_user and message.from_user.is_bot:
        return

    project = storage.find_by_chat(update.effective_chat.id)
    if not project or not project.is_active:
        return

    source_chat_id = update.effective_chat.id
    if project.executor_chat_id == source_chat_id and project.customer_chat_id:
        target_chat_id = project.customer_chat_id
        role = "executor"
    elif project.customer_chat_id == source_chat_id and project.executor_chat_id:
        target_chat_id = project.executor_chat_id
        role = "customer"
    else:
        return

    try:
        if message.text and not message.photo and not message.document and not message.voice and not message.audio and not message.video:
            await relay_text(project, message, target_chat_id, role)
        else:
            await relay_media(project, message, target_chat_id, role)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Ошибка при пересылке сообщения: %s", exc)
        try:
            await message.reply_text("Не удалось переслать сообщение. Проверьте настройки бота.")
        except Exception:  # noqa: BLE001
            logger.exception("Не удалось отправить сообщение об ошибке")


async def start(update: Update, context: CallbackContext) -> None:
    await update.message.reply_text(
        "Бот-посредник активен. Используйте /project_info чтобы узнать привязку чата."
    )


@admin_only
async def admin_create_project(update: Update, context: CallbackContext) -> None:
    await create_project(update, context)


@admin_only
async def admin_bind_customer(update: Update, context: CallbackContext) -> None:
    await bind_customer(update, context)


@admin_only
async def admin_list_projects(update: Update, context: CallbackContext) -> None:
    await list_projects(update, context)


@admin_only
async def admin_unlink_project(update: Update, context: CallbackContext) -> None:
    await unlink_project(update, context)


def build_application() -> Application:
    application = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .rate_limiter(AIORateLimiter())
        .parse_mode(ParseMode.HTML)
        .build()
    )

    # Command handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("project_info", project_info))
    application.add_handler(CommandHandler("create_project", admin_create_project))
    application.add_handler(CommandHandler("bind_customer", admin_bind_customer))
    application.add_handler(CommandHandler("list_projects", admin_list_projects))
    application.add_handler(CommandHandler("unlink_project", admin_unlink_project))

    # Message handler for all content types
    application.add_handler(
        MessageHandler(
            filters.ALL
            & ~filters.COMMAND
            & (filters.TEXT
               | filters.Document.ALL
               | filters.PHOTO
               | filters.VOICE
               | filters.AUDIO
               | filters.VIDEO),
            relay_message,
        )
    )

    return application


async def main() -> None:
    application = build_application()
    await application.initialize()
    await application.start()
    logger.info("Bot started with long polling")
    await application.updater.start_polling(drop_pending_updates=True)

    # Keep running until interrupted
    await asyncio.Event().wait()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped")
