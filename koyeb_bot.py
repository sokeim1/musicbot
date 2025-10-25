"""
Музыкальный Telegram бот для Koyeb хостинга
Оптимизирован для бесплатного плана с keep-alive
"""
import asyncio
import os
import logging
import json
from io import BytesIO
from typing import Optional
from datetime import datetime
import aiohttp
from aiohttp import web

from aiogram import Bot, Dispatcher, F
from aiogram.filters import Command, CommandStart
from aiogram.types import Message, BufferedInputFile, InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage

from youtube_downloader import YouTubeDownloader

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]  # Только консоль для Koyeb
)
logger = logging.getLogger(__name__)

# Токен бота (замените на ваш)
BOT_TOKEN = "8353650126:AAGvR3EoPXWeyCMkDIB8gDR7NwXx1REMbwQ"

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
storage = MemoryStorage()
dp = Dispatcher(storage=storage)

# ID админа для доступа к статистике
ADMIN_ID = 7850455999

# Статистика в памяти (для бесплатного плана)
users_stats = {"users": []}


def add_user(user_id: int, username: str = None, first_name: str = None):
    """Добавляет пользователя в статистику"""
    global users_stats
    
    # Проверяем, есть ли уже такой пользователь
    for user in users_stats['users']:
        if user['user_id'] == user_id:
            # Обновляем данные
            user['last_seen'] = datetime.now().isoformat()
            if username:
                user['username'] = username
            if first_name:
                user['first_name'] = first_name
            return False  # Пользователь уже был
    
    # Добавляем нового пользователя
    users_stats['users'].append({
        'user_id': user_id,
        'username': username,
        'first_name': first_name,
        'joined': datetime.now().isoformat(),
        'last_seen': datetime.now().isoformat()
    })
    return True  # Новый пользователь


class MusicStates(StatesGroup):
    """Состояния FSM для работы с музыкой"""
    waiting_for_query = State()
    choosing_track = State()


@dp.message(CommandStart())
async def cmd_start(message: Message):
    """Обработчик команды /start"""
    # Добавляем пользователя в статистику
    user = message.from_user
    is_new = add_user(
        user_id=user.id,
        username=user.username,
        first_name=user.first_name
    )
    
    # Логируем нового пользователя
    if is_new:
        logger.info(f"Новый пользователь: {user.id} (@{user.username}) - {user.first_name}")
    
    await message.answer(
        "🎵 <b>Привет! Я @DownloaderSSMusicBot</b>\n\n"
        "💫 Я помогу тебе найти и скачать любую музыку\n\n"
        "✨ Просто отправь мне название песни или исполнителя!\n\n"
        "🌐 <i>Работаю на Koyeb хостинге</i>",
        parse_mode="HTML"
    )


@dp.message(Command('stats'))
async def cmd_stats(message: Message):
    """Обработчик команды /stats - только для админа"""
    logger.info(f"Запрос статистики от пользователя {message.from_user.id}")
    
    # Проверяем, является ли пользователь админом
    if message.from_user.id != ADMIN_ID:
        logger.warning(f"Отказано в доступе к /stats для пользователя {message.from_user.id}")
        await message.answer("❌ У вас нет доступа к этой команде")
        return
    
    users = users_stats.get('users', [])
    
    if not users:
        await message.answer(
            "📊 <b>Статистика бота</b>\n\n"
            "👥 Пользователей: 0",
            parse_mode="HTML"
        )
        return
    
    # Формируем сообщение со статистикой
    total_users = len(users)
    
    # Сортируем по дате добавления (последние 10)
    recent_users = sorted(users, key=lambda x: x.get('joined', ''), reverse=True)[:10]
    
    text = f"📊 <b>Статистика бота</b>\n\n"
    text += f"👥 <b>Всего пользователей:</b> {total_users}\n\n"
    text += f"📋 <b>Последние {min(10, total_users)} пользователей:</b>\n\n"
    
    for idx, user in enumerate(recent_users, 1):
        username = f"@{user.get('username')}" if user.get('username') else "Нет username"
        first_name = user.get('first_name', 'Без имени')
        user_id = user.get('user_id')
        
        # Форматируем дату
        joined = user.get('joined', '')
        if joined:
            try:
                joined_date = datetime.fromisoformat(joined).strftime("%d.%m.%Y")
            except:
                joined_date = "Неизвестно"
        else:
            joined_date = "Неизвестно"
        
        text += f"{idx}. <b>{first_name}</b> ({username})\n"
        text += f"   ID: <code>{user_id}</code>\n"
        text += f"   Присоединился: {joined_date}\n\n"
    
    await message.answer(text, parse_mode="HTML")


