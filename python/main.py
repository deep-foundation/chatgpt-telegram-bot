import asyncio
import logging
import os
import re
import sys
from typing import Any
import tiktoken
import aiofiles
import aiohttp
import openai
from aiogram import Bot, Dispatcher, Router
from aiogram.enums import ParseMode
from aiogram.filters.callback_data import CallbackData
from aiogram.types import Message, BufferedInputFile, CallbackQuery
from aiogram.utils.keyboard import InlineKeyboardBuilder
from dotenv import load_dotenv
import tempfile
import Filters as ContentTypesFilter

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
openai.api_type = "azure"
openai.api_key = os.getenv('OPENAI_API_KEY')
openai.api_base = "https://deep-ai.openai.azure.com"
openai.api_version = "2023-03-15-preview"
encoding = tiktoken.encoding_for_model("gpt-4")


async def send_or_split_message(message, text):
    if len(text) > 4096:
        for i in range(0, len(text), 4096):
            text_chunk = text[i:i + 4096]
            await message.answer(text_chunk)
    else:
        await message.answer(text)

    # text_file = BufferedInputFile(bytes(text, 'utf-8'), filename="file.txt")
    # await message.answer_document(text_file)


async def get_openai_completion(prompt):
    try:
        chat_completion = await openai.ChatCompletion.acreate(
            deployment_id="gpt-4-128k",
            model="gpt-4",
            messages=[{"role": 'user', "content": prompt}]
        )

        return chat_completion["choices"][0]["message"]["content"]
    except Exception as e:
        logger.error(f"OpenAI completion error: {e}")
        raise


router = Router(name=__name__)


class MyCallback(CallbackData, prefix="my"):
    action: str
    id: int


class UserContext:
    def __init__(self):
        self.data = ""

    def update_data(self, value):
        self.data += "\n" + value

    def clear_data(self):
        self.data = ""

    def get_data(self):
        return self.data


users_context = {}


def get_user_context(user_id):
    if user_id not in users_context:
        users_context[user_id] = UserContext()
    return users_context[user_id]


@router.callback_query()
async def handle_callback_query(callback_query: CallbackQuery) -> Any:
    data = callback_query.data
    cb1 = MyCallback.unpack(data)
    user_context = get_user_context(cb1.id)
    user_data = user_context.get_data()
    if cb1.action == "Send":
        if user_data == "":
            await callback_query.message.answer("Context is empty")
        else:
            answer = await get_openai_completion(user_data)
            user_context.update_data("\n---\n" + answer)
            await send_or_split_message(callback_query.message, answer)
    elif cb1.action == "Clear":
        user_context.clear_data()
        await callback_query.message.answer("Context cleared")
    elif cb1.action == "See":
        if user_data == "":
            await callback_query.message.answer("Context is empty")
        else:
            await callback_query.message.answer(user_data)
    await callback_query.answer()


def contains_url(string):
    url_pattern = re.compile(r'https?://\S+')
    return url_pattern.search(string) is not None


def find_url(string):
    url_pattern = re.compile(r'https?://\S+')
    match = url_pattern.search(string)
    if match:
        return match.group()
    return None


async def fetch(url):
    async with aiohttp.ClientSession() as session:
        async with session.get(url) as response:
            return await response.text()


@router.message(ContentTypesFilter.Text())
async def handle_text(message: Message) -> Any:
    user_id = message.from_user.id
    user_context = get_user_context(user_id)
    try:
        if message.text:
            user_context.update_data("\n---\n" + message.text)
        if contains_url(message.text):
            url = find_url(message.text)
            html_content = await fetch(url)
            user_context.update_data(url + ":\n" + html_content)

        logger.info(f"---------\nReceived message: {message}")
        if message.reply_to_message and message.reply_to_message.text:
            user_context.update_data(message.reply_to_message.text)
        document_file = message.reply_to_message.document if message.reply_to_message and message.reply_to_message.document else None
        if document_file:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                await bot.download(document_file, temp_file.name)
            async with aiofiles.open(temp_file.name, 'r', encoding='utf-8') as file:
                user_context.update_data(await file.read())
        tokens_count = len(encoding.encode(user_context.get_data()))
        builder = InlineKeyboardBuilder()
        builder.button(text="Send request", callback_data=MyCallback(action="Send", id=user_id))
        builder.button(text="Clear context", callback_data=MyCallback(action="Clear", id=user_id))
        builder.button(text="See context", callback_data=MyCallback(action="See", id=user_id))
        markup = builder.as_markup()
        await message.answer(f"Your context: {tokens_count}/128000", reply_markup=markup)
    except Exception as e:
        logger.error(e)


@router.message(ContentTypesFilter.Document())
async def handle_document(message: Message) -> Any:
    try:
        user_id = message.from_user.id
        user_context = get_user_context(user_id)
        user_document = message.document if message.document else None
        if message.caption:
            user_context.update_data(message.caption)
        if user_document:
            with tempfile.NamedTemporaryFile(delete=False) as temp_file:
                await bot.download(user_document, temp_file.name)
            async with aiofiles.open(temp_file.name, 'r', encoding='utf-8') as file:
                user_context.update_data(await file.read())
        tokens_count = len(encoding.encode(user_context.get_data()))
        builder = InlineKeyboardBuilder()
        builder.button(text="Send request", callback_data=MyCallback(action="Send", id=user_id))
        builder.button(text="Clear context", callback_data=MyCallback(action="Clear", id=user_id))
        builder.button(text="See context", callback_data=MyCallback(action="See", id=user_id))
        markup = builder.as_markup()
        await message.answer(f"Your context: {tokens_count}/128000", reply_markup=markup)
    except UnicodeDecodeError as e:
        logger.error(e)
        await message.answer("This file is not supported.")
    except Exception as e:
        logger.error(e)

dp = Dispatcher()

TOKEN = os.getenv("TELEGRAM_TOKEN")
bot = Bot(TOKEN, parse_mode=ParseMode.HTML)


async def main() -> None:
    dp.include_router(router)
    await dp.start_polling(bot)


if __name__ == "__main__":
    logging.basicConfig(level=logging.DEBUG, stream=sys.stdout)
    httpx_logger = logging.getLogger("httpx")
    httpx_logger.setLevel(logging.DEBUG)
    httpx_logger.propagate = True
    asyncio.run(main())
