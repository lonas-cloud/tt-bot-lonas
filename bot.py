import asyncio
import logging
import os
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager
from urllib.parse import urlparse

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


logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
WEBHOOK_PATH = f"/webhook/{TOKEN}"
MAX_FILE_SIZE = 45 * 1024 * 1024

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML),
)

dp = Dispatcher()
links = {}


def download(url: str, mode: str, folder: str) -> Path:
    options = {
        "outtmpl": str(Path(folder) / "download.%(ext)s"),
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
        options["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    with YoutubeDL(options) as ydl:
        ydl.extract_info(url, download=True)

    files = [
        file
        for file in Path(folder).iterdir()
        if file.is_file()
        and not file.name.endswith((".part", ".ytdl"))
    ]

    if not files:
        raise RuntimeError("Downloaded file not found")

    if mode == "audio":
        mp3_files = [
            file for file in files
            if file.suffix.lower() == ".mp3"
        ]
        if mp3_files:
            return max(mp3_files, key=lambda file: file.stat().st_size)

    return max(files, key=lambda file: file.stat().st_size)


@asynccontextmanager
async def lifespan(app: FastAPI):
    secret = os.environ.get("WEBHOOK_SECRET")
    await bot.set_webhook(
        BASE_URL + WEBHOOK_PATH,
        secret_token=secret,
    )
    logger.info("Webhook ÑÑÑÐ°Ð½Ð¾Ð²Ð»ÐµÐ½")

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
    secret = os.environ.get("WEBHOOK_SECRET")

    if secret:
        received = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token"
        )
        if received != secret:
            raise HTTPException(status_code=403, detail="Forbidden")

    try:
        data = await request.json()
        update = Update.model_validate(data, context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
    except Exception:
        logger.exception("ÐÑÐ¸Ð±ÐºÐ° Ð¾Ð±ÑÐ°Ð±Ð¾ÑÐºÐ¸ webhook")
        raise HTTPException(status_code=500, detail="Internal error")


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "ð <b>TikTok Downloader</b>\n\n"
        "ÐÑÐ¿ÑÐ°Ð²Ñ Ð¾Ð±ÑÐµÐ´Ð¾ÑÑÑÐ¿Ð½ÑÑ ÑÑÑÐ»ÐºÑ Ð½Ð° TikTok!"
    )


@dp.message(F.text)
async def get_link(message: Message):
    url = message.text.strip()
    parsed = urlparse(url)
    hostname = (parsed.hostname or "").lower()

    if (
        parsed.scheme not in ("http", "https")
        or not (
            hostname == "tiktok.com"
            or hostname.endswith(".tiktok.com")
        )
    ):
        await message.answer("иди нахуй")
        return

    links[message.from_user.id] = url

    keyboard = InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="ð¬ ÐÐ¸Ð´ÐµÐ¾",
                    callback_data="video",
                ),
                InlineKeyboardButton(
                    text="ðµ ÐÐ²ÑÐº MP3",
                    callback_data="audio",
                ),
            ]
        ]
    )

    await message.answer(
        "Ð§ÑÐ¾ ÑÐºÐ°ÑÐ°ÑÑ?",
        reply_markup=keyboard,
    )


@dp.callback_query(F.data.in_({"video", "audio"}))
async def choose(callback: CallbackQuery):
    url = links.get(callback.from_user.id)

    if not url:
        await callback.answer(
            "ÐÑÐ¿ÑÐ°Ð²Ñ ÑÑÑÐ»ÐºÑ Ð·Ð°Ð½Ð¾Ð²Ð¾.",
            show_alert=True,
        )
        return

    mode = callback.data
    await callback.answer()
    status = await callback.message.answer(
        "â³ ÐÐ°Ð³ÑÑÐ¶Ð°Ñ ÑÐ°Ð¹Ð», Ð¿Ð¾Ð´Ð¾Ð¶Ð´Ð¸..."
    )

    try:
        with tempfile.TemporaryDirectory() as folder:
            path = await asyncio.to_thread(
                download,
                url,
                mode,
                folder,
            )

            if not path.exists() or path.stat().st_size == 0:
                raise RuntimeError("Ð¤Ð°Ð¹Ð» Ð½Ðµ Ð½Ð°Ð¹Ð´ÐµÐ½ Ð¸Ð»Ð¸ Ð¿ÑÑÑ")

            if path.stat().st_size > MAX_FILE_SIZE:
                await status.edit_text(
                    "â Ð¤Ð°Ð¹Ð» ÑÐ»Ð¸ÑÐºÐ¾Ð¼ Ð±Ð¾Ð»ÑÑÐ¾Ð¹ Ð´Ð»Ñ Ð¾ÑÐ¿ÑÐ°Ð²ÐºÐ¸."
                )
                return

            file = FSInputFile(path)

            if mode == "audio":
                await callback.message.answer_audio(
                    file,
                    title=path.stem[:64],
                )
            else:
                await callback.message.answer_video(
                    file,
                    supports_streaming=True,
                )

        await status.edit_text(
            "â ÐÐ¾ÑÐ¾Ð²Ð¾! ÐÑÐ¿ÑÐ°Ð²Ñ ÑÐ»ÐµÐ´ÑÑÑÑÑ ÑÑÑÐ»ÐºÑ."
        )

    except Exception as error:
        logger.exception(
            "ÐÑÐ¸Ð±ÐºÐ° ÑÐºÐ°ÑÐ¸Ð²Ð°Ð½Ð¸Ñ: %s: %s",
            type(error).__name__,
            error,
        )
        try:
            await status.edit_text(
                "â ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ ÑÐºÐ°ÑÐ°ÑÑ ÑÐ°Ð¹Ð».\n"
                "ÐÐ¾Ð¿ÑÐ¾Ð±ÑÐ¹ Ð´ÑÑÐ³ÑÑ Ð¾Ð±ÑÐµÐ´Ð¾ÑÑÑÐ¿Ð½ÑÑ ÑÑÑÐ»ÐºÑ."
            )
        except Exception:
            logger.exception("ÐÐµ ÑÐ´Ð°Ð»Ð¾ÑÑ Ð¾ÑÐ¿ÑÐ°Ð²Ð¸ÑÑ ÑÐ¾Ð¾Ð±ÑÐµÐ½Ð¸Ðµ Ð¾Ð± Ð¾ÑÐ¸Ð±ÐºÐµ")

    finally:
        links.pop(callback.from_user.id, None)