@dp.message(Command('search'))
async def cmd_search(message: Message, state: FSMContext):
    """Обработчик команды /search"""
    await message.answer(
        "🔍 <b>Поиск музыки</b>\n\n"
        "Отправь название песни или исполнителя:",
        parse_mode="HTML"
    )
    await state.set_state(MusicStates.waiting_for_query)


@dp.message(Command('cancel'))
async def cmd_cancel(message: Message, state: FSMContext):
    """Обработчик команды /cancel"""
    current_state = await state.get_state()
    if current_state is None:
        await message.answer("❌ Нечего отменять")
        return
    
    await state.clear()
    await message.answer("✅ Операция отменена")


async def show_tracks_page(message: Message, tracks: list, page: int, state: FSMContext):
    """Показывает страницу с треками"""
    TRACKS_PER_PAGE = 5
    total_pages = (len(tracks) + TRACKS_PER_PAGE - 1) // TRACKS_PER_PAGE
    
    # Вычисляем индексы треков для текущей страницы
    start_idx = page * TRACKS_PER_PAGE
    end_idx = min(start_idx + TRACKS_PER_PAGE, len(tracks))
    page_tracks = tracks[start_idx:end_idx]
    
    # Формируем клавиатуру
    keyboard = []
    
    for idx, track in enumerate(page_tracks):
        global_idx = start_idx + idx
        
        # Форматируем красиво: номер + название + длительность
        # Обрезаем длинные названия
        title = track['title']
        duration = track.get('duration', 'N/A')
        
        # Ограничиваем длину названия
        max_title_length = 30
        if len(title) > max_title_length:
            title = title[:max_title_length] + "..."
        
        button_text = f"{global_idx + 1}. {title} • {duration}"
        
        keyboard.append([InlineKeyboardButton(
            text=button_text,
            callback_data=f"download_{global_idx}"
        )])
    
    # Кнопки навигации
    nav_buttons = []
    
    if page > 0:
        nav_buttons.append(InlineKeyboardButton(
            text="⬅️ Назад",
            callback_data=f"page_{page - 1}"
        ))
    
    nav_buttons.append(InlineKeyboardButton(
        text=f"📄 {page + 1}/{total_pages}",
        callback_data="page_info"
    ))
    
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton(
            text="Вперёд ➡️",
            callback_data=f"page_{page + 1}"
        ))
    
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    # Кнопка отмены
    keyboard.append([InlineKeyboardButton(
        text="❌ Отмена",
        callback_data="cancel"
    )])
    
    markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    
    # Формируем текст сообщения
    text = (
        f"🎵 <b>Результаты поиска</b>\n"
        f"📊 Найдено: {len(tracks)} треков\n"
        f"📄 Страница {page + 1} из {total_pages}\n\n"
        f"⏬ Выбери трек для скачивания:"
    )
    
    await message.edit_text(
        text,
        reply_markup=markup,
        parse_mode="HTML"
    )


@dp.message(MusicStates.waiting_for_query)
@dp.message(F.text & ~F.text.startswith('/'))
async def search_music(message: Message, state: FSMContext):
    """Поиск музыки по запросу пользователя"""
    query = message.text.strip()
    
    if not query:
        await message.answer("❌ Пожалуйста, отправь корректный запрос")
        return
    
    # Отправляем сообщение о начале поиска
    search_msg = await message.answer("🔍 Ищу музыку...")
    
    try:
        logger.info(f"Поиск музыки: '{query}' от пользователя {message.from_user.id}")
        
        # Поиск треков (увеличим лимит до 20)
        downloader = YouTubeDownloader()
        tracks = await downloader.search(query, limit=20)
        
        logger.info(f"Найдено {len(tracks)} треков для запроса: '{query}'")
        
        if not tracks:
            logger.warning(f"Треки не найдены для запроса: '{query}'")
            await search_msg.edit_text(
                "❌ Ничего не найдено\n\n"
                "Попробуй изменить запрос или используй /search для нового поиска"
            )
            await state.clear()
            return
        
        # Сохраняем треки и показываем первую страницу
        await state.update_data(tracks=tracks, page=0)
        await state.set_state(MusicStates.choosing_track)
        
        # Показываем первую страницу
        await show_tracks_page(search_msg, tracks, 0, state)
        
    except Exception as e:
        logger.error(f"Ошибка при поиске: {e}")
        await search_msg.edit_text(
            "❌ Произошла ошибка при поиске\n\n"
            "Попробуй еще раз позже"
        )
        await state.clear()


