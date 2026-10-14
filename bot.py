import asyncio
import logging
import os
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message,
    CallbackQuery,
    Update,
    InlineKeyboardMarkup,
    InlineKeyboardButton,
    FSInputFile,
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from yt_dlp import YoutubeDL


# ---------------- CONFIG ----------------

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
WEBHOOK_PATH = f"/webhook/{TOKEN}"

MAX_FILE_SIZE = 49 * 1024 * 1024

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()

# Ссылки пользователей
links = {}


# ---------------- DOWNLOAD ----------------

def download(url: str, mode: str, folder: str) -> Path:
    output_template = str(Path(folder) / "download.%(ext)s")

    options = {
        "outtmpl": output_template,
        "noplaylist": True,
        "quiet": True,
        "no_warnings": False,
        "socket_timeout": 30,
        "retries": 2,
        "fragment_retries": 2,
        "max_filesize": MAX_FILE_SIZE,
        "format": (
            "best[ext=mp4]/best"
            if mode == "video"
            else "bestaudio/best"
        ),
    }

    if mode == "audio":
        options["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": "192",
            }
        ]

    with YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)

    downloaded_files = [
        file
        for file in Path(folder).iterdir()
        if file.is_file()
        and not file.name.endswith((".part", ".ytdl"))
    ]

    if not downloaded_files:
        raise RuntimeError("Скачанный файл не найден")

    # После преобразования аудио выбираем итоговый MP3.
    if mode == "audio":
        mp3_files = [
            file for file in downloaded_files
            if file.suffix.lower() == ".mp3"
        ]

        if mp3_files:
            return max(mp3_files, key=lambda file: file.stat().st_size)

    return max(downloaded_files, key=lambda file: file.stat().st_size)


# ---------------- FASTAPI ----------------

@asynccontextmanager
async def lifespan(app: FastAPI):
    webhook_url = BASE_URL + WEBHOOK_PATH

    await bot.set_webhook(webhook_url)
    logger.info("Webhook установлен")

    try:
        yield
    finally:
        await bot.delete_webhook()
        await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def home():
    return {"status": "TikTok bot is running"}


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    # Проверяем секретный токен Telegram webhook.
    secret = os.environ.get("WEBHOOK_SECRET")

    if secret:
        received_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )

        if received_secret != secret:
            raise HTTPException(status_code=403, detail="Forbidden")

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}

    except Exception:
        logger.exception("Ошибка обработки webhook")
        raise HTTPException(status_code=500, detail="Internal error")


# ---------------- COMMANDS ----------------

@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 <b>TikTok Downloader</b>\n\n"
        "Отправь общедоступную ссылку на TikTok.\n"
        "Я предложу скачать видео или аудио."
    )


# ---------------- RECEIVE LINK ----------------

@dp.message(F.text)
async def get_link(message: Message):
    url = message.text.strip()

    if not url.startswith(("https://", "http://")):
        await message.

answer("❌ Отправь корректную ссылку на TikTok.")
        return

    from urllib.parse import urlparse

    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    if not (
        hostname == "tiktok.com"
        or hostname.endswith(".tiktok.com")
    ):
        await message.answer("❌ Это не ссылка на TikTok.")
        return

    links[message.from_user.id] = url

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🎬 Видео",
                    callback_data="video",
                ),
                InlineKeyboardButton(
                    text="🎵 Звук MP3",
                    callback_data="audio",
                ),
            ]
        ]
    )

    await message.answer("Что скачать?", reply_markup=keyboard)


# ---------------- DOWNLOAD BUTTONS ----------------

@dp.callback_query(F.data.in_({"video", "audio"}))
async def choose(callback: CallbackQuery):
    url = links.get(callback.from_user.id)

    if not url:
        await callback.answer(
            "Отправь ссылку заново.",
            show_alert=True,
        )
        return

    mode = callback.data

    await callback.answer()
    status_message = await callback.message.answer(
        "⏳ Скачиваю файл. Подожди..."
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            path = await asyncio.to_thread(
                download,
                url,
                mode,
                folder,
            )

            file_size = path.stat().st_size

            if file_size == 0:
                raise RuntimeError("Файл пустой")

            if file_size > MAX_FILE_SIZE:
                await status_message.edit_text(
                    "❌ Файл слишком большой для отправки."
                )
                return

            telegram_file = FSInputFile(path)

            if mode == "audio":
                await callback.message.answer_audio(
                    telegram_file,
                    title=path.stem[:64],
                )
            else:
                await callback.message.answer_video(
                    telegram_file,
                    supports_streaming=True,
                )

        await status_message.edit_text(
            "✅ Готово! Отправь следующую ссылку."
        )

    except Exception as error:
        logger.exception(
            "Ошибка скачивания (%s): %s",
            type(error).__name__,
            error,
        )

        try:
            await status_message.edit_text(
                "❌ Не удалось скачать файл.\n\n"
                "Попробуй другую общедоступную ссылку."
            )
        except Exception:
            logger.exception("Не удалось отправить сообщение об ошибке")

    finally:
        links.pop(callback.from_user.id, None)
