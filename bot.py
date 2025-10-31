import asyncio
import os
import json
import logging
import aiohttp
import random
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.enums import ParseMode
from aiogram.types import ReplyKeyboardMarkup, KeyboardButton
from dotenv import load_dotenv

# Импортируем логирование
from logging_config import setup_logging

DATA_FILE_PATH = 'data.json'

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

main_keyboard = ReplyKeyboardMarkup(
    keyboard=[
        [
            KeyboardButton(text="🗺️ Построить свой маршрут")
        ]
    ],
    resize_keyboard=True, # Делает кнопки более компактными
    one_time_keyboard=False, # Клавиатура не будет скрываться после нажатия
    input_field_placeholder="Нажмите на кнопку, чтобы начать..." # Текст в поле ввода
)

try:
    with open(DATA_FILE_PATH, 'r', encoding='utf-8') as f:
        data = json.load(f)
        cultural_data = data.get('categories')
        if cultural_data is None:
            # Явно поднимем KeyError, чтобы это попало в except и было залогировано
            raise KeyError("'categories' key missing in data.json")
    logger.info(f"Файл данных '{DATA_FILE_PATH}' успешно загружен.")
except (FileNotFoundError, KeyError, json.JSONDecodeError) as e:
    logger.critical(f"Критическая ошибка при загрузке данных из '{DATA_FILE_PATH}': {e}")
    # Используем пустой словарь, чтобы в дальнейшем код корректно проверял наличие категории
    cultural_data = {}

#  Состояния для сбора данных от пользователя
class RouteForm(StatesGroup):
    """Состояния для сбора данных от пользователя."""
    interests = State()
    time = State()
    location = State()

def construct_prompt_for_interests(interests: str) -> str:
    return f"""
    Какая из перечисленных категорий (1:Памятники и монументы; 2:Парки и скверы; 3:Архитектурные сооружения; 4:Музеи и галереи; 5:Театры; 6:Культурно-досуговые центры; 7:Набережные; 8:Тактильные макеты; 9:Советские мозаики) наиболее подходит под интересы ({interests}) пользователя?
    ГЛАВНОЕ ПРАВИЛО: Отвечай ТОЛЬКО цифрой от 1 до 9, без пояснений.
    """

def construct_prompt(user_data: dict) -> str:
    """Промпт для AI-рассказчика на основе отобранных данных."""
    objects_text = "\n".join([f"- {obj['title']} (адрес: {obj['address']})" for obj in user_data['selected_objects']])
    
    return f"""
    Ты — увлеченный гид и скрупулезный планировщик. Твоя задача — взять ГОТОВЫЙ список достопримечательностей и сплести из них живой и интересный пешеходный маршрут, который СТРОГО соответствует запросу туриста. Верни результат в формате JSON.

    --- ГЛАВНОЕ ПРАВИЛО: СООТВЕТСТВИЕ ВРЕМЕНИ ---
    Общая продолжительность маршрута должна максимально точно соответствовать 'Доступному времени' туриста: {user_data['time']} часа.

    ДАННЫЕ ОТ ТУРИСТА:
    - Начальная точка: {user_data['location']}

    СПИСОК ДОСТОПРИМЕЧАТЕЛЬНОСТЕЙ ДЛЯ МАРШРУТА (используй ТОЛЬКО их):
    {objects_text}

    --- ПРАВИЛА КАЧЕСТВА ---
    1. Пиши увлекательно, добавляй "фишку" или малоизвестный факт для каждого места.
    2. Таймлайн должен быть простым и читаемым, в формате "0-15 мин: ...".

    ТРЕБОВАНИЯ К JSON:
    Верни ОДИН JSON-объект с ключами: "title", "start_location", "route_points", "timeline", "summary". "route_points" — массив объектов с ключами "name", "reason", "travel_info", "visit_duration".
    """

def format_route_from_json(route_data: dict) -> str:
    """
    Форматирует данные из JSON в идеально структурированное сообщение для Telegram.
    """
    try:
        title_block = [
            f"*{route_data.get('title', 'Ваш пешеходный маршрут')}*", 
            f"*Начало маршрута:* {route_data.get('start_location', 'Не указано')}" 
        ]

        route_points_blocks = []
        for i, point in enumerate(route_data.get('route_points', []), 1):
            point_details = [
                f"*{i}. {point.get('name', 'Без названия')}*", 
                f"_{point.get('reason', '')}_",
                f"*Время в пути:* {point.get('travel_info', 'Не указано')}", 
                f"*Время посещения:* {point.get('visit_duration', 'Не указано')}" 
            ]
            route_points_blocks.append("\n".join(point_details))

        timeline_items = route_data.get('timeline', [])
        if timeline_items:
            timeline_block = ["_Примерный таймлайн:_"]
            for item in timeline_items:
                timeline_block.append(f"- _{item}_")
            timeline_block = ["\n".join(timeline_block)]
        else:
            timeline_block = []

        summary_block = [route_data.get('summary', '')]

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
    if not cultural_data:
        await message.answer("Извините, сервис временно недоступен.")
        return
        
    logger.info(f"Пользователь {message.from_user.id} запустил бота.")
    
    await state.clear() 
    
    await message.answer(
        "Привет! Я твой AI-гид по Нижнему Новгороду. Нажми на кнопку ниже, чтобы начать!",
        reply_markup=main_keyboard
    )

@dp.message(F.text == "🗺️ Построить свой маршрут")
async def handle_build_route_button(message: types.Message, state: FSMContext):
    """Обрабатывает нажатие на кнопку и запускает FSM."""
    logger.info(f"Пользователь {message.from_user.id} нажал кнопку 'Построить маршрут'.")
    await message.answer("Расскажи, что тебе интересно? 🤔")
    await state.set_state(RouteForm.interests)



@dp.message(RouteForm.interests)
async def process_interests(message: types.Message, state: FSMContext):
    """Обработка интересов пользователя."""
    await state.update_data(interests=message.text)
    prompt_for_category = construct_prompt_for_interests(message.text)
    category = await get_gpt_route_async(prompt_for_category)
    logger.info(f"YandexGPT обработал интересы и вернул категорию: {category}.")

    category_key = category.strip() if category else ""

    # Сразу получаем список объектов по ключу категории
    all_objects_in_category = cultural_data.get(category_key)

    # Проверяем, что мы получили именно список и он не пустой
    if not isinstance(all_objects_in_category, list) or not all_objects_in_category:
        logger.warning(f"Категория '{category_key}' не найдена или пуста в data.json.")
        await message.answer("К сожалению, не нашёл подходящих мест. Попробуйте сформулировать интересы иначе.")
        return # Остаемся в том же состоянии FSM, чтобы пользователь мог попробовать еще раз

    # Безопасный выбор: если объектов меньше 4 — берем все, иначе случайную подвыборку из 4
    num_to_select = min(len(all_objects_in_category), 4)
    selected_objects = random.sample(all_objects_in_category, num_to_select)

    await state.update_data(selected_objects=selected_objects)

    await message.answer(f"Отлично, я подобрал для вас несколько мест по вашим интересам!\n\nСколько у вас свободного времени⏳ на прогулку (в часах)?")
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

    prompt = construct_prompt( user_data)
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