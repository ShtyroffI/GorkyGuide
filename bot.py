import asyncio
import os
import requests
import json
import time
from aiogram import Bot, Dispatcher, types, F
from aiogram.filters import CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from dotenv import load_dotenv

# --- Конфигурация (как у вас) ---
load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")
YANDEX_GPT_API_KEY = os.getenv("YANDEX_GPT_API_KEY")
FOLDER_ID = os.getenv("FOLDER_ID")

if not all([BOT_TOKEN, YANDEX_GPT_API_KEY, FOLDER_ID]):
    raise ValueError("Проверьте .env — не хватает данных")

# --- URL для YandexGPT (теперь используем асинхронные) ---
ASYNC_API_URL = "https://llm.api.cloud.yandex.net/foundationModels/v1/completionAsync"
OPERATION_STATUS_URL = "https://operation.api.cloud.yandex.net/operations/"

# --- Инициализация бота и диспетчера ---
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# --- Машина состояний для сбора данных ---
class RouteForm(StatesGroup):
    interests = State()  # Состояние для ожидания интересов
    time = State()       # Состояние для ожидания времени
    location = State()   # Состояние для ожидания локации

# --- Функции для работы с YandexGPT API ---

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
    1. Составь маршрут из 3-5 реальных и интересных мест в Нижнем Новгороде.
    2. Начни маршрут от указанного местоположения.
    3. Для каждого места кратко и увлекательно объясни, почему его стоит посетить.
    4. Предложи логичную последовательность и примерный таймлайн.
    5. Ответ должен быть структурированным и дружелюбным.
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
        response = requests.post(ASYNC_API_URL, headers=headers, data=json.dumps(body))
        if response.status_code == 200:
            return response.json()['id']
        print(f"Ошибка при запуске генерации: {response.status_code}, {response.text}")
        return None
    except Exception as e:
        print(f"Исключение при запуске генерации: {e}")
        return None

async def await_result(operation_id: str) -> str | None:
    """Ожидает результат генерации, периодически проверяя статус."""
    url = f"{OPERATION_STATUS_URL}{operation_id}"
    headers = {"Authorization": f"Api-Key {YANDEX_GPT_API_KEY}"}

    while True:
        try:
            response = requests.get(url, headers=headers)
            if response.status_code == 200:
                data = response.json()
                if data.get('done'):
                    if 'response' in data:
                        return data['response']['alternatives'][0]['message']['text']
                    else:
                        # Если 'done' true, но ответа нет, значит была ошибка
                        error_message = data.get('error', {}).get('message', 'Неизвестная ошибка в операции')
                        return f"Произошла ошибка при генерации маршрута: {error_message}"
                # Если не готово, ждем перед следующей проверкой
                await asyncio.sleep(2)
            else:
                return f"Ошибка при проверке статуса: {response.status_code}"
        except Exception as e:
            return f"Исключение при проверке статуса: {str(e)}"

# --- Обработчики сообщений (хэндлеры) ---

@dp.message(CommandStart())
async def cmd_start(message: types.Message, state: FSMContext):
    """Начало диалога, запрашиваем интересы."""
    await message.answer("Привет! Я твой AI-гид по Нижнему Новгороду.\nРасскажи, что тебе интересно? (например, стрит-арт, история, кофейни)")
    await state.set_state(RouteForm.interests)

@dp.message(RouteForm.interests)
async def process_interests(message: types.Message, state: FSMContext):
    """Обрабатываем интересы и запрашиваем время."""
    await state.update_data(interests=message.text)
    await message.answer("Отлично! Сколько у тебя свободного времени на прогулку (в часах)?")
    await state.set_state(RouteForm.time)

@dp.message(RouteForm.time)
async def process_time(message: types.Message, state: FSMContext):
    """Обрабатываем время и запрашиваем локацию."""
    # Простая проверка, что введено число
    if not message.text.isdigit():
        await message.answer("Пожалуйста, введи количество часов цифрой (например, 3).")
        return
    await state.update_data(time=message.text)
    await message.answer("Принято. Теперь отправь свою геолокацию или напиши адрес, откуда начнем.")
    await state.set_state(RouteForm.location)

@dp.message(RouteForm.location)
async def process_location_and_generate(message: types.Message, state: FSMContext):
    """Получаем последнюю информацию и запускаем генерацию."""
    # Пользователь может отправить и текст, и геолокацию
    user_location = message.text
    if message.location:
        user_location = f"{message.location.latitude}, {message.location.longitude}"
    
    await state.update_data(location=user_location)
    user_data = await state.get_data()
    await state.clear() # Завершаем диалог FSM

    # Уведомляем пользователя, что мы начали работу
    await message.answer("Супер! Все данные получил. 🧠 Составляю твой уникальный маршрут... Это может занять около минуты.")

    # 1. Создаем промпт
    prompt = construct_prompt(user_data)
    
    # 2. Запускаем асинхронную генерацию
    operation_id = start_generation(prompt)
    if not operation_id:
        await message.answer("Прости, не смог запустить генерацию маршрута. Попробуй позже.")
        return
        
    # 3. Ожидаем результат
    final_route = await await_result(operation_id)
    
    # 4. Отправляем результат пользователю
    if final_route:
        await message.answer(final_route)
    else:
        await message.answer("Не удалось получить готовый маршрут. Что-то пошло не так.")


# --- Запуск бота ---
async def main():
    print("Бот запущен...")
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())