import os
from typing import Dict
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import Message
from pydantic import BaseModel
from openai import OpenAI
from dotenv import load_dotenv
import json
import asyncio
import PyPDF2
import requests

def download_pdf(url, filename):
    response = requests.get(url)
    response.raise_for_status()  # Ошибка, если не 200

    with open(filename, 'wb') as f:
        f.write(response.content)
    print(f"✅ Файл успешно сохранён как: {filename}")

def save_pdf():
    urls = {
        "ai": "https://api.itmo.su/constructor-ep/api/v1/static/programs/10033/plan/abit/pdf",
        "ai_product": "https://api.itmo.su/constructor-ep/api/v1/static/programs/10035/plan/abit/pdf"
    }

    for key, url in urls.items():
        filename = f"{key}_study_plan.pdf"
        download_pdf(url, filename)

save_pdf()

# Загрузка переменных окружения
load_dotenv()

# Конфигурация
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

# Инициализация клиента OpenAI с OpenRouter
client = OpenAI(
    base_url="https://openrouter.ai/api/v1",
    api_key=OPENROUTER_API_KEY,
)

# Инициализация бота
bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()

# Состояния для FSM
class Form(StatesGroup):
    waiting_for_background = State()
    waiting_for_question = State()

# Модели данных
class BackgroundInfo(BaseModel):
    education: str
    experience: str
    interests: str
    goals: str


def read_pdf(file_path: str) -> str:
    try:
        with open(file_path, "rb") as file:
            reader = PyPDF2.PdfReader(file)
            text = ""
            for page in reader.pages:
                text += page.extract_text() or ""
            return text.strip()
    except Exception as e:
        return f"Ошибка при чтении PDF-файла {file_path}: {str(e)}"

# Загрузка текстов учебных планов из PDF
ai_program_text = read_pdf("ai_study_plan.pdf")
ai_product_program_text = read_pdf("ai_product_study_plan.pdf")

# Функция для запросов к LLM
async def ask_llm(prompt: str, context: str = "") -> str:
    response = client.chat.completions.create(
        # model="meta-llama/llama-3-8b-instruct:free",
        model="deepseek/deepseek-chat-v3-0324:free",
        messages=[
            {"role": "system", "content": "Ты помощник для абитуриентов магистратуры ITMO. Отвечай только по теме обучения."},
            {"role": "user", "content": f"{context}\n\n{prompt}"}
        ],
        temperature=0.7,
    )
    return response.choices[0].message.content

# Главная функция рекомендации
async def recommend_program_with_context(background: BackgroundInfo) -> str:
    """Рекомендует программу на основе бэкграунда и учебных планов"""
    prompt = f"""
    Бэкграунд абитуриента:
    - Образование: {background.education}
    - Опыт: {background.experience}
    - Интересы: {background.interests}
    - Цели: {background.goals}

    Доступные программы:
    
    1. Магистратура "Искусственный интеллект":
    {ai_program_text}
    
    2. Магистратура "Управление AI продуктами":
    {ai_product_program_text}

    Проанализируй бэкграунд абитуриента и учебные планы программ. 
    Ответь в формате JSON:
    {{
        "recommended_program": "название программы",
        "reason": "обоснование рекомендации",
        "suggested_courses": ["список", "релевантных", "курсов"]
    }}
    """
    
    response = await ask_llm(prompt)
    return response

# Функция для показа результата
async def show_recommendation_result(state: FSMContext) -> str:
    """Показывает результат пользователю"""
    data = await state.get_data()
    recommendation = data.get("recommendation", "{}")
    
    try:
        rec_data = json.loads(recommendation)
        program = rec_data.get("recommended_program", "не определена")
        reason = rec_data.get("reason", "")
        courses = "\n- ".join(rec_data.get("suggested_courses", []))
        
        message = (
            f"🎓 Рекомендованная программа: {program}\n\n"
            f"📌 Обоснование: {reason}\n\n"
            f"📚 Рекомендуемые курсы:\n- {courses}\n\n"
            f"Задайте вопросы о программе, если нужно уточнить детали!"
        )
    except json.JSONDecodeError:
        message = "Получены рекомендации:\n" + recommendation
    
    await state.set_state(Form.waiting_for_question)
    return message

# Обработчики Telegram
@dp.message(Command("start"))
async def cmd_start(message: Message, state: FSMContext):
    await message.answer(
        "Привет! Я помогу тебе выбрать подходящую магистерскую программу в ITMO.\n"
        "Давай начнем с твоего бэкграунда. Какое у тебя образование?"
    )
    await state.set_state(Form.waiting_for_background)
    await state.update_data(conversation_stage="ask_education")

@dp.message(Form.waiting_for_background)
async def process_background(message: Message, state: FSMContext):
    data = await state.get_data()
    stage = data.get("conversation_stage")
    
    if stage == "ask_education":
        await state.update_data(education=message.text, conversation_stage="ask_experience")
        await message.answer("Какой у тебя профессиональный опыт?")
    elif stage == "ask_experience":
        await state.update_data(experience=message.text, conversation_stage="ask_interests")
        await message.answer("Какие аспекты AI тебя больше всего интересуют? (технические, бизнес-приложения, исследования и т.д.)")
    elif stage == "ask_interests":
        await state.update_data(interests=message.text, conversation_stage="ask_goals")
        await message.answer("Какие у тебя карьерные цели после магистратуры?")
    elif stage == "ask_goals":
        await state.update_data(goals=message.text)
        
        # Формируем рекомендацию
        background = BackgroundInfo(
            education=data.get("education", ""),
            experience=data.get("experience", ""),
            interests=data.get("interests", ""),
            goals=message.text
        )
        recommendation = await recommend_program_with_context(background)
        await state.update_data(recommendation=recommendation)
        
        # Показываем результат
        result_message = await show_recommendation_result(state)
        await message.answer(result_message)

@dp.message(Form.waiting_for_question)
async def process_question(message: Message, state: FSMContext):
    """Обработчик вопросов о программах"""
    data = await state.get_data()
    background = BackgroundInfo(
        education=data.get("education", ""),
        experience=data.get("experience", ""),
        interests=data.get("interests", ""),
        goals=data.get("goals", "")
    )
    
    # Формируем контекст для ответа
    context = f"""
    Бэкграунд абитуриента:
    - Образование: {background.education}
    - Опыт: {background.experience}
    - Интересы: {background.interests}
    - Цели: {background.goals}
    
    Программа 1: Искусственный интеллект
    {ai_program_text}
    
    Программа 2: Управление AI продуктами
    {ai_product_program_text}
    """
    
    response = await ask_llm(message.text, context)
    await message.answer(response)

# Запуск бота
async def main():
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())