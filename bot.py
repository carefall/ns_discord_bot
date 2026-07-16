import os
import discord
from discord import Client, Interaction, Embed
from discord.app_commands import Choice, CommandTree
from dotenv import load_dotenv
import aiohttp
import datetime
from pathlib import Path
from typing import Any
from collections import Counter
import logging

load_dotenv(override=True)

TOKEN : str = os.getenv('DISCORD_TOKEN', '')
KEY : str = os.getenv('DISCORD_KEY', '')
URL : str = os.getenv('DJANGO_API_URL', 'http://127.0.0.1:8000/translator/discord')

filenames : list[str]= []

HEADERS : dict[str, str] = {
    "X-Discord-Key": KEY
}

logging.basicConfig(level=logging.WARNING)

async def load_filenames() -> None:
    global filenames
    async with aiohttp.ClientSession() as session:
        async with session.get(f"{URL}/files", headers=HEADERS, proxy="http://proxy.server:3128") as response:
            data = await response.json()
            filenames = list(data)


def parse_datetime(value: str | datetime.datetime) -> datetime.datetime:
    if isinstance(value, datetime.datetime):
        return value
    return datetime.datetime.fromisoformat(value.replace("Z", "+00:00"))


def create_file_embeds(files: dict[str, Any]) -> list[Embed]:
    embeds : list[Embed] = []
    sorted_files = sorted(
        files.items(),
        key=lambda item: (
            item[1]["finished"],
            -parse_datetime(item[1]["uploaded_at"]).timestamp(),
            item[0]
        )
    )
    for i in range(0, len(sorted_files), 15):
        chunk = sorted_files[i:i + 15]
        embed = Embed(title=f"📁 Статусы файлов (страница {i // 15 + 1})", color=0x9B59B6)
        for filename, data in chunk:
            status = "✅" if data["finished"] else "❌"
            short_name = filename.split("/")[-1]
            embed.add_field(
                name=f"{status} {short_name}",
                value=(
                    f"📂 `{filename}`\n"
                    f"👤 {data['uploaded_by']}\n"
                    f"🕒 {data['uploaded_at']}"
                ),
                inline=False
            )
        embeds.append(embed)
    return embeds

def create_users_embed(files: dict[str, Any]) -> Embed:
    counter = Counter(
        data["uploaded_by"]
        for data in files.values()
    )
    embed = Embed(title="👥 Статистика пользователей",color=0x2ECC71)
    users_count = len(
        {
            data["uploaded_by"]
            for data in files.values()
        }
    )
    embed.add_field(
        name='👥 Последних загрузчиков: ',
        value=f"{users_count}",
        inline=True
    )
    embed.add_field(
        name='Статистика по последним обновлениям',
        value='',
        inline=False
    )
    for user, count in counter.most_common(15):
        embed.add_field(
            name=f"{user} :",
            value=f"{count} файлов",
            inline=True
        )
    return embed

def create_overview_embed(files: dict[str, Any]) -> Embed:
    total = len(files)
    finished = sum(
        1
        for file in files.values()
        if file["finished"]
    )
    percent = (finished / total * 100 if total else 0)
    bar_length = 10
    filled = int(bar_length * percent / 100)
    progress_bar = ("█" * filled + "░" * (bar_length - filled))
    last_file : tuple[str, dict[str, Any]] = max(
        files.items(),
        key=lambda x: x[1]["uploaded_at"]
    )
    embed = Embed(title="📊 Отчёт", color=0x3498DB)
    embed.add_field(
        name="Всего файлов",
        value=str(total),
        inline=True
    )
    embed.add_field(
        name="Закончено",
        value=str(finished),
        inline=True
    )
    embed.add_field(
        name="Прогресс",
        value=f"{progress_bar} {percent:.1f}%",
        inline=False
    )
    embed.add_field(
        name="Последний загрузивший",
        value=last_file[1]["uploaded_by"],
        inline=True
    )
    embed.add_field(
        name="Последний файл",
        value=f"`{last_file[0]}`",
        inline=False
    )
    return embed


async def send_file_message(interaction: Interaction, data : dict[str, str | bool | datetime.datetime], file : str) -> None:
    
    embed = Embed(title=f"📄 Статус файла {Path(file).name}")
    embed.add_field(
        name="Завершён?",
        value="✅ Да" if data["finished"] else "❌ Нет",
        inline=True
    )
    embed.add_field(
        name="Последний обновивший",
        value=data["uploaded_by"],
        inline=True
    )
    uploaded_at = datetime.datetime.fromisoformat(str(data["uploaded_at"]).replace("Z", "+00:00"))
    embed.add_field(
        name="Последнее обновление",
        value=uploaded_at.strftime("%Y-%m-%d %H:%M:%S UTC"),
        inline=False
    )
    await interaction.followup.send(embed=embed)


class MyBot(Client):

    def __init__(self) -> None:
        intents = discord.Intents.default()
        super().__init__(intents=intents, proxy="http://proxy.server:3128")
        self.tree = CommandTree(self)

    async def setup_hook(self) -> None:
        await self.tree.sync()

bot = MyBot()

@bot.event
async def on_ready() -> None:
    await load_filenames()


@bot.tree.command(name="status", description="Статус перевода")

@discord.app_commands.describe(filename="Имя файла для проверки")

async def status(interaction: Interaction, filename : str | None = None) -> None:
    if filename is None:
        endpoint = f"{URL}/statuses"
    else:
        endpoint = f"{URL}/status/{filename}"
    await interaction.response.defer()
    async with aiohttp.ClientSession() as session:
        async with session.get(endpoint, headers=HEADERS, proxy="http://proxy.server:3128") as response:
            data = await response.json()
    if filename is None:
        await interaction.followup.send(embed=create_overview_embed(data))
        await interaction.followup.send(embed=create_users_embed(data))
        for embed in create_file_embeds(data):
            await interaction.followup.send(embed=embed)
    else:
        await send_file_message(interaction, data, filename)

@status.autocomplete("filename")
async def filename_autocomplete(interaction: Interaction, current: str) -> list[Choice[str]]:
    return [
        Choice(name=file, value=file)
        for file in filenames
        if current.lower() in file.lower()
    ][:25]

bot.run(TOKEN)