@dp.callback_query(F.data == "cancel")
async def callback_cancel(callback: CallbackQuery, state: FSMContext):
    """Обработчик отмены выбора"""
    await callback.message.edit_text("✅ Поиск отменен")
    await state.clear()
    await callback.answer()


@dp.callback_query(F.data.startswith("page_"))
async def callback_page(callback: CallbackQuery, state: FSMContext):
    """Обработчик навигации по страницам"""
    if callback.data == "page_info":
        await callback.answer("ℹ️ Используй кнопки для навигации", show_alert=False)
        return
    
    # Получаем номер страницы
    page = int(callback.data.split("_")[1])
    
    # Получаем треки из состояния
    data = await state.get_data()
    tracks = data.get('tracks', [])
    
    if not tracks:
        await callback.answer("❌ Треки не найдены", show_alert=True)
        return
    
    # Обновляем страницу в состоянии
    await state.update_data(page=page)
    
    # Показываем новую страницу
    await show_tracks_page(callback.message, tracks, page, state)
    await callback.answer()


@dp.callback_query(F.data.startswith("download_"))
async def callback_download(callback: CallbackQuery, state: FSMContext):
    """Обработчик скачивания выбранного трека"""
    await callback.answer("⏳ Скачиваю...")
    
    try:
        # Получаем индекс трека
        track_idx = int(callback.data.split("_")[1])
        
        # Получаем список треков из состояния
        data = await state.get_data()
        tracks = data.get('tracks', [])
        
        if track_idx >= len(tracks):
            await callback.message.edit_text("❌ Трек не найден")
            await state.clear()
            return
        
        track = tracks[track_idx]
        
        logger.info(f"Начало скачивания трека: '{track['title']}' ({track['url']}) для пользователя {callback.from_user.id}")
        
        # Прогресс бар при скачивании
        progress_msg = await callback.message.edit_text(
            f"⬜⬜⬜⬜⬜⬜⬜⬜⬜⬜ 0%\n"
            f"📥 <b>Подготовка...</b>\n\n"
            f"🎵 {track['title']}\n"
            f"⏱ {track['duration']}",
            parse_mode="HTML"
        )
        
        # Обновляем прогресс - 25%
        await asyncio.sleep(0.3)
        await progress_msg.edit_text(
            f"🟦🟦⬜⬜⬜⬜⬜⬜⬜⬜ 25%\n"
            f"📥 <b>Скачивание...</b>\n\n"
            f"🎵 {track['title']}\n"
            f"⏱ {track['duration']}",
            parse_mode="HTML"
        )
        
        # Скачиваем трек
        downloader = YouTubeDownloader()
        audio_data = await downloader.download_track(track['url'])
        
        if not audio_data:
            logger.error(f"Не удалось скачать трек: '{track['title']}' ({track['url']})")
            await callback.message.edit_text(
                "❌ <b>YouTube недоступен</b>\n\n"
                "🔍 YouTube блокирует запросы с серверов\n"
                "💡 <b>Попробуйте:</b>\n"
                "• Выбрать другой трек из списка\n"
                "• Выполнить новый поиск\n"
                "• Попробовать позже\n\n"
                "🌐 <i>Работаем над решением проблемы</i>",
                parse_mode="HTML"
            )
            await state.clear()
            return
        
        file_size_mb = len(audio_data) / 1024 / 1024
        logger.info(f"Трек скачан успешно: '{track['title']}', размер: {file_size_mb:.2f} МБ")
        
        # Обновляем прогресс - 75%
        await progress_msg.edit_text(
            f"🟦🟦🟦🟦🟦🟦🟦⬜⬜⬜ 75%\n"
            f"📤 <b>Отправка...</b>\n\n"
            f"🎵 {track['title']}\n"
            f"⏱ {track['duration']}",
            parse_mode="HTML"
        )
        
        # Отправляем аудио файл
        audio_file = BufferedInputFile(
            file=audio_data,
            filename=f"{track['artist']} - {track['title']}.mp3"
        )
        
        # Форматируем название трека красиво
        formatted_title = f"♫ {track['title']}"
        performer_with_bot = f"{track['artist']} ✦ @DownloaderSSMusicBot"
        
        # Загружаем обложку
        thumbnail_path = os.path.join(os.path.dirname(__file__), 'thumbnail.jpg')
        if os.path.exists(thumbnail_path):
            with open(thumbnail_path, 'rb') as thumb_file:
                thumbnail = BufferedInputFile(thumb_file.read(), filename='thumbnail.jpg')
        else:
            thumbnail = None
        
        await callback.message.answer_audio(
            audio=audio_file,
            title=formatted_title,
            performer=performer_with_bot,
            thumbnail=thumbnail,
            caption=f"🎵 <b>{track['title']}</b>\n"
                   f"👤 <i>{track['artist']}</i>\n"
                   f"⏱ {track['duration']}\n\n"
                   f"📥 Downloaded by @DownloaderSSMusicBot\n"
                   f"🌐 Powered by Koyeb",
            parse_mode="HTML"
        )
        
        # Удаляем сообщение с прогресс баром
        await progress_msg.delete()
        
        logger.info(f"Трек успешно отправлен пользователю {callback.from_user.id}: '{track['title']}'")
        
        await state.clear()
        
    except Exception as e:
        logger.error(f"Ошибка при скачивании/отправке трека: {e}", exc_info=True)
        
        # Проверяем, не слишком ли большой файл
        error_msg = str(e)
        if "Request Entity Too Large" in error_msg or "too large" in error_msg.lower():
            logger.warning(f"Файл слишком большой для Telegram: '{track['title']}'")
            await callback.message.edit_text(
                "❌ <b>Файл слишком большой!</b>\n\n"
                "📦 Размер файла превышает лимит Telegram (50 МБ)\n\n"
                "💡 <b>Попробуй:</b>\n"
                "• Выбрать другую версию трека\n"
                "• Найти короткую версию песни",
                parse_mode="HTML"
            )
        else:
            await callback.message.edit_text(
                "❌ <b>Произошла ошибка</b>\n\n"
                "Попробуй выбрать другой трек\n"
                "или выполни новый поиск",
                parse_mode="HTML"
            )
        await state.clear()


