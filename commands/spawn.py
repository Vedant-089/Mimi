import discord
from discord.ext import commands
import onnxruntime as ort
import numpy as np
from PIL import Image
import aiohttp
import io
import json
import urllib.request
import os
import asyncio
import functools
import concurrent.futures
import re
import socket
import time
from collections import defaultdict
from pathlib import Path

from database import db
from functions import (
    get_users_hunting,
    get_users_collecting,
    get_users_reserved,
    get_users_type_ping,
    get_users_region_ping,
    update_and_check_incense,
    parse_incense_footer,
    warm_ping_caches,
)

POKETWO_BOT_ID = 716390085896962058
MODEL_PATH = "pokemon_student_int8.onnx"
IMG_SIZE = 224
CONF_THRESHOLD = 0.30  # 30%
RECOG_PATH = Path("recog.json")
TYPE_SETTINGS_PATH = Path("type_settings.json")
TYPE_CHANNELS_PATH = Path("type_channels.json")
CATCH_RE = re.compile(
    r"^Congratulations\s+<@!?(\d+)>!\s+You caught a Level",
    re.IGNORECASE,
)


if not os.path.exists(MODEL_PATH):
    print(f"Downloading {MODEL_PATH}...")
    urllib.request.urlretrieve(
        "https://huggingface.co/veduxd/pokemon_model/resolve/main/pokemon_student_int8.onnx",
        MODEL_PATH
    )
    print(f"{MODEL_PATH} downloaded.")


def preprocess_image(img: Image.Image):
    img = img.resize((IMG_SIZE, IMG_SIZE))
    arr = np.array(img).astype(np.float32) / 255.0
    arr = (arr - 0.5) / 0.5
    arr = arr.transpose(2, 0, 1)  # HWC -> CHW
    return arr[np.newaxis, :]  # add batch dim


def run_inference(session, tensor_np):
    outputs = session.run(None, {"input": tensor_np})
    logits = outputs[0][0]
    exp = np.exp(logits - np.max(logits))
    probs = exp / exp.sum()
    return probs


def run_full_inference(session, img_bytes):
    """
    Decode -> preprocess -> inference, all inside ONE executor call.

    This must run entirely off the event loop. Splitting decode/preprocess
    (PIL + numpy, both blocking/CPU-bound) onto the event loop while only
    offloading session.run() was the bug that let one spawn's image
    processing stall the whole bot's Discord gateway heartbeat and every
    other spawn's download while it ran.
    """
    preprocess_start = time.perf_counter()

    img = Image.open(io.BytesIO(img_bytes)).convert("RGB")
    tensor_np = preprocess_image(img)

    preprocess_ms = (time.perf_counter() - preprocess_start) * 1000

    inference_start = time.perf_counter()

    probs = run_inference(session, tensor_np)

    inference_ms = (time.perf_counter() - inference_start) * 1000

    return probs, preprocess_ms, inference_ms


