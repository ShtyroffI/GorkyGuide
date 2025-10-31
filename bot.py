import asyncio
import os
import json
import logging
import requests
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
    raise ValueError("Необходимо задать переменные BOT_TOKEN, YANDEX_GPT_API_KEY и FOLDER_ID в файле .env")

# Создаем логгер для текущего модуля
logger = logging.getLogger(__name__)

# URL для YandexGPT API
ASYNC_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completionAsync"
OPERATION_STATUS_URL = "https://operation.api.cloud.yandex.net/operations/"

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

def start_generation(prompt: str) -> str | None:
    """Отправляет запрос на асинхронную генерацию и возвращает ID операции."""
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
        logger.debug(f"Отправка запроса на генерацию в YandexGPT. Размер промпта: {len(prompt)} символов.")
        response = requests.post(ASYNC_API_URL, headers=headers, data=json.dumps(body))
        if response.status_code == 200:
            op_id = response.json()['id']
            logger.info(f"Запрос на генерацию успешно отправлен. ID операции: {op_id}")
            return op_id
        logger.error(f"Ошибка при запуске генерации: {response.status_code}, {response.text}")
        return None
    except Exception as e:
        logger.exception("Исключение при запуске генерации!")
        return None

async def await_result(operation_id: str) -> str | None:
    """Ожидает результат генерации, периодически проверяя статус."""
    url = f"{OPERATION_STATUS_URL}{operation_id}"
    headers = {"Authorization": f"Api-Key {YANDEX_GPT_API_KEY}"}
    
    for _ in range(30): # Ограничим количество попыток
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if data.get('done'):
                    if 'response' in data:
                        return data['response']['alternatives'][0]['message']['text']
                    else:
                        error_message = data.get('error', {}).get('message', 'Неизвестная ошибка в операции')
                        logger.error(f"Операция {operation_id} завершилась с ошибкой: {error_message}")
                        return f"Произошла ошибка при генерации маршрута: {error_message}"
                
                logger.debug(f"Операция {operation_id} еще не завершена, ждем 5 секунд...")
                await asyncio.sleep(5)
            else:
                logger.error(f"Ошибка при проверке статуса операции {operation_id}: {response.status_code}")
                return None
        except Exception as e:
            logger.exception(f"Исключение при проверке статуса операции {operation_id}!")
            return None
    logger.warning(f"Тайм-аут ожидания для операции {operation_id}.")
    return None



@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    """Начало диалога по команде /start."""
    logger.info(f"Пользователь {message.from_user.id} ({message.from_user.full_name}) запустил бота.")
    await message.answer("""Привет! Я твой AI-гид по Нижнему Новгороду.\nРасскажи, что тебе интересно? 🤔\n(например: 🎨 стрит-арт, 🏰 история, ☕️ кофейни)""")
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
    
    operation_id = start_generation(prompt)
    if not operation_id:
        await message.answer("Прости, не смог запустить генерацию маршрута. Попробуй позже.")
        return
        
    final_route = await await_result(operation_id)
    
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