import asyncio
import os
import json
import logging
import aiohttp
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
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
    return f"""
    Ты — увлеченный и харизматичный гид по Нижнему Новгороду. Твоя задача — составить живой и интересный пешеходный маршрут и вернуть его СТРОГО в формате JSON.

    ДАННЫЕ ОТ ТУРИСТА:
    - Интересы: {data['interests']}
    - Доступное время: {data['time']} часа
    - Текущее местоположение: {data['location']}

    --- НОВЫЕ ПРАВИЛА КАЧЕСТВА ---
    1.  **Пиши увлекательно!** Используй живой язык. Вместо "Здесь находится..." пиши "Представьте себе...".
    2.  **Добавь "фишку".** Для каждого места в "reason" добавь один малоизвестный факт или интригующую деталь.
    3.  **Таймлайн должен быть простым и читаемым.** Используй интервалы от начала прогулки. Например: "0-15 мин: Осмотр Арсенала", "15-25 мин: Прогулка до музея".

    ТРЕБОВАНИЯ К JSON:
    1. Ответ должен быть ОДНИМ JSON-объектом.
    2. JSON должен содержать ключи: "title", "start_location", "route_points", "timeline", "summary".
    3. "route_points" — массив объектов с ключами: "name", "reason", "travel_info", "visit_duration".
    4. Не используй Markdown внутри JSON-значений.

    ### Пример идеального формата JSON:
    {{
      "title": "Историческое сердце Нижнего: прогулка по Кремлю",
      "start_location": "Площадь Минина и Пожарского",
      "route_points": [
        {{
          "name": "Дмитриевская башня",
          "reason": "Это не просто вход в Кремль, а его визитная карточка! По легенде, именно здесь хранилась казна города. Обязательно поднимитесь на смотровую площадку.",
          "travel_info": "Находится прямо у входа.",
          "visit_duration": "20 минут"
        }}
      ],
      "timeline": [
        "0-5 мин: Осмотр Дмитриевской башни.",
        "5-20 мин: Прогулка к Арсеналу."
      ],
      "summary": "Эта прогулка — быстрое, но насыщенное погружение в военную и гражданскую историю города прямо в его сердце."
    }}
    """

def format_route_from_json(route_data: dict) -> str:
    """
    Форматирует данные из JSON в идеально структурированное сообщение для Telegram.
    """
    try:
        # --- БЛОК 1: Заголовок ---
        title_block = [
            f"*{route_data.get('title', 'Ваш пешеходный маршрут')}*", # ИЗМЕНЕНО
            f"*Начало маршрута:* {route_data.get('start_location', 'Не указано')}" # ИЗМЕНЕНО
        ]

        # --- БЛОК 2: Точки маршрута ---
        route_points_blocks = []
        for i, point in enumerate(route_data.get('route_points', []), 1):
            point_details = [
                f"*{i}. {point.get('name', 'Без названия')}*", # ИЗМЕНЕНО
                f"_{point.get('reason', '')}_",
                f"*Время в пути:* {point.get('travel_info', 'Не указано')}", # ИЗМЕНЕНО
                f"*Время посещения:* {point.get('visit_duration', 'Не указано')}" # ИЗМЕНЕНО
            ]
            route_points_blocks.append("\n".join(point_details))

        # --- БЛОК 3: Таймлайн ---
        timeline_items = route_data.get('timeline', [])
        if timeline_items:
            timeline_block = ["_Примерный таймлайн:_"] # ИЗМЕНЕНО
            for item in timeline_items:
                timeline_block.append(f"- _{item}_")
            timeline_block = ["\n".join(timeline_block)]
        else:
            timeline_block = []

        # --- БЛОК 4: Заключение ---
        summary_block = [route_data.get('summary', '')]

        # --- ФИНАЛЬНАЯ СБОРКА ---
        all_blocks = [
            "\n".join(title_block),
            *route_points_blocks,
            *timeline_block,
            *summary_block
        ]
        
        return "\n\n".join(filter(None, all_blocks))

    except Exception as e:
        logger.error(f"Критическая ошибка при форматировании JSON: {e}", exc_info=True)
        return "Произошла ошибка при обработке маршрута."    
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
        "completionOptions": {"stream": False, "temperature": 0.1, "maxTokens": "2000"},
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
    logger.debug(f"Сформирован JSON-промпт для YandexGPT.")
    
    # Получаем ответ в виде строки JSON
    json_string = await get_gpt_route_async(prompt)
    
    if not json_string:
        logger.warning(f"Не удалось получить ответ от YandexGPT.")
        await message.answer("Не удалось сгенерировать маршрут. Попробуйте позже.")
        return
    
    if json_string.startswith("```"):
        json_string = json_string[3:]
    if json_string.endswith("```"):
        json_string = json_string[:-3]
    json_string = json_string.strip()


    try:
        # Пытаемся распарсить JSON
        route_data = json.loads(json_string)
        
        # Форматируем JSON в красивое сообщение
        formatted_message = format_route_from_json(route_data)

        logger.debug(f"Финальное отформатированное сообщение для отправки:\n---\n{formatted_message}\n---")
        
        logger.info(f"Маршрут для пользователя {message.from_user.id} успешно сгенерирован и отформатирован.")
        await message.answer(formatted_message, parse_mode=ParseMode.MARKDOWN)
        
    except json.JSONDecodeError:
        logger.error(f"Не удалось распарсить JSON от YandexGPT. Ответ:\n{json_string}")
        await message.answer("Получен некорректный ответ от AI, не могу построить маршрут. Пожалуйста, попробуйте изменить запрос.")


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