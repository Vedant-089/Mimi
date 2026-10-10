import discord
from discord.ext import commands
import os
from database import Database
from functions import ensure_ping_tables, warm_ping_caches
import asyncio
from dotenv import load_dotenv

load_dotenv()  # move this to top before any os.getenv calls

intents = discord.Intents.default()
intents.message_content = True
intents.members = True
intents.guilds = True

bot = commands.Bot(command_prefix=commands.when_mentioned_or("m!", "M!"), intents=intents, help_command=None)

bot.db = Database(dsn=os.getenv("DATABASE_URL"))

async def load_extensions():
    for filename in os.listdir("./commands"):
        if filename.endswith(".py"):
            await bot.load_extension(f"commands.{filename[:-3]}")

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user.name}")

@bot.event
async def on_message(message):
    if message.author.bot:
        context = await bot.get_context(message)
        command = context.command
        if command is not None and command.qualified_name.split(" ", 1)[0] == "reserves":
            await bot.invoke(context)
        return

    await bot.process_commands(message)

async def main():
    async with bot:
        await bot.db.connect()
        await ensure_ping_tables(bot.db)
        await warm_ping_caches(bot.db)
        await load_extensions()
        await bot.start(os.getenv("DISCORD_TOKEN"))

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Bot is shutting down gracefully.")