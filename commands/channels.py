import discord
from discord.ext import commands

class ChannelManager(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="channel")
    @commands.has_permissions(manage_channels=True)
    async def create_incense_channels(self, ctx):
        """Creates a category 'incense' and 100 text channels inside it."""
        guild = ctx.guild
        category_name = "incense-1"

        # Check if category already exists
        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            category = await guild.create_category(category_name)
            await ctx.send(f"✅ Created category **{category_name}**.")
        else:
            await ctx.send(f"⚠️ Category **{category_name}** already exists — using it.")

        created = 0
        for i in range(51, 100):
            channel_name = f"incense-{i}"
            existing = discord.utils.get(guild.channels, name=channel_name)
            if existing:
                continue  # skip already existing channels

            await guild.create_text_channel(channel_name, category=category)
            created += 1

        await ctx.send(f"✅ Done! Created {created} new incense channels.")

async def setup(bot):
    await bot.add_cog(ChannelManager(bot))
