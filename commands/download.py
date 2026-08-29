import discord
from discord.ext import commands
import aiohttp
import os

class ImageFetcher(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.save_dir = "saved_images"
        os.makedirs(self.save_dir, exist_ok=True)

    @commands.command(name="fetch_images")
    async def fetch_images(self, ctx):
        """Downloads any visible images from messages, including forwarded embeds."""
        channel_id = 1395737844533690408
        channel = self.bot.get_channel(channel_id)

        if not channel:
            return await ctx.send("❌ Could not access channel.")

        await ctx.send("🔍 Scanning messages...")

        count = 0

        async for message in channel.history(limit=None):
            urls = set()

            # ⬇️ 1. Try attachments
            for attachment in message.attachments:
                if attachment.content_type and attachment.content_type.startswith("image/"):
                    urls.add(attachment.url)

            # ⬇️ 2. Try embeds (especially forwarded ones)
            for embed in message.embeds:
                if embed.image and embed.image.url:
                    urls.add(embed.image.url)
                if embed.thumbnail and embed.thumbnail.url:
                    urls.add(embed.thumbnail.url)
                if embed.url and embed.url.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp")):
                    urls.add(embed.url)

            # ⬇️ 3. Try deep fields if they include image URLs
            for embed in message.embeds:
                if embed.fields:
                    for field in embed.fields:
                        for text in [field.name, field.value]:
                            for ext in (".png", ".jpg", ".jpeg", ".gif", ".webp"):
                                if ext in text:
                                    start = text.find("http")
                                    if start != -1:
                                        url = text[start:].split()[0]
                                        if url.endswith(ext):
                                            urls.add(url)

            # 🔽 Try downloading
            for i, url in enumerate(urls):
                filename = f"{message.id}_{i}.jpg"
                if await self.download_image(url, filename):
                    count += 1

        await ctx.send(f"✅ Done. Downloaded {count} image(s).")

    async def download_image(self, url, filename):
        path = os.path.join(self.save_dir, filename)
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status == 200:
                        with open(path, "wb") as f:
                            f.write(await resp.read())
                        print(f"✅ Saved: {filename}")
                        return True
        except Exception as e:
            print(f"⚠️ Error downloading image from {url}: {e}")
        return False

# Setup
async def setup(bot):
    await bot.add_cog(ImageFetcher(bot))