# HTTP endpoints для keep-alive и мониторинга
async def health_check(request):
    """Endpoint для проверки здоровья бота"""
    return web.Response(
        text=f"🎵 Music Bot is alive!\n⏰ {datetime.now().isoformat()}\n👥 Users: {len(users_stats['users'])}",
        status=200,
        content_type='text/plain'
    )


async def stats_endpoint(request):
    """Endpoint для получения статистики"""
    return web.json_response({
        'status': 'alive',
        'users_count': len(users_stats['users']),
        'timestamp': datetime.now().isoformat()
    })


# Keep-alive функция для предотвращения засыпания
async def keep_alive():
    """Периодически пингует сам себя для предотвращения засыпания"""
    while True:
        try:
            await asyncio.sleep(300)  # Каждые 5 минут
            # Можно добавить самопинг если нужно
            logger.info("Keep-alive ping")
        except Exception as e:
            logger.error(f"Keep-alive error: {e}")


async def start_web_server():
    """Запуск веб-сервера для keep-alive пингов"""
    app = web.Application()
    app.router.add_get('/', health_check)
    app.router.add_get('/health', health_check)
    app.router.add_get('/stats', stats_endpoint)
    
    # Порт из переменной окружения или 8080 по умолчанию
    port = int(os.getenv('PORT', 8080))
    
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', port)
    await site.start()
    
    logger.info(f"🌐 HTTP сервер запущен на порту {port}")
    return runner


async def main():
    """Главная функция запуска бота"""
    logger.info("🚀 Запуск бота на Koyeb...")
    
    try:
        # Запускаем HTTP сервер для пингов
        web_runner = await start_web_server()
        
        # Запускаем keep-alive в фоне
        keep_alive_task = asyncio.create_task(keep_alive())
        
        # Удаляем старые обновления
        await bot.delete_webhook(drop_pending_updates=True)
        
        logger.info("✅ Бот готов к работе!")
        
        # Запускаем polling
        await dp.start_polling(bot)
        
    finally:
        await bot.session.close()
        if web_runner:
            await web_runner.cleanup()
        keep_alive_task.cancel()


if __name__ == '__main__':
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("👋 Бот остановлен")