class SpawnPredictor(commands.Cog):

    def __init__(self, bot):
        self.bot = bot
        self.type_channels_lock = asyncio.Lock()
        self.bot.loop.create_task(self.ensure_db_connected())
        self.timing_stats = {
            "inference": {"min": None, "max": None, "sum": 0.0, "count": 0},
            "response": {"min": None, "max": None, "sum": 0.0, "count": 0},
        }

        json_start = time.perf_counter()

        # Load class.json
        # Expected format: {"Pikachu": 0, "Charmander": 1, ...} (dict of
        # name -> index). When exporting the new model, save it as:
        #   class_to_idx = {name: idx for idx, name in enumerate(full_ds.classes)}
        #   json.dump(class_to_idx, open("class.json", "w"))
        # NOT as a plain list - a list will silently break this loading
        # code (or crash on idx_map.items() since lists don't have that).
        with open("class.json", "r") as f:
            idx_map = json.load(f)

        self.idx_to_name = [None] * len(idx_map)

        for name, idx in idx_map.items():
            self.idx_to_name[idx] = name

        # Load typing.json
        with open("typing.json", "r", encoding="utf-8") as f:
            self.typing_data = json.load(f)

        # Load region.json
        with open("region.json", "r", encoding="utf-8") as f:
            self.region_data = json.load(f)

        # Load rare.json
        with open("rare.json", "r", encoding="utf-8") as f:
            self.rare_data = json.load(f)

        # Load regional.json
        with open("regional.json", "r", encoding="utf-8") as f:
            self.regional_data = json.load(f)

        # Load eeveelutions_paradox.json
        with open("eeveelutions_paradox.json", "r", encoding="utf-8") as f:
            self.eeveelutions_paradox_data = json.load(f)

        # Load gigantamax.json
        with open("gigantamax.json", "r", encoding="utf-8") as f:
            self.gigantamax_data = json.load(f)

        self.json_load_ms = (time.perf_counter() - json_start) * 1000

        csv_files = [
            name for name in os.listdir(".")
            if name.lower().endswith(".csv")
        ]
        if csv_files:
            import csv as csv_module

            csv_start = time.perf_counter()
            for csv_name in csv_files:
                with open(csv_name, "r", encoding="utf-8", newline="") as f:
                    list(csv_module.reader(f))
            self.csv_load_ms = (time.perf_counter() - csv_start) * 1000
        else:
            self.csv_load_ms = None

        # Precompute normalized name lookups for robust matching.
        self.rare_lookup = {
            group: {self.normalize_name(n) for n in names}
            for group, names in self.rare_data.items()
        }

        self.regional_lookup = {
            group: {self.normalize_name(n) for n in names}
            for group, names in self.regional_data.items()
        }

        self.eeveelutions_paradox_lookup = {
            group: {self.normalize_name(n) for n in names}
            for group, names in self.eeveelutions_paradox_data.items()
        }

        self.gigantamax_lookup = {
            group: {self.normalize_name(n) for n in names}
            for group, names in self.gigantamax_data.items()
        }

        self.name_to_types = defaultdict(list)
        for type_name, names in self.typing_data.items():
            for pokemon_name in names:
                self.name_to_types[pokemon_name.lower()].append(type_name)

        self.name_to_regions = defaultdict(list)
        for region_name, names in self.region_data.items():
            for pokemon_name in names:
                self.name_to_regions[pokemon_name.lower()].append(region_name)

        self._type_enabled = self._read_type_enabled()
        self._recognition_enabled = self._read_recognition_enabled()

        # Load ONNX model with CPU settings tuned for this VPS.
        # NOTE: intra_op_num_threads=1 was benchmarked for the old
        # ConvNeXt-Tiny model. Re-benchmark this value (try 1, 2, 3)
        # against the new MobileNetV4 int8 model before trusting it -
        # the optimal thread count is architecture-dependent and may
        # have changed.
        session_options = ort.SessionOptions()
        session_options.graph_optimization_level = (
            ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        )
        session_options.intra_op_num_threads = 1
        session_options.inter_op_num_threads = 1

        self.ort_session = ort.InferenceSession(
            MODEL_PATH,
            sess_options=session_options,
            providers=["CPUExecutionProvider"]
        )

        # Keep inference isolated from other bot thread-pool work.
        # Each session.run() is pinned to 1 internal thread (see
        # intra_op_num_threads above), so 2 workers here means at most
        # 2 genuinely parallel single-threaded inferences on the 3-vCPU
        # VPS, leaving 1 core free for the event loop / Discord gateway.
        # If you still see queuing under peak load, try 3; if latency
        # per-call gets worse, drop back to 1.
        self.inference_executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=25,
            thread_name_prefix="onnx-inference"
        )

        print("ONNX model loaded (optimized, 1 CPU thread per worker, 2 workers).")

        # family=AF_INET avoids a very common failure mode: aiohttp (no
        # Happy Eyeballs) may still be handed an IPv6 address for
        # cdn.discordapp.com / media.discordapp.net by the resolver even
        # on a nominally "IPv4-only" box, if any IPv6 interface/route is
        # present but not actually reachable. The SYN gets silently
        # dropped and the OS sits on it for its full connect-timeout
        # (commonly ~10-21s) before falling back to IPv4. That matches
        # the random 10s/20s spikes exactly. Pinning to IPv4 skips the
        # dead attempt entirely, regardless of the underlying cause.
        #
        # limit_per_host caps concurrent connections to a single host so
        # one saturated host can't starve the whole pool.
        connector = aiohttp.TCPConnector(
            limit=100,
            limit_per_host=30,
            ttl_dns_cache=300,
            keepalive_timeout=30,
            family=socket.AF_INET,
        )

        # No timeout was set before, so aiohttp's default (300s total)
        # applied - a bad connection could silently hang for 5 minutes.
        # These caps make worst-case latency bounded and predictable.
        # connect/sock_connect stay tight (2s) - a real TCP handshake to
        # Discord's CDN should never legitimately take longer than that,
        # and the earlier IPv4 pin already removed the dead-route case.
        #
        # sock_read is generous (5s) because the observed slow cases are
        # media.discordapp.net proxy cache-misses: Discord's proxy has
        # to fetch + transform the original image before it can start
        # streaming bytes, which can occasionally take a few seconds on
        # their end. That's not a stalled/dead connection, just a slow
        # one - killing it at 3s only to retry into the exact same slow
        # path doesn't help, it just doubles the wait before failing.
        timeout = aiohttp.ClientTimeout(
            total=7,
            connect=2,
            sock_connect=2,
            sock_read=5,
        )

        self.http_session = aiohttp.ClientSession(
            connector=connector,
            timeout=timeout
        )

    async def ensure_db_connected(self):
        if not db.pool:
            await db.connect()
        await warm_ping_caches(db)

    async def cog_unload(self):
        await self.http_session.close()
        self.inference_executor.shutdown(wait=False, cancel_futures=True)

    def _default_type_channel_data(self):
        return {
            type_name: []
            for type_name in self.typing_data.keys()
        }

    def _read_type_channel_data(self):
        data = self._default_type_channel_data()

        if TYPE_CHANNELS_PATH.exists():
            with TYPE_CHANNELS_PATH.open(
                "r",
                encoding="utf-8"
            ) as f:
                existing = json.load(f)

            for type_name, channel_ids in existing.items():
                cleaned_ids = []

                for channel_id in channel_ids:
                    try:
                        channel_int = int(channel_id)
                    except (TypeError, ValueError):
                        continue

                    if channel_int not in cleaned_ids:
                        cleaned_ids.append(channel_int)

                data[type_name] = cleaned_ids

        return data

    def _write_type_channel_data(self, data):
        with TYPE_CHANNELS_PATH.open(
            "w",
            encoding="utf-8"
        ) as f:
            json.dump(
                data,
                f,
                indent=2,
                sort_keys=True
            )

    async def sync_type_channel_data(
        self,
        channel_id: int,
        types: list[str]
    ):
        async with self.type_channels_lock:

            data = await asyncio.to_thread(
                self._read_type_channel_data
            )

            for type_name in list(data.keys()):
                data[type_name] = [
                    saved_id
                    for saved_id in data[type_name]
                    if saved_id != channel_id
                ]

            for type_name in types:
                data.setdefault(type_name, [])

                if channel_id not in data[type_name]:
                    data[type_name].append(channel_id)

            await asyncio.to_thread(
                self._write_type_channel_data,
                data
            )

    async def get_saved_channels_for_type(
        self,
        type_name: str
    ):
        async with self.type_channels_lock:

            data = await asyncio.to_thread(
                self._read_type_channel_data
            )

        return data.get(
            type_name.lower(),
            []
        )

    def get_pokemon_types(self, name: str):
        return list(self.name_to_types.get(name.lower(), []))

    def get_pokemon_regions(self, name: str):
        return list(self.name_to_regions.get(name.lower(), []))

    def normalize_name(self, name: str) -> str:
        return re.sub(
            r"[^a-z0-9]+",
            "",
            name.lower()
        )

    def get_rare_flags(self, name: str):
        norm_name = self.normalize_name(name)
        rares = []

        for group, names in self.rare_lookup.items():
            if norm_name in names:
                rares.append(group)

        return rares

    def get_regional_flags(self, name: str):
        norm_name = self.normalize_name(name)
        regionals = []

        for group, names in self.regional_lookup.items():
            if norm_name in names:
                regionals.append(group)

        return regionals

    def get_eeveelutions_paradox_flags(self, name: str):
        norm_name = self.normalize_name(name)
        ep = []

        for group, names in self.eeveelutions_paradox_lookup.items():
            if norm_name in names:
                ep.append(group)

        return ep

    def get_gigantamax_flags(self, name: str):
        norm_name = self.normalize_name(name)
        gmax = []

        for group, names in self.gigantamax_lookup.items():
            if norm_name in names:
                gmax.append(group)

        return gmax

    def _read_recognition_enabled(self) -> bool:
        try:
            with RECOG_PATH.open(
                "r",
                encoding="utf-8"
            ) as f:
                data = json.load(f)

            value = data.get(
                "recognition",
                data.get(
                    "recoginition",
                    True
                )
            )

            return bool(value)

        except FileNotFoundError:
            return True

        except Exception as e:
            print(
                f"⚠️ Failed to read {RECOG_PATH}: {e}"
            )

            return True

    def is_recognition_enabled(self) -> bool:
        return self._recognition_enabled

    def _read_type_enabled(self) -> bool:
        try:
            with TYPE_SETTINGS_PATH.open(
                "r",
                encoding="utf-8"
            ) as f:
                data = json.load(f)

            value = data.get(
                "type",
                data.get(
                    "enabled",
                    True
                )
            )

            return bool(value)

        except FileNotFoundError:
            return True

        except Exception as e:
            print(
                f"⚠️ Failed to read {TYPE_SETTINGS_PATH}: {e}"
            )

            return True

    def is_type_enabled(self) -> bool:
        return self._type_enabled

    async def set_type_enabled(
        self,
        enabled: bool
    ):
        self._type_enabled = enabled

        def _write():
            with TYPE_SETTINGS_PATH.open(
                "w",
                encoding="utf-8"
            ) as f:
                json.dump(
                    {"type": enabled},
                    f,
                    indent=2,
                    sort_keys=True
                )

        await asyncio.to_thread(_write)

    async def _download_image_bytes(
        self,
        image_url: str
    ):
        """
        Download image bytes with one retry on timeout/connection error.

        Per-attempt sock_read is generous (5s, set on the session's
        ClientTimeout) to tolerate slow media.discordapp.net proxy
        cache-misses. But that means two attempts back-to-back could
        otherwise stack up to ~14s worst case - so the whole function
        (both attempts) is wrapped in an outer 8s budget. Whichever
        limit hits first wins; the retry only gets whatever time is
        left in that budget, and a slow-but-alive attempt 1 that eats
        most of the budget will get a correspondingly short (or zero)
        attempt 2 rather than a fresh full window.
        """
        async def _attempts():
            last_exc = None

            for attempt in range(2):
                try:
                    async with self.http_session.get(
                        image_url
                    ) as resp:

                        if resp.status != 200:
                            return None, f"❌ Download failed (HTTP {resp.status})."

                        return await resp.read(), None

                except (
                    asyncio.TimeoutError,
                    aiohttp.ClientConnectionError,
                    aiohttp.ServerDisconnectedError,
                ) as e:
                    last_exc = e

                    if attempt == 0:
                        continue

            return None, f"❌ Download failed after retry: {last_exc}"

        try:
            return await asyncio.wait_for(_attempts(), timeout=8)

        except asyncio.TimeoutError:
            return None, "❌ Download failed: exceeded 8s overall budget."

    async def predict_image_url(
        self,
        image_url: str
    ):
        try:
            img_bytes, dl_error = await self._download_image_bytes(
                image_url
            )

            if dl_error:
                return (
                    None,
                    None,
                    dl_error
                )

            loop = asyncio.get_event_loop()

            probs, _preprocess_ms, _inference_ms = await loop.run_in_executor(
                self.inference_executor,
                functools.partial(
                    run_full_inference,
                    self.ort_session,
                    img_bytes
                )
            )

            top_idx = int(
                np.argmax(probs)
            )

            confidence = float(
                probs[top_idx]
            )

            name = self.idx_to_name[top_idx]

            return (
                name,
                confidence,
                None
            )

        except Exception as e:
            return (
                None,
                None,
                f"❌ Prediction failed: {e}"
            )

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message
    ):

        # If Poketwo posted a catch confirmation,
        # remove this channel from saved type channels
        if (
            message.author.id == POKETWO_BOT_ID
            and message.content
            and CATCH_RE.search(message.content)
        ):
            if self.is_type_enabled():
                try:
                    await self._remove_channel_from_all_types(
                        message.channel.id
                    )

                except Exception as e:
                    print(
                        f"⚠️ Failed to remove channel after catch: {e}"
                    )

            return

        if (
            message.author.id != POKETWO_BOT_ID
            or not message.embeds
            or not (
                message.embeds[0].image
                and message.embeds[0].image.url
            )
        ):
            return

        # Only predict images from the two valid wild-spawn embed titles.
        embed_title = message.embeds[0].title or ""
        if not (
            embed_title == "A wild pokémon has appeared!"
            or re.fullmatch(
                r"Wild .+ fled\. A new wild pokémon has appeared!",
                embed_title
            )
        ):
            return

        if not self.is_recognition_enabled():
            return

        try:

            overall_start = time.perf_counter()
            image_url = message.embeds[0].image.url

            # Start the CDN download immediately so incense checks do not
            # add onto image latency.
            download_start = time.perf_counter()
            download_task = asyncio.create_task(
                self._download_image_bytes(image_url)
            )

            # Check incense tracking & max limits
            embed_footer = (
                message.embeds[0].footer.text
                if message.embeds[0].footer
                else None
            )

            is_active_incense, is_20s, remaining = parse_incense_footer(embed_footer)

            incense_allowed = await update_and_check_incense(
                message.guild.id if message.guild else None,
                message.channel.id,
                embed_footer,
                db
            )

            # If this is an active incense spawn, enforce incense limit checks.
            # Normal spawns (no active incense footer) are predicted unconditionally.
            if is_active_incense and is_20s and remaining is not None:
                if not incense_allowed:
                    download_task.cancel()
                    return

            # 1. Download image
            img_bytes, dl_error = await download_task

            download_ms = (
                time.perf_counter()
                - download_start
            ) * 1000

            if dl_error:
                print(
                    f"[spawn] download failed after "
                    f"{download_ms:.1f}ms: {dl_error}"
                )
                return

            # 2 & 3. Decode + preprocess + inference, all offloaded
            # together so none of it blocks the event loop.
            loop = asyncio.get_event_loop()

            probs, preprocess_ms, inference_ms = await loop.run_in_executor(
                self.inference_executor,
                functools.partial(
                    run_full_inference,
                    self.ort_session,
                    img_bytes
                )
            )

            top_idx = int(
                np.argmax(probs)
            )

            confidence = float(
                probs[top_idx]
            )

            name = self.idx_to_name[top_idx]
            display_name = name[:1].upper() + name[1:]

            if confidence < CONF_THRESHOLD:
                await message.channel.send(
                    "❌ Not confident enough to guess this Pokémon. "
                    "<@1397199191792685056>"
                )

                return

            total_ms = (
                time.perf_counter()
                - overall_start
            ) * 1000

            print(
                f"[spawn] "
                f"download={download_ms:.1f}ms "
                f"preprocess={preprocess_ms:.1f}ms "
                f"inference={inference_ms:.1f}ms "
                f"total={total_ms:.1f}ms "
                f"name={name} "
                f"conf={confidence:.3%}"
            )

            lookup_start = time.perf_counter()

            hunters, collectors, reserves, type_ping_users, region_ping_users = await asyncio.gather(
                get_users_hunting(name, db),
                get_users_collecting(name, message.guild.id, db),
                get_users_reserved(name, message.guild.id, db),
                get_users_type_ping(name, message.guild.id, db),
                get_users_region_ping(name, message.guild.id, db),
            )

            types = self.get_pokemon_types(name)
            rares = self.get_rare_flags(name)
            regionals = self.get_regional_flags(name)

            eeveelutions_paradox = (
                self.get_eeveelutions_paradox_flags(
                    name
                )
            )

            gigantamax = (
                self.get_gigantamax_flags(
                    name
                )
            )

            if types and self.is_type_enabled():
                asyncio.create_task(
                    self.sync_type_channel_data(
                        message.channel.id,
                        types
                    )
                )

            rare_role_mentions = []

            if rares:
                role = discord.utils.get(
                    message.guild.roles,
                    name="Rares"
                )

                if role:
                    rare_role_mentions.append(
                        role.mention
                    )

            regional_role_mentions = []

            if regionals:
                role = discord.utils.get(
                    message.guild.roles,
                    name="Regionals"
                )

                if role:
                    regional_role_mentions.append(
                        role.mention
                    )

            eeveelutions_paradox_role_mentions = []

            if eeveelutions_paradox:
                role = discord.utils.get(
                    message.guild.roles,
                    name="Eeveelutions & Paradox"
                )

                if role:
                    eeveelutions_paradox_role_mentions.append(
                        role.mention
                    )

            gigantamax_role_mentions = []

            if gigantamax:
                role = discord.utils.get(
                    message.guild.roles,
                    name="Gigantamax"
                )

                if role:
                    gigantamax_role_mentions.append(
                        role.mention
                    )

            # ==================================================
            # PREDICTION MESSAGE
            # ==================================================

            reply_lines = [
                f"{display_name}: {confidence:.2%}",
            ]

            # ==================================================
            # RESERVE OVERRIDE
            #
            # If there is ANY reservation:
            # ONLY reserve pings are sent.
            #
            # No:
            # - shiny hunt pings
            # - collection pings
            # - rare role pings
            # - regional role pings
            # - Eeveelutions/Paradox role pings
            # - Gigantamax role pings
            # ==================================================

            if reserves:

                reply_lines.append(
                    "Reserve Pings: "
                    + ", ".join(
                        f"<@{uid}>"
                        for uid in reserves
                    )
                )

            else:

                # Normal ping behavior
                # when there is NO reservation.

                if hunters:
                    reply_lines.append(
                        "Shiny Hunt Pings: "
                        + ", ".join(
                            f"<@{uid}>"
                            for uid in hunters
                        )
                    )

                if collectors:
                    reply_lines.append(
                        "Collection Pings: "
                        + ", ".join(
                            f"<@{uid}>"
                            for uid in collectors
                        )
                    )

                if type_ping_users:
                    reply_lines.append(
                        "Type Pings: "
                        + ", ".join(
                            f"<@{uid}>"
                            for uid in type_ping_users
                        )
                    )

                if region_ping_users:
                    reply_lines.append(
                        "Region Pings: "
                        + ", ".join(
                            f"<@{uid}>"
                            for uid in region_ping_users
                        )
                    )

                if rare_role_mentions:
                    reply_lines.append(
                        "Rare Pings: "
                        + ", ".join(
                            rare_role_mentions
                        )
                    )

                if regional_role_mentions:
                    reply_lines.append(
                        "Regional Pings: "
                        + ", ".join(
                            regional_role_mentions
                        )
                    )

                if eeveelutions_paradox_role_mentions:
                    reply_lines.append(
                        "Eeveelutions & Paradox Pings: "
                        + ", ".join(
                            eeveelutions_paradox_role_mentions
                        )
                    )

                if gigantamax_role_mentions:
                    reply_lines.append(
                        "Gigantamax Pings: "
                        + ", ".join(
                            gigantamax_role_mentions
                        )
                    )

            reply_lines.append(
                f"Copy-Command: `<@{POKETWO_BOT_ID}> c {name}`"
            )

            await message.channel.send(
                "\n".join(reply_lines)
            )

            lookup_ms = (time.perf_counter() - lookup_start) * 1000
            print(
                f"[spawn] lookup+send={lookup_ms:.1f}ms name={name}"
            )

            self._record_spawn_timing(
                inference_ms,
                (time.perf_counter() - overall_start) * 1000
            )

        except Exception as e:
            print(
                "⚠️ Prediction error:",
                e
            )

    async def _remove_channel_from_all_types(
        self,
        channel_id: int
    ):
        async with self.type_channels_lock:

            data = await asyncio.to_thread(
                self._read_type_channel_data
            )

            removed_types = []
            changed = False

            for type_name in list(data.keys()):

                if channel_id in data[type_name]:

                    data[type_name] = [
                        cid
                        for cid in data[type_name]
                        if cid != channel_id
                    ]

                    removed_types.append(
                        type_name
                    )

                    changed = True

            if changed:
                await asyncio.to_thread(
                    self._write_type_channel_data,
                    data
                )

        return removed_types

    def _record_spawn_timing(self, inference_ms: float, response_ms: float):
        for key, value in (
            ("inference", inference_ms),
            ("response", response_ms),
        ):
            stats = self.timing_stats[key]
            if stats["min"] is None or value < stats["min"]:
                stats["min"] = value
            if stats["max"] is None or value > stats["max"]:
                stats["max"] = value
            stats["sum"] += value
            stats["count"] += 1

    def _format_ms(self, value) -> str:
        if value is None:
            return "`N/A`"
        return f"`{value:.2f}ms`"

    def _format_stat(self, key: str, kind: str) -> str:
        stats = self.timing_stats[key]
        if not stats["count"]:
            return "`N/A`"
        if kind == "min":
            return self._format_ms(stats["min"])
        if kind == "max":
            return self._format_ms(stats["max"])
        return self._format_ms(stats["sum"] / stats["count"])

    @commands.command(name="ping")
    @commands.guild_only()
    async def ping_type_channels(
        self,
        ctx: commands.Context,
        *,
        type_name: str = ""
    ):
        type_name = (type_name or "").strip().lower()

        if not type_name:
            latency_ms = self.bot.latency * 1000
            await ctx.send(f"🏓 Pong! `{latency_ms:.0f}ms`")
            return

        if not self.is_type_enabled():
            await ctx.send(
                "❌ Type/region prediction info is currently disabled. "
                "Use `m!type` to turn it back on."
            )
            return

        if type_name not in self.typing_data:
            await ctx.send(
                f"❌ Unknown type: {type_name}"
            )
            return

        channel_ids = await self.get_saved_channels_for_type(
            type_name
        )

        if not channel_ids:
            await ctx.send(
                f"❌ No saved channels for {type_name}."
            )
            return

        sent_count = 0

        for channel_id in channel_ids:

            channel = (
                ctx.guild.get_channel(channel_id)
                or self.bot.get_channel(channel_id)
            )

            if channel is None:
                continue

            try:
                await channel.send(
                    ctx.author.mention
                )

                sent_count += 1

            except (
                discord.Forbidden,
                discord.HTTPException
            ):
                continue

        await ctx.send(
            f"✅ Pinged {ctx.author.mention} "
            f"in {sent_count} channel(s) for {type_name}."
        )

    @commands.command(name="type")
    @commands.guild_only()
    async def toggle_type_info(
        self,
        ctx: commands.Context
    ):

        new_value = not self.is_type_enabled()

        await self.set_type_enabled(
            new_value
        )

        state = (
            "enabled"
            if new_value
            else "disabled"
        )

        await ctx.send(
            f"✅ Type/region prediction info is now **{state}**."
        )

    @commands.command(name="predict")
    @commands.guild_only()
    async def predict_reply_embed(
        self,
        ctx: commands.Context
    ):

        reference = ctx.message.reference

        if (
            not reference
            or not reference.message_id
        ):
            await ctx.send(
                "❌ Reply to a message that has an embed image, "
                "then use `m!predict`."
            )
            return

        try:

            target_message = reference.resolved

            if (
                target_message is None
                or not isinstance(
                    target_message,
                    discord.Message
                )
            ):
                target_message = (
                    await ctx.channel.fetch_message(
                        reference.message_id
                    )
                )

        except (
            discord.NotFound,
            discord.Forbidden,
            discord.HTTPException
        ):
            await ctx.send(
                "❌ Could not access the replied message."
            )
            return

        if not target_message.embeds:
            await ctx.send(
                "❌ The replied message has no embeds."
            )
            return

        embed_with_image = next(
            (
                embed
                for embed in target_message.embeds
                if embed.image
                and embed.image.url
            ),
            None,
        )

        if embed_with_image is None:
            await ctx.send(
                "❌ The replied embed does not contain an image."
            )
            return

        name, confidence, error = (
            await self.predict_image_url(
                embed_with_image.image.url
            )
        )

        if error:
            await ctx.send(error)
            return

        if confidence < CONF_THRESHOLD:
            await ctx.send(
                f"❌ Not confident enough to guess this Pokémon "
                f"({confidence:.3%})."
            )
            return

        await ctx.send(
            f"Best Name: **{name}**\n"
            f"Confidence: {confidence:.3%}"
        )


async def setup(bot):
    await bot.add_cog(
        SpawnPredictor(bot)
    )