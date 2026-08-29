import discord
from discord.ext import commands

class ChannelsCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name='channels')
    async def create_channels(self, ctx):
        """Creates 200 channels divided into 4 categories"""

        categories = [
            ("1-50", 1, 50),
            ("51-100", 51, 100),
            ("101-150", 101, 150),
            ("151-200", 151, 200),
        ]

        await ctx.send("Creating 200 channels...")

        for name, start, end in categories:
            # Create the category
            category = await ctx.guild.create_category(name)

            # Create the channels inside the category
            for i in range(start, end + 1):
                await ctx.guild.create_text_channel(name=f"channel-{i}", category=category)

        await ctx.send("✅ Successfully created 200 channels!")

# Setup function for the cog
async def setup(bot):
    await bot.add_cog(ChannelsCog(bot))

