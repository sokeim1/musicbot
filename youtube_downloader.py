"""
Модуль для поиска и скачивания музыки с YouTube
"""
import asyncio
import os
import logging
from typing import List, Dict, Optional
from io import BytesIO
import yt_dlp
# from dotenv import load_dotenv
# load_dotenv()
logger = logging.getLogger(__name__)


class YouTubeDownloader:
    """Класс для работы с YouTube через yt-dlp"""
    
    def __init__(self):
        self.ydl_opts_search = {
            'format': 'bestaudio/best',
            'quiet': True,
            'no_warnings': True,
            'extract_flat': True,
            'socket_timeout': 30,
            'nocheckcertificate': True,
            # Обход защиты YouTube
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web'],
                    'skip': ['hls', 'dash']
                }
            },
        }
        
        self.ydl_opts_download = {
            'format': 'bestaudio[ext=m4a]/bestaudio[ext=webm]/bestaudio/best[height<=480]',
            'quiet': True,
            'no_warnings': True,
            'outtmpl': '%(id)s.%(ext)s',
            # Расширенные настройки для обхода защиты YouTube
            'extractor_args': {
                'youtube': {
                    'player_client': ['android', 'web', 'ios'],
                    'skip': ['hls', 'dash'],
                    'player_skip': ['configs'],
                }
            },
            'geo_bypass': True,
            'geo_bypass_country': 'DE',  # Германия
            'nocheckcertificate': True,
            'prefer_insecure': True,
            'socket_timeout': 60,
            'http_chunk_size': 1048576,
            # Дополнительные заголовки для обхода детекции ботов
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Linux; Android 11; SM-G973F) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.120 Mobile Safari/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.9',
                'Accept-Encoding': 'gzip, deflate',
                'DNT': '1',
                'Connection': 'keep-alive',
                'Upgrade-Insecure-Requests': '1',
            },
            # Дополнительные опции
            'age_limit': None,
            'writesubtitles': False,
            'writeautomaticsub': False,
        }
        
        logger.info("YouTube downloader инициализирован с обходом защиты ботов")
    
    async def search(self, query: str, limit: int = 10) -> List[Dict[str, str]]:
        """
        Поиск музыки на YouTube
        
        Args:
            query: поисковый запрос
            limit: максимальное количество результатов
            
        Returns:
            Список словарей с информацией о треках
        """
        try:
            # Добавляем "audio" к запросу для лучших результатов
            search_query = f"ytsearch{limit}:{query} audio"
            logger.info(f"Выполняем поиск: {search_query}")
            
            # Выполняем поиск в отдельном потоке
            loop = asyncio.get_event_loop()
            results = await loop.run_in_executor(
                None,
                self._search_sync,
                search_query
            )
            
            if not results or 'entries' not in results:
                logger.warning(f"YouTube не вернул результаты для запроса: {query}")
                return []
            
            logger.debug(f"YouTube вернул {len(results.get('entries', []))} результатов")
            
            tracks = []
            for entry in results.get('entries', []):
                if not entry:
                    continue
                
                # Извлекаем информацию о треке
                title = entry.get('title', 'Неизвестно')
                uploader = entry.get('uploader', 'Неизвестный исполнитель')
                duration = entry.get('duration', 0)
                url = entry.get('url') or f"https://youtube.com/watch?v={entry.get('id')}"
                
                # Форматируем длительность
                duration_str = self._format_duration(duration)
                
                tracks.append({
                    'title': title,
                    'artist': uploader,
                    'duration': duration_str,
                    'url': url,
                    'full_name': f"{uploader} - {title}"
                })
            
            logger.info(f"Поиск вернул {len(tracks)} треков для запроса: {query}")
            return tracks
            
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}", exc_info=True)
            return []
    
    def _search_sync(self, search_query: str) -> dict:
        """Синхронный поиск через yt-dlp"""
        with yt_dlp.YoutubeDL(self.ydl_opts_search) as ydl:
            return ydl.extract_info(search_query, download=False)
    
    async def download_track(self, url: str) -> Optional[bytes]:
        """
        Скачивание трека с YouTube
        
        Args:
            url: URL видео на YouTube
            
        Returns:
            Байты аудио файла или None в случае ошибки
        """
        try:
            logger.info(f"Начало скачивания: {url}")
            
            # Скачиваем во временный файл
            loop = asyncio.get_event_loop()
            filename = await loop.run_in_executor(
                None,
                self._download_sync,
                url
            )
            
            if not filename or not os.path.exists(filename):
                logger.error(f"Файл не был создан или не найден: {filename}")
                return None
            
            # Читаем файл
            with open(filename, 'rb') as f:
                audio_data = f.read()
            
            logger.info(f"Файл прочитан: {len(audio_data)} байт")
            
            # Удаляем временный файл
            try:
                os.remove(filename)
                logger.debug(f"Временный файл удален: {filename}")
            except Exception as del_err:
                logger.warning(f"Не удалось удалить временный файл: {del_err}")
            
            return audio_data
            
        except Exception as e:
            logger.error(f"Ошибка скачивания: {e}", exc_info=True)
            return None
    
    def _download_sync(self, url: str) -> Optional[str]:
        """Синхронное скачивание через yt-dlp с несколькими методами"""
        max_retries = 3
        
        # Разные конфигурации для попыток
        configs = [
            # Попытка 1: Android клиент
            {**self.ydl_opts_download, 'extractor_args': {'youtube': {'player_client': ['android']}}},
            # Попытка 2: iOS клиент
            {**self.ydl_opts_download, 'extractor_args': {'youtube': {'player_client': ['ios']}}},
            # Попытка 3: Web клиент с дополнительными заголовками
            {**self.ydl_opts_download, 'extractor_args': {'youtube': {'player_client': ['web']}}}
        ]
        
        for attempt in range(max_retries):
            try:
                config = configs[attempt] if attempt < len(configs) else self.ydl_opts_download
                logger.info(f"Попытка скачивания {attempt + 1}/{max_retries}: {url}")
                logger.info(f"Используется клиент: {config['extractor_args']['youtube']['player_client']}")
                
                with yt_dlp.YoutubeDL(config) as ydl:
                    info = ydl.extract_info(url, download=True)
                    
                    if not info:
                        logger.warning("Не удалось получить информацию о видео")
                        continue
                    
                    video_id = info.get('id')
                    logger.debug(f"ID видео: {video_id}")
                    
                    # Ищем скачанный файл
                    for ext in ['m4a', 'webm', 'opus', 'mp3', 'mp4']:
                        filename = f"{video_id}.{ext}"
                        if os.path.exists(filename):
                            logger.info(f"✅ Файл успешно скачан: {filename}")
                            return filename
                    
                    logger.warning(f"Файл не найден после попытки {attempt + 1}")
                    
            except Exception as e:
                error_msg = str(e)
                logger.error(f"Ошибка при попытке {attempt + 1}: {error_msg}")
                
                # Проверяем специфичные ошибки YouTube
                if "Sign in to confirm" in error_msg or "not a bot" in error_msg:
                    logger.warning("YouTube требует аутентификацию, пробуем другой клиент...")
                elif "Video unavailable" in error_msg:
                    logger.error("Видео недоступно")
                    return None
                elif "Private video" in error_msg:
                    logger.error("Приватное видео")
                    return None
                
                if attempt == max_retries - 1:
                    logger.error("Все попытки исчерпаны")
                    return None
                
                # Пауза перед повторной попыткой
                import time
                wait_time = 2 + attempt  # Увеличиваем время ожидания
                logger.info(f"Ожидание {wait_time} секунд перед повторной попыткой...")
                time.sleep(wait_time)
        
        return None
    
    def _format_duration(self, seconds) -> str:
        """Форматирование длительности в минуты:секунды"""
        if not seconds:
            return "N/A"
        
        # Конвертируем в int, если это float
        seconds = int(seconds)
        
        minutes = seconds // 60
        secs = seconds % 60
        return f"{minutes}:{secs:02d}"
