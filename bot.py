import asyncio
import os
import json
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

# Импортируем логирование
from logging_config import setup_logging


# Загружаем переменные окружения
load_dotenv()

# Получаем токены и ключи
BOT_TOKEN = os.getenv("BOT_TOKEN")
YANDEX_GPT_API_KEY = os.getenv("YANDEX_GPT_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")

# Проверка наличия необходимых переменных
if not all([BOT_TOKEN, YANDEX_GPT_API_KEY, FOLDER_ID]):
    raise ValueError("Переменные BOT_TOKEN, YANDEX_GPT_API_KEY и FOLDER_ID должны быть заданы в .env")

# Создаем логгер для текущего модуля
logger = logging.getLogger(__name__)

# URL для СИНХРОННОГО YandexGPT API
SYNC_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completion"

# Инициализация бота и диспетчера
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

#  Состояния для сбора данных от пользователя
class RouteForm(StatesGroup):
    """Состояния для сбора данных от пользователя."""
    interests = State()
    time = State()
    location = State()


def construct_prompt(data: dict) -> str:
    """Создает промпт для YandexGPT на основе данных от пользователя."""
    return f"""
    Ты — эксперт-гид по Нижнему Новгороду.
    Твоя задача — составить персонализированный пешеходный маршрут для туриста.

    Вот данные от туриста:
    - Интересы: {data['interests']}
    - Доступное время: {data['time']} часа
    - Текущее местоположение: {data['location']}

    Требования к маршруту:
    1. Составь маршрут из 3-5 реальных и интересных мест в Нижнем Новгороде, которые соответствуют интересам.
    2. Начни маршрут от указанного местоположения.
    3. Для каждого места кратко и увлекательно объясни, почему его стоит посетить (ответь на вопрос "Почему мы идем именно туда?").
    4. Предложи логичную последовательность посещения мест и примерный таймлайн (сколько времени займет посещение и путь).
    5. Ответ должен быть структурированным, легко читаемым и представлен в дружелюбном тоне.
    """

async def get_gpt_route_async(prompt: str) -> str | None:
    """
    Асинхронно отправляет запрос к API YandexGPT и возвращает результат.
    """
    headers = {
        "Authorization": f"Api-Key {YANDEX_GPT_API_KEY}",
        "Content-Type": "application/json"
    }
    body = {
        "modelUri": f"gpt://{FOLDER_ID}/yandexgpt/latest",
        "completionOptions": {"stream": False, "temperature": 0.6, "maxTokens": "2000"},
        "messages": [
            {"role": "system", "text": "Ты — эксперт-гид по Нижнему Новгороду."},
            {"role": "user", "text": prompt}
        ]
    }

    try:
        # Создаем асинхронную сессию с таймаутом в 45 секунд
        timeout = aiohttp.ClientTimeout(total=45)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            logger.debug(f"Отправка запроса в YandexGPT. Размер промпта: {len(prompt)} символов.")
            
            async with session.post(SYNC_API_URL, headers=headers, json=body) as response:
                if response.status == 200:
                    data = await response.json()
                    result_text = data['result']['alternatives'][0]['message']['text']
                    logger.info("Ответ от YandexGPT успешно получен.")
                    return result_text
                else:
                    error_text = await response.text()
                    logger.error(f"Ошибка при обращении к YandexGPT: {response.status}, {error_text}")
                    return None
    except asyncio.TimeoutError:
        logger.error("Запрос к YandexGPT превысил таймаут.")
        return None
    except Exception as e:
        logger.exception("Исключение при обращении к YandexGPT!")
        return None


@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    """Начало диалога по команде /start."""
    logger.info(f"Пользователь {message.from_user.id} ({message.from_user.full_name}) запустил бота.")
    await message.answer("Привет! Я твой AI-гид по Нижнему Новгороду.\nРасскажи, что тебе интересно? 🤔\n(например: 🎨 стрит-арт, 🏰 история, ☕️ кофейни)")
    await state.set_state(RouteForm.interests)

@dp.message(RouteForm.interests)
async def process_interests(message: types.Message, state: FSMContext):
    """Обработка интересов пользователя."""
    await state.update_data(interests=message.text)
    logger.debug(f"Пользователь {message.from_user.id} ввел интересы: {message.text}")
    await message.answer("Отлично! Сколько у тебя свободного времени⏳ на прогулку (в часах)?")
    await state.set_state(RouteForm.time)

@dp.message(RouteForm.time)
async def process_time(message: types.Message, state: FSMContext):
    """Обработка времени, которое есть у пользователя."""
    if not message.text.isdigit():
        logger.warning(f"Пользователь {message.from_user.id} ввел некорректное время: {message.text}")
        await message.answer("Пожалуйста, введи количество часов цифрой (например, 3).")
        return
    await state.update_data(time=message.text)
    logger.debug(f"Пользователь {message.from_user.id} ввел время: {message.text} ч.")
    await message.answer("Принято. Теперь отправь свою геолокацию📍 или напиши адрес, откуда начнем.")
    await state.set_state(RouteForm.location)

@dp.message(RouteForm.location, F.text | F.location)
async def process_location_and_generate(message: types.Message, state: FSMContext):
    """Обработка локации и запуск генерации маршрута."""
    user_location = message.text
    if message.location:
        user_location = f"координаты {message.location.latitude}, {message.location.longitude}"
    
    await state.update_data(location=user_location)
    user_data = await state.get_data()
    await state.clear()
    
    logger.info(f"Все данные от пользователя {message.from_user.id} собраны: {user_data}")
    await message.answer("Супер! Все данные получил. 🧠 Составляю твой уникальный маршрут... Это может занять около минуты.")

    prompt = construct_prompt(user_data)
    logger.debug(f"Сформирован промпт для YandexGPT для пользователя {message.from_user.id}.")
    
    # Вызываем асинхронную функцию
    final_route = await get_gpt_route_async(prompt)
    
    if final_route:
        logger.info(f"Маршрут для пользователя {message.from_user.id} успешно сгенерирован.")
        await message.answer(final_route)
    else:
        logger.warning(f"Не удалось получить готовый маршрут для пользователя {message.from_user.id}.")
        await message.answer("Не удалось получить готовый маршрут. Что-то пошло не так, попробуй еще раз позже.")



async def main():
    """Главная функция для запуска бота."""
    # Вызываем настройку логирования
    setup_logging()
    
    logger.info("Запуск бота...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Бот остановлен вручную.")