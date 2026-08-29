import discord
from discord.ext import commands
import torch
import timm
from torchvision import transforms
from PIL import Image
import aiohttp
import io
import json
from database import db

# async helper – already uses the global pool from database.py
from functions import get_users_hunting, get_users_collecting

POKETWO_BOT_ID = 716390085896962058
MODEL_PATH = "pokemon_model.pt"
MODEL_NAME = "convnext_tiny.fb_in22k"
IMG_SIZE = 224
CONF_THRESHOLD = 0.30  # 30%


def preprocess_image(img: Image.Image):
    transform = transforms.Compose(
        [
            transforms.Resize((IMG_SIZE, IMG_SIZE)),
            transforms.ToTensor(),
            transforms.Normalize([0.5] * 3, [0.5] * 3),
        ]
    )
    return transform(img).unsqueeze(0)


class SpawnPredictor(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.bot.loop.create_task(self.ensure_db_connected())

        # index → name map
        with open("class.json", "r") as f:
            idx_map = json.load(f)
        self.idx_to_name = [None] * len(idx_map)
        for name, idx in idx_map.items():
            self.idx_to_name[idx] = name

        # load typing.json
        with open("typing.json", "r", encoding="utf-8") as f:
            self.typing_data = json.load(f)

        # ✅ load region.json
        with open("region.json", "r", encoding="utf-8") as f:
            self.region_data = json.load(f)

        # load model
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
        self.model = timm.create_model(
            MODEL_NAME, pretrained=False, num_classes=len(self.idx_to_name)
        )
        self.model.load_state_dict(torch.load(MODEL_PATH, map_location=self.device))
        self.model.eval().to(self.device)

        self.session = aiohttp.ClientSession()

    async def ensure_db_connected(self):
        from database import db
        if not db.pool:
            await db.connect()

    async def cog_unload(self):
        await self.session.close()

    def get_pokemon_types(self, name: str):
        """Return a list of types that the given Pokémon belongs to."""
        types = []
        for t, names in self.typing_data.items():
            if name.lower() in [n.lower() for n in names]:
                types.append(t)
        return types

    def get_pokemon_regions(self, name: str):
        """Return a list of regions that the given Pokémon belongs to."""
        regions = []
        for region, names in self.region_data.items():
            if name.lower() in [n.lower() for n in names]:
                regions.append(region)
        return regions

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        # Handle only Pokétwo embeds with an image
        if (
            message.author.id != POKETWO_BOT_ID
            or not message.embeds
            or not (message.embeds[0].image and message.embeds[0].image.url)
        ):
            return

        try:
            # fetch image
            async with self.session.get(message.embeds[0].image.url) as resp:
                if resp.status != 200:
                    return
                img_bytes = await resp.read()

            img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
            tensor = preprocess_image(img).to(self.device)

            with torch.no_grad():
                probs = torch.softmax(self.model(tensor), dim=1)[0]

            top_idx = int(torch.argmax(probs))
            confidence = float(probs[top_idx])
            name = self.idx_to_name[top_idx]

            if confidence < CONF_THRESHOLD:
                await message.channel.send(
                    "❌ Not confident enough to guess this Pokémon."
                )
                return

            hunters = await get_users_hunting(name, db)
            collectors = await get_users_collecting(name, message.guild.id, db)

            types = self.get_pokemon_types(name)
            regions = self.get_pokemon_regions(name)

            type_role_mentions = []
            for t in types:
                role = discord.utils.get(message.guild.roles, name=t.lower())
                if role:
                    type_role_mentions.append(role.mention)

            region_role_mentions = []
            for r in regions:
                role = discord.utils.get(message.guild.roles, name=r.lower())
                if role:
                    region_role_mentions.append(role.mention)

            reply_lines = [
                f"{name}: {confidence:.3%}",
                f"Best Name: **{name}**"
            ]

            # 🔹 Type roles + names
            if type_role_mentions:
                reply_lines.append("Type Pings: " + ", ".join(type_role_mentions))
                reply_lines.append("Types: " + ", ".join(types))  # 🟩 ADDED

            # 🔹 Region roles + names
            if region_role_mentions:
                reply_lines.append("Region Pings: " + ", ".join(region_role_mentions))
                reply_lines.append("Regions: " + ", ".join(regions))  # 🟩 ADDED

            if hunters:
                hunter_pings = ", ".join(f"<@{uid}>" for uid in hunters)
                reply_lines.append(f"Hunt Pings: {hunter_pings}")

            if collectors:
                collector_pings = ", ".join(f"<@{uid}>" for uid in collectors)
                reply_lines.append(f"Col Pings: {collector_pings}")

            reply = "\n".join(reply_lines)
            await message.channel.send(reply)

        except Exception as e:
            print("⚠️ Prediction error:", e)


async def setup(bot: commands.Bot):
    await bot.add_cog(SpawnPredictor(bot))