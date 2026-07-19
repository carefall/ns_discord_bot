import os
import discord
from discord import Client, Interaction, Embed
from discord.app_commands import Choice, CommandTree
from dotenv import load_dotenv
import aiohttp
import datetime
from pathlib import Path
from typing import Any
import logging
import asyncio

load_dotenv(override=True)

TOKEN : str = os.getenv('DISCORD_TOKEN', '')
KEY : str = os.getenv('DISCORD_KEY', '')
URL : str = os.getenv('DJANGO_API_URL', 'http://127.0.0.1:8000/analytics')

filenames : list[str]= []

HEADERS : dict[str, str] = {
    "X-Discord-Key": KEY
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)

logger = logging.getLogger("discord_bot")

discord_send_lock = asyncio.Semaphore(1)

session : aiohttp.ClientSession

async def api_get(endpoint: str) -> Any | None:
    try:
        async with session.get(
            endpoint,
            headers=HEADERS,
            timeout=aiohttp.ClientTimeout(total=10)
        ) as response:
            if response.status != 200:
                logger.error("API error %s: %s", response.status, await response.text())
                return None
            return await response.json()

    except aiohttp.ClientError as e:
        logger.exception("API connection error: %s", e)
        return None

    except Exception:
        logger.exception("Unknown API error")
        return None

async def load_filenames() -> None:
    global filenames
    data = await api_get(f"{URL}/files")
    if data is None:
        logger.warning("Failed to load filenames")
        return
    filenames = list(data)


def parse_datetime(value: str | datetime.datetime) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def create_users_embed(users: dict[str, int]) -> Embed:
    embed = Embed(
        title="👥 Статистика пользователей",
        color=0x2ECC71
    )
    embed.add_field(
        name="Последние загрузки",
        value="",
        inline=False
    )
    for user, count in sorted(users.items(), key=lambda x: x[1], reverse=True)[:15]:
        embed.add_field(
            name=user,
            value=f"{count} файлов",
            inline=True
        )
    return embed

def create_overview_embed(data: dict[str, Any]) -> Embed:
    progress = data["progress"]
    bar_length = 10
    filled = int(bar_length * progress / 100)
    progress_bar = "█" * filled + "░" * (bar_length - filled)
    embed = Embed(
        title="📊 Статистика перевода",
        color=0x3498DB
    )
    embed.add_field(
        name="📁 Файлы",
        value=(
            f"Всего: **{data['files_total']}**\n"
            f"🔒 В работе: **{data['files_finished']}**"
        ),
        inline=True
    )
    embed.add_field(
        name="📝 Строки",
        value=(
            f"Переведено: **{data['strings_translated']}**\n"
            f"Всего: **{data['strings_total']}**"
        ),
        inline=True
    )
    embed.add_field(
        name="💬 Комментарии",
        value=str(data["comments"]),
        inline=True
    )
    embed.add_field(
        name="Прогресс",
        value=f"{progress_bar} **{progress:.2f}%**",
        inline=False
    )

    return embed


async def send_file_message(interaction: Interaction, data: dict[str, Any], file: str) -> None:
    try:
        embed = Embed(
            title=f"📄 Статус файла {Path(file).name}",
            color=0x3498DB
        )
        embed.add_field(
            name="Статус",
            value="🔒 В работе" if data["finished"] else "🟢 Доступен",
            inline=True
        )
        embed.add_field(
            name="Прогресс",
            value=(
                f"{data['translated']} / {data['total']}\n"
                f"{data['progress']:.2f}%"
            ),
            inline=True
        )
        embed.add_field(
            name="💬 Комментарии",
            value=str(data["comments"]),
            inline=True
        )
        embed.add_field(
            name="Последний обновивший",
            value=data["uploaded_by"],
            inline=True
        )
        uploaded_at = datetime.datetime.fromisoformat(
            str(data["uploaded_at"]).replace("Z", "+00:00")
        )
        embed.add_field(
            name="Последнее обновление",
            value=uploaded_at.strftime(
                "%Y-%m-%d %H:%M:%S UTC"
            ),
            inline=False
        )
        await safe_send_embed(interaction, embed)
    except Exception:
        logger.exception("send_file_message failed")


class MyBot(Client):
    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents)
        self.tree = CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()

    async def close(self) -> None:
        global session
        if session:
            await session.close()
        await super().close()

bot = MyBot()

@bot.event
async def on_ready() -> None:
    try:
        global session
        session = aiohttp.ClientSession()
        await load_filenames()
    except Exception:
        logger.exception("on_ready failed")

@bot.tree.error
async def on_app_command_error(interaction: Interaction, error: discord.app_commands.AppCommandError) -> None:

    logger.exception("Command error", exc_info=error)
    try:
        message = "⚠️ Произошла ошибка. Администратор уведомлён."
        if interaction.response.is_done():
            await interaction.followup.send(message)
        else:
            await interaction.response.send_message(message)
    except Exception:
        logger.exception("Failed to send error message")

@bot.tree.command(name="ping")
async def ping(interaction: Interaction) -> None:
    await interaction.response.send_message("pong")

@bot.tree.command(name="status", description="Статус перевода")
@discord.app_commands.describe(filename="Имя файла для проверки")

async def status(interaction: Interaction, filename: str | None = None) -> None:
    await interaction.response.defer()
    try:
        if filename is None:
            endpoint = f"{URL}/statuses"
        else:
            endpoint = f"{URL}/status/{filename}"
        data = await api_get(endpoint)
        if data is None:
            await interaction.followup.send("❌ Сервер переводов недоступен")
            return
        if filename is None:
            await safe_send_embed(interaction, create_overview_embed(data))
            users = await api_get(f"{URL}/users")
            if users is not None:
                await safe_send_embed(interaction, create_users_embed(users))
        else:
            await send_file_message(interaction, data, filename)
    except Exception:
        logger.exception("/status failed")
        try:
            await interaction.followup.send("❌ Ошибка обработки статуса")
        except Exception:
            pass


async def safe_send_embed(interaction: Interaction, embed: Embed) -> None:
    try:
        await interaction.followup.send(embed=embed)
    except discord.HTTPException as ex:
        logger.warning("Discord refused message")
        logger.error(ex)
    except Exception:
        logger.exception("Discord send failed")


@status.autocomplete("filename")
async def filename_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    try:
        return [
            Choice(name=file, value=file)
            for file in filenames
            if current.lower() in file.lower()
        ][:25]
    except Exception:
        logger.exception("Autocomplete failed")
        return []
    

async def safe_followup_send(interaction: Interaction, *, embed: Embed | None = None, content: str | None = None) -> None:
    async with discord_send_lock:
        try:
            if embed != None:
                await interaction.followup.send(embed=embed)
            elif content != None:
                await interaction.followup.send(content=content)
        except discord.HTTPException as e:
            logger.exception("Discord send failed: %s", e)
        except Exception:
            logger.exception("Unknown Discord send error")

bot.run(TOKEN)