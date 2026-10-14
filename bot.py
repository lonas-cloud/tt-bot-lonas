import asyncio
import os
import tempfile
from pathlib import Path
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, HTTPException
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (
    Message, CallbackQuery, Update,
    InlineKeyboardMarkup, InlineKeyboardButton, FSInputFile
)
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from yt_dlp import YoutubeDL

TOKEN = os.environ["BOT_TOKEN"]
BASE_URL = os.environ["RENDER_EXTERNAL_URL"].rstrip("/")
WEBHOOK_PATH = f"/webhook/{TOKEN}"

bot = Bot(
    TOKEN,
    default=DefaultBotProperties(parse_mode=ParseMode.HTML)
)
dp = Dispatcher()
links = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    await bot.set_webhook(BASE_URL + WEBHOOK_PATH)
    yield
    await bot.delete_webhook()
    await bot.session.close()


app = FastAPI(lifespan=lifespan)


@app.get("/")
async def home():
    return {"status": "TikTok bot is running"}


@app.post(WEBHOOK_PATH)
async def webhook(request: Request):
    data = await request.json()
    update = Update.model_validate(data, context={"bot": bot})
    await dp.feed_update(bot, update)
    return {"ok": True}


@dp.message(CommandStart())
async def start(message: Message):
    await message.answer(
        "👋 <b>TikTok Downloader</b>\n\n"
        "Отправь ссылку на TikTok!"
    )


@dp.message(F.text)
async def get_link(message: Message):
    url = message.text.strip()

    if not url.startswith(("https://", "http://")) or \
            "tiktok.com/" not in url.lower():
        await message.answer("Пришли ссылку на TikTok.")
        return

    links[message.from_user.id] = url
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🎬 Видео",
                callback_data="video"
            ),
            InlineKeyboardButton(
                text="🎵 Звук MP3",
                callback_data="audio"
            )
        ]
    ])
    await message.answer("Что скачать?", reply_markup=keyboard)


def download(url, mode, folder):
    opts = {
        "outtmpl": str(Path(folder) / "download.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 25,
        "max_filesize": 45 * 1024 * 1024,
        "format": "best[ext=mp4]/best" if mode == "video"
                  else "bestaudio/best",
    }

    if mode == "audio":
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]

    with YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)

    files = [
        p for p in Path(folder).iterdir()
        if p.is_file() and not p.name.endswith((".part", ".ytdl"))
    ]
    if not files:
        raise RuntimeError("No downloaded file")

    return max(files, key=lambda p: p.stat().st_size)


@dp.callback_query(F.data.in_({"video", "audio"}))
async def choose(callback: CallbackQuery):
    url = links.get(callback.from_user.id)
    if not url:
        await callback.answer("Отправь ссылку заново.", show_alert=True)
        return

    mode = callback.data
    await callback.answer()
    await callback.message.edit_text("⏳ Загружаю...")

    try:
        with tempfile.TemporaryDirectory() as folder:
            path = await asyncio.to_thread(download, url, mode, folder)

            if path.stat().st_size > 49 * 1024 * 1024:
                await callback.message.answer(
                    "Файл слишком большой для отправки ботом."
                )
                return

            file = FSInputFile(path)
            if mode == "audio":
                await callback.message.answer_audio(
                    file, title=path.stem[:64]
                )
            else:
                await callback.message.answer_video(
                    file, supports_streaming=True
                )

        await callback.message.answer("✅ Готово! Пришли новую ссылку.")

    except Exception:
        await callback.message.answer(
            "❌ Не удалось скачать видео. "
            "Попробуй другую общедоступную ссылку."
        )
    finally:
        links.pop(callback.from_user.id, None)
