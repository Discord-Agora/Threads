from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum, auto
from logging.handlers import RotatingFileHandler
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    DefaultDict,
    Dict,
    Generator,
    List,
    Literal,
    Mapping,
    Optional,
    Sequence,
    Set,
    Tuple,
    Union,
)

import aiofiles
import aiofiles.os
import interactions
import orjson
import StarCC
from cachetools import TTLCache
from interactions.api.events import (
    ExtensionLoad,
    ExtensionUnload,
    MessageCreate,
    NewThreadCreate,
)
from interactions.client.errors import Forbidden, NotFound
from interactions.ext.paginators import Paginator
from yarl import URL

BASE_DIR: str = os.path.abspath(os.path.dirname(__file__))
LOG_FILE: str = os.path.join(BASE_DIR, "threads.log")

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
formatter = logging.Formatter(
    "%(asctime)s | %(process)d:%(thread)d | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
    "%Y-%m-%d %H:%M:%S.%f %z",
)
file_handler = RotatingFileHandler(
    LOG_FILE, maxBytes=1024 * 1024, backupCount=1, encoding="utf-8"
)
file_handler.setFormatter(formatter)
logger.addHandler(file_handler)

# Model


class ActionType(Enum):
    LOCK = auto()
    UNLOCK = auto()
    BAN = auto()
    UNBAN = auto()
    DELETE = auto()
    EDIT = auto()
    PIN = auto()
    UNPIN = auto()
    SHARE_PERMISSIONS = auto()
    REVOKE_PERMISSIONS = auto()


class EmbedColor(Enum):
    OFF = 0x5D5A58
    FATAL = 0xFF4343
    ERROR = 0xE81123
    WARN = 0xFFB900
    INFO = 0x0078D7
    DEBUG = 0x00B7C3
    TRACE = 0x8E8CD8
    ALL = 0x0063B1


@dataclass
class ActionDetails:
    action: ActionType
    reason: str
    post_name: str
    actor: interactions.Member
    target: Optional[interactions.Member] = None
    result: str = "successful"
    channel: Optional[interactions.GuildForumPost] = None
    additional_info: Optional[Mapping[str, Any]] = None


@dataclass
class PostStats:
    message_count: int = 0
    last_activity: datetime = datetime.now(timezone.utc)


class Model:
    def __init__(self) -> None:
        self.banned_users: DefaultDict[str, DefaultDict[str, Set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        self.thread_permissions: DefaultDict[str, Set[str]] = defaultdict(set)
        self.ban_cache: Dict[Tuple[str, str, str], Tuple[bool, datetime]] = {}
        self.CACHE_DURATION: timedelta = timedelta(minutes=5)
        self.post_stats: Dict[str, PostStats] = {}
        self.featured_posts: Dict[str, str] = {}
        self.current_pinned_post: Optional[str] = None
        self.converters: Dict[str, StarCC.PresetConversion] = {}

    async def load_banned_users(self, file_path: str) -> None:
        try:
            async with aiofiles.open(file_path, mode="rb") as file:
                content: bytes = await file.read()
                loaded_data: Dict[str, Dict[str, list]] = (
                    orjson.loads(content) if content.strip() else {}
                )

            self.banned_users.clear()
            self.banned_users.update(
                {
                    channel_id: defaultdict(
                        set,
                        {
                            post_id: set(user_list)
                            for post_id, user_list in channel_data.items()
                        },
                    )
                    for channel_id, channel_data in loaded_data.items()
                }
            )
        except FileNotFoundError:
            logger.warning(
                f"Banned users file not found: {file_path}. Creating a new one"
            )
            await self.save_banned_users(file_path)
        except orjson.JSONDecodeError as e:
            logger.error(f"Error decoding JSON data: {e}")
        except Exception as e:
            logger.error(
                f"Unexpected error loading banned users data: {e}", exc_info=True
            )

    async def save_banned_users(self, file_path: str) -> None:
        try:
            serializable_banned_users: Dict[str, Dict[str, List[str]]] = {
                channel_id: {
                    post_id: list(user_set)
                    for post_id, user_set in channel_data.items()
                }
                for channel_id, channel_data in self.banned_users.items()
            }

            json_data: bytes = orjson.dumps(
                serializable_banned_users,
                option=orjson.OPT_INDENT_2
                | orjson.OPT_SORT_KEYS
                | orjson.OPT_SERIALIZE_NUMPY,
            )

            async with aiofiles.open(file_path, mode="wb") as file:
                await file.write(json_data)

            logger.info(f"Successfully saved banned users data to {file_path}")
        except Exception as e:
            logger.error(f"Error saving banned users data: {e}", exc_info=True)

    async def save_thread_permissions(self, file_path: str) -> None:
        try:
            serializable_permissions: Dict[str, List[str]] = {
                k: list(v) for k, v in self.thread_permissions.items()
            }
            json_data: bytes = orjson.dumps(
                serializable_permissions, option=orjson.OPT_INDENT_2
            )

            async with aiofiles.open(file_path, mode="wb") as file:
                await file.write(json_data)

            logger.info(f"Successfully saved thread permissions to {file_path}")
        except Exception as e:
            logger.error(f"Error saving thread permissions: {e}", exc_info=True)

    async def load_thread_permissions(self, file_path: str) -> None:
        try:
            async with aiofiles.open(file_path, mode="rb") as file:
                content: bytes = await file.read()
                loaded_data: Dict[str, List[str]] = orjson.loads(content)

            self.thread_permissions.clear()
            self.thread_permissions.update({k: set(v) for k, v in loaded_data.items()})

            logger.info(f"Successfully loaded thread permissions from {file_path}")
        except FileNotFoundError:
            logger.warning(
                f"Thread permissions file not found: {file_path}. Creating a new one"
            )
            await self.save_thread_permissions(file_path)
        except Exception as e:
            logger.error(f"Error loading thread permissions: {e}", exc_info=True)

    async def load_post_stats(self, file_path: str) -> None:
        try:
            async with aiofiles.open(file_path, mode="rb") as file:
                content: bytes = await file.read()
                loaded_data: Dict[str, Dict[str, Any]] = (
                    {} if not content.strip() else orjson.loads(content)
                )

            self.post_stats = {
                post_id: PostStats(
                    message_count=data.get("message_count", 0),
                    last_activity=datetime.fromisoformat(data["last_activity"]),
                )
                for post_id, data in loaded_data.items()
            }
            logger.info(f"Successfully loaded post stats from {file_path}")
        except FileNotFoundError:
            logger.warning(
                f"Thread stats file not found: {file_path}. Creating a new one"
            )
            await self.save_post_stats(file_path)
        except orjson.JSONDecodeError as e:
            logger.error(f"Error decoding JSON data: {e}")
        except Exception as e:
            logger.error(
                f"Unexpected error loading post stats data: {e}", exc_info=True
            )

    async def save_post_stats(self, file_path: str) -> None:
        try:
            serializable_stats: Dict[str, Dict[str, Any]] = {
                post_id: {
                    "message_count": stats.message_count,
                    "last_activity": stats.last_activity.isoformat(),
                }
                for post_id, stats in self.post_stats.items()
            }
            json_data: bytes = orjson.dumps(
                serializable_stats,
                option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS,
            )
            async with aiofiles.open(file_path, mode="wb") as file:
                await file.write(json_data)
            logger.info(f"Successfully saved post stats to {file_path}")
        except Exception as e:
            logger.error(f"Error saving post stats data: {e}", exc_info=True)

    async def save_featured_posts(self, file_path: str) -> None:
        try:
            json_data = orjson.dumps(
                self.featured_posts, option=orjson.OPT_INDENT_2 | orjson.OPT_SORT_KEYS
            )
            async with aiofiles.open(file_path, mode="wb") as file:
                await file.write(json_data)
            logger.info(f"Successfully saved selected posts to {file_path}")
        except Exception as e:
            logger.exception(f"Error saving selected posts to {file_path}: {e}")

    async def load_featured_posts(self, file_path: str) -> None:
        try:
            async with aiofiles.open(file_path, mode="rb") as file:
                content = await file.read()
                self.featured_posts = orjson.loads(content) if content else {}
            logger.info(f"Successfully loaded selected posts from {file_path}")
        except FileNotFoundError:
            logger.warning(
                f"Selected posts file not found: {file_path}. Creating a new one"
            )
            await self.save_featured_posts(file_path)
        except orjson.JSONDecodeError as json_err:
            logger.error(f"JSON decoding error in selected posts: {json_err}")
        except Exception as e:
            logger.exception(f"Unexpected error while loading selected posts: {e}")

    def is_user_banned(self, channel_id: str, post_id: str, user_id: str) -> bool:
        cache_key: Tuple[str, str, str] = (channel_id, post_id, user_id)
        current_time: datetime = datetime.now(timezone.utc)

        if cache_key in self.ban_cache:
            cached_result, timestamp = self.ban_cache[cache_key]
            if current_time - timestamp < self.CACHE_DURATION:
                return cached_result

        result: bool = user_id in self.banned_users[channel_id][post_id]
        self.ban_cache[cache_key] = (result, current_time)
        return result

    async def invalidate_ban_cache(
        self, channel_id: str, post_id: str, user_id: str
    ) -> None:
        self.ban_cache.pop((channel_id, post_id, user_id), None)

    def has_thread_permissions(self, post_id: str, user_id: str) -> bool:
        return user_id in self.thread_permissions[post_id]

    def get_banned_users(self) -> Generator[Tuple[str, str, str], None, None]:
        return (
            (channel_id, post_id, user_id)
            for channel_id, channel_data in self.banned_users.items()
            for post_id, user_set in channel_data.items()
            for user_id in user_set
        )

    def get_thread_permissions(self) -> Generator[Tuple[str, str], None, None]:
        return (
            (post_id, user_id)
            for post_id, user_set in self.thread_permissions.items()
            for user_id in user_set
        )


# Decorator


def log_action(func):
    @functools.wraps(func)
    async def wrapper(self, ctx: interactions.CommandContext, *args, **kwargs):
        action_details: Optional[ActionDetails] = None
        try:
            result = await func(self, ctx, *args, **kwargs)
            if isinstance(result, ActionDetails):
                action_details = result
            else:
                return result
        except Exception as e:
            error_message: str = str(e)
            await self.send_error(ctx, error_message)
            action_details = ActionDetails(
                action=ActionType.DELETE,
                reason=f"Error: {error_message}",
                post_name=(
                    ctx.channel.name
                    if isinstance(ctx.channel, interactions.GuildForumPost)
                    else "Unknown"
                ),
                actor=ctx.author,
                result="failed",
                channel=(
                    ctx.channel
                    if isinstance(ctx.channel, interactions.GuildForumPost)
                    else None
                ),
            )
            raise
        finally:
            if action_details:
                await asyncio.shield(self.log_action_internal(action_details))
        return result

    return wrapper


# Controller


class Threads(interactions.Extension):
    def __init__(self, bot: interactions.Client) -> None:
        self.bot: interactions.Client = bot
        self.model: Model = Model()
        self.ban_lock: asyncio.Lock = asyncio.Lock()
        self.conversion_task: Optional[asyncio.Task] = None

        self.BANNED_USERS_FILE: str = os.path.join(
            os.path.dirname(__file__), "banned_users.json"
        )
        self.THREAD_PERMISSIONS_FILE: str = os.path.join(
            os.path.dirname(__file__), "thread_permissions.json"
        )
        self.POST_STATS_FILE: str = os.path.join(BASE_DIR, "post_stats.json")
        self.FEATURED_POSTS_FILE: str = os.path.join(BASE_DIR, "featured_posts.json")
        self.LOG_CHANNEL_ID: int = 1166627731916734504
        self.LOG_FORUM_ID: int = 1159097493875871784
        self.LOG_POST_ID: int = 1279118293936111707
        self.POLL_FORUM_ID: Tuple[int, ...] = (1155914521907568740,)
        self.TAIWAN_ROLE_ID: int = 1261328929013108778
        self.THREADS_ROLE_ID: int = 1223635198327914639
        self.GUILD_ID: int = 1150630510696075404
        self.CONGRESS_ID: int = 1196707789859459132
        self.CONGRESS_MEMBER_ROLE: int = 1200254783110525010
        self.CONGRESS_MOD_ROLE: int = 1300132191883235368
        self.ROLE_CHANNEL_PERMISSIONS: Dict[int, Tuple[int, ...]] = {
            1223635198327914639: (
                1152311220557320202,
                1168209956802142360,
                1230197011761074340,
                1155914521907568740,
                1169032829548630107,
                1213345198147637268,
                1183254117813071922,
                1250396377540853801,
            ),
            1213490790341279754: (1185259262654562355,),
        }
        self.ALLOWED_CHANNELS: Tuple[int, ...] = (
            1152311220557320202,
            1168209956802142360,
            1230197011761074340,
            1155914521907568740,
            1169032829548630107,
            1185259262654562355,
            1183048643071180871,
            1213345198147637268,
            1183254117813071922,
            1196707789859459132,
            1250396377540853801,
        )
        self.FEATURED_CHANNELS: Tuple[int, ...] = (1152311220557320202,)
        self.TIMEOUT_CHANNEL_IDS: Tuple[int, ...] = (
            1299458193507881051,
            1150630511136481322,
        )
        self.TIMEOUT_REQUIRED_DIFFERENCE: int = 5
        self.TIMEOUT_DURATION: int = 30

        self.active_timeout_polls: Dict[int, asyncio.Task] = {}
        self.message_count_threshold: int = 200
        self.rotation_interval: timedelta = timedelta(hours=23)
        self.url_cache: TTLCache = TTLCache(maxsize=1024, ttl=3600)
        self.last_threshold_adjustment: datetime = datetime.now(
            timezone.utc
        ) - timedelta(days=8)

        asyncio.create_task(self.initialize_data())

    async def initialize_data(self) -> None:
        await asyncio.gather(
            self.model.load_banned_users(self.BANNED_USERS_FILE),
            self.model.load_thread_permissions(self.THREAD_PERMISSIONS_FILE),
            self.model.load_post_stats(self.POST_STATS_FILE),
            self.model.load_featured_posts(self.FEATURED_POSTS_FILE),
        )

    # Tag operations

    async def increment_message_count(self, post_id: str) -> None:
        stats = self.model.post_stats.setdefault(post_id, PostStats())
        stats.message_count += 1
        stats.last_activity = datetime.now(timezone.utc)
        await self.model.save_post_stats(self.POST_STATS_FILE)

    async def update_featured_posts_tags(self) -> None:
        logger.info(
            "Updating featured posts tags with threshold: %d",
            self.message_count_threshold,
        )
        logger.debug("Current featured posts: %s", self.model.featured_posts)

        eligible_posts = {
            post_id
            for forum_id in self.FEATURED_CHANNELS
            if (post_id := self.model.featured_posts.get(str(forum_id)))
            and (stats := self.model.post_stats.get(post_id))
            and stats.message_count >= self.message_count_threshold
        }

        for post_id in eligible_posts:
            try:
                await self.add_tag_to_post(post_id)
            except Exception as e:
                logger.error("Failed to add tag to post %s: %s", post_id, e)
                continue

        logger.info("Featured posts tags update completed successfully")

    async def add_tag_to_post(self, post_id: str) -> None:
        try:
            channel = await self.bot.fetch_channel(int(post_id))
            forum = await self.bot.fetch_channel(channel.parent_id)

            if not all(
                (
                    isinstance(channel, interactions.GuildForumPost),
                    isinstance(forum, interactions.GuildForum),
                )
            ):
                return

            featured_tag_id = 1275098388718813215
            current_tags = frozenset(tag.id for tag in channel.applied_tags)

            if featured_tag_id not in current_tags:
                new_tags = list(current_tags | {featured_tag_id})
                if len(new_tags) <= 5:
                    await channel.edit(applied_tags=new_tags)
                    logger.info(f"Added featured tag to post {post_id}")

        except (ValueError, NotFound, Exception) as e:
            logger.error(f"Error adding featured tag to post {post_id}: {e}")

    async def pin_featured_post(self, new_post_id: str) -> None:
        try:
            new_post = await self.bot.fetch_channel(int(new_post_id))
            if not isinstance(new_post, interactions.GuildForumPost):
                return

            forum = await self.bot.fetch_channel(new_post.parent_id)
            if not isinstance(forum, interactions.GuildForum):
                return

            posts = await forum.fetch_posts()
            pinned_posts = [post for post in posts if post.pinned]

            for post in pinned_posts:
                try:
                    await post.unpin(reason="Rotating featured posts.")
                    await asyncio.sleep(0.25)
                except Exception as e:
                    logger.error(f"Failed to unpin post {post.id}: {e}")
                    return

            if not new_post.pinned:
                await new_post.pin(reason="New featured post.")
                await asyncio.sleep(0.25)

                updated_post = await self.bot.fetch_channel(int(new_post_id))
                if (
                    isinstance(updated_post, interactions.GuildForumPost)
                    and updated_post.pinned
                ):
                    self.model.current_pinned_post = new_post_id
                else:
                    logger.error(f"Failed to pin new post {new_post_id}")
                    return
            else:
                self.model.current_pinned_post = new_post_id

            posts = await forum.fetch_posts()
            final_pinned = [post for post in posts if post.pinned]
            if len(final_pinned) > 1:
                logger.warning(f"Multiple posts pinned in channel {new_post.parent_id}")

        except (ValueError, NotFound) as e:
            logger.error(f"Error pinning post {new_post_id}: {e}")
        except Exception as e:
            logger.error(f"Unexpected error pinning new post: {e}", exc_info=True)

    async def update_featured_posts_rotation(self) -> None:
        forum_ids: Sequence[int] = tuple(self.FEATURED_CHANNELS)

        top_posts: list[Optional[str]] = []
        tasks = [self.get_top_post_id(fid) for fid in forum_ids]
        for task in asyncio.as_completed(tasks):
            result = await task
            top_posts.append(result)

        updates: tuple[tuple[int, str], ...] = tuple(
            (forum_id, new_post_id)
            for forum_id, new_post_id in zip(forum_ids, top_posts)
            if new_post_id
            and new_post_id != self.model.featured_posts.get(str(forum_id))
        )

        if not updates:
            return

        featured_posts_update = {
            str(forum_id): new_post_id for forum_id, new_post_id in updates
        }
        self.model.featured_posts.update(featured_posts_update)

        for forum_id, new_post_id in updates:
            current = self.model.featured_posts.get(str(forum_id))
            logger.info(
                "".join(
                    [
                        "Rotating featured post for forum ",
                        str(forum_id),
                        " from ",
                        str(current),
                        " to ",
                        new_post_id,
                    ]
                )
            )

        try:
            await self.model.save_featured_posts(self.FEATURED_POSTS_FILE)
            await self.update_featured_posts_tags()

            for _, post_id in updates:
                await self.pin_featured_post(post_id)

            logger.info("Completed featured posts rotation successfully")

        except Exception as e:
            logger.error(
                f"Failed to complete featured posts rotation: {e}", exc_info=True
            )
            raise

    async def get_top_post_id(self, forum_id: int) -> Optional[str]:
        try:
            forum_channel: interactions.GuildChannel = await self.bot.fetch_channel(
                forum_id
            )
            if not isinstance(forum_channel, interactions.GuildForum):
                logger.warning(f"Channel ID {forum_id} is not a forum channel")
                return None

            posts: List[interactions.GuildForumPost] = await forum_channel.fetch_posts()
            stats_dict: Dict[str, PostStats] = self.model.post_stats

            valid_posts: List[interactions.GuildForumPost] = [
                post for post in posts if str(post.id) in stats_dict
            ]

            if not valid_posts:
                return None

            top_post: interactions.GuildForumPost = max(
                valid_posts, key=(lambda p: stats_dict[str(p.id)].message_count)
            )

            return str(top_post.id)

        except Exception as e:
            logger.error(
                f"Unexpected error fetching top post for forum {forum_id}: {e}",
                exc_info=True,
            )
            return None

    async def adjust_thresholds(self) -> None:
        current_time: datetime = datetime.now(timezone.utc)
        post_stats: tuple[PostStats, ...] = tuple(self.model.post_stats.values())

        if not post_stats:
            logger.info("No posts available to adjust thresholds.")
            return

        total_posts: int = len(post_stats)
        total_messages: int = sum(stat.message_count for stat in post_stats)
        average_messages: float = total_messages / total_posts

        self.message_count_threshold = int(average_messages)

        one_day_ago: datetime = current_time - timedelta(days=1)
        recent_activity: int = sum(
            1 for stat in post_stats if stat.last_activity >= one_day_ago
        )

        self.rotation_interval = (
            timedelta(hours=12)
            if recent_activity > 100
            else timedelta(hours=48) if recent_activity < 10 else timedelta(hours=24)
        )

        activity_threshold: int = 50
        adjustment_period: timedelta = timedelta(days=7)
        minimum_threshold: int = 10

        if (
            average_messages < activity_threshold
            and (current_time - self.last_threshold_adjustment) > adjustment_period
        ):
            self.rotation_interval = timedelta(hours=12)
            self.message_count_threshold = max(
                minimum_threshold, self.message_count_threshold >> 1
            )
            self.last_threshold_adjustment = current_time

            logger.info(
                f"Standards not met for over a week. Adjusted thresholds: message_count_threshold={self.message_count_threshold}, rotation_interval={self.rotation_interval}"
            )

        logger.info(
            f"Threshold adjustment complete: message_count_threshold={self.message_count_threshold}, rotation_interval={self.rotation_interval}"
        )

    # View methods

    async def create_embed(
        self,
        title: str,
        description: str = "",
        color: Union[EmbedColor, int] = EmbedColor.INFO,
    ) -> interactions.Embed:
        color_value: int = color.value if isinstance(color, EmbedColor) else color

        embed: interactions.Embed = interactions.Embed(
            title=title, description=description, color=color_value
        )

        guild: Optional[interactions.Guild] = await self.bot.fetch_guild(self.GUILD_ID)
        if guild and guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)

        embed.timestamp = datetime.now(timezone.utc)
        embed.set_footer(text="鍵政大舞台")
        return embed

    @functools.lru_cache(maxsize=1)
    def get_log_channels(self) -> tuple[int, int, int]:
        return (
            self.LOG_CHANNEL_ID,
            self.LOG_POST_ID,
            self.LOG_FORUM_ID,
        )

    async def send_response(
        self,
        ctx: Optional[
            Union[
                interactions.SlashContext,
                interactions.InteractionContext,
                interactions.ComponentContext,
            ]
        ],
        title: str,
        message: str,
        color: EmbedColor,
        log_to_channel: bool = True,
    ) -> None:
        embed: interactions.Embed = await self.create_embed(title, message, color)

        if ctx:
            await ctx.send(embed=embed, ephemeral=True)

        if log_to_channel:
            log_channel_id, log_post_id, log_forum_id = self.get_log_channels()
            await self.send_to_channel(log_channel_id, embed)
            await self.send_to_forum_post(log_forum_id, log_post_id, embed)

    async def send_to_channel(self, channel_id: int, embed: interactions.Embed) -> None:
        try:
            channel = await self.bot.fetch_channel(channel_id)

            if not isinstance(
                channel := (
                    channel if isinstance(channel, interactions.GuildText) else None
                ),
                interactions.GuildText,
            ):
                logger.error(f"Channel ID {channel_id} is not a valid text channel.")
                return

            await channel.send(embed=embed)

        except NotFound as nf:
            logger.error(f"Channel with ID {channel_id} not found: {nf!r}")
        except Exception as e:
            logger.error(f"Error sending message to channel {channel_id}: {e!r}")

    async def send_to_forum_post(
        self, forum_id: int, post_id: int, embed: interactions.Embed
    ) -> None:
        try:
            if not isinstance(
                forum := await self.bot.fetch_channel(forum_id), interactions.GuildForum
            ):
                logger.error(f"Channel ID {forum_id} is not a valid forum channel.")
                return

            if not isinstance(
                thread := await forum.fetch_post(post_id),
                interactions.GuildPublicThread,
            ):
                logger.error(f"Post with ID {post_id} is not a valid thread.")
                return

            await thread.send(embed=embed)

        except NotFound:
            logger.error(f"{forum_id=}, {post_id=} - Forum or post not found")
        except Exception as e:
            logger.error(f"Forum post error [{forum_id=}, {post_id=}]: {e!r}")

    async def send_error(
        self,
        ctx: Optional[
            Union[
                interactions.SlashContext,
                interactions.InteractionContext,
                interactions.ComponentContext,
            ]
        ],
        message: str,
        log_to_channel: bool = False,
    ) -> None:
        await self.send_response(
            ctx, "Error", message, EmbedColor.ERROR, log_to_channel
        )

    async def send_success(
        self,
        ctx: Optional[
            Union[
                interactions.SlashContext,
                interactions.InteractionContext,
                interactions.ComponentContext,
            ]
        ],
        message: str,
        log_to_channel: bool = False,
    ) -> None:
        await self.send_response(
            ctx, "Success", message, EmbedColor.INFO, log_to_channel
        )

    async def log_action_internal(self, details: ActionDetails) -> None:
        logger.debug(f"log_action_internal called for action: {details.action}")
        timestamp = int(datetime.now(timezone.utc).timestamp())
        action_name = details.action.name.capitalize()

        log_embed = await self.create_embed(
            title=f"Action Log: {action_name}",
            color=self.get_action_color(details.action),
        )

        fields = [
            ("Actor", details.actor.mention if details.actor else "Unknown", True),
            (
                "Thread",
                f"{details.channel.mention if details.channel else 'Unknown'}",
                True,
            ),
            ("Time", f"<t:{timestamp}:F> (<t:{timestamp}:R>)", True),
            *(
                [
                    (
                        "Target",
                        details.target.mention if details.target else "Unknown",
                        True,
                    )
                ]
                if details.target
                else []
            ),
            ("Result", details.result.capitalize(), True),
            ("Reason", details.reason, False),
        ] + (
            [
                (
                    "Additional Info",
                    self.format_additional_info(details.additional_info),
                    False,
                )
            ]
            if details.additional_info
            else []
        )
        for name, value, inline in fields:
            log_embed.add_field(name=name, value=value, inline=inline)

        log_channel = await self.bot.fetch_channel(self.LOG_CHANNEL_ID)
        log_forum = await self.bot.fetch_channel(self.LOG_FORUM_ID)
        log_post = await log_forum.fetch_post(self.LOG_POST_ID)

        log_key = f"{details.action}_{details.post_name}_{timestamp}"
        if getattr(self, "_last_log_key", None) == log_key:
            logger.warning(f"Duplicate log detected: {log_key}")
            return
        self._last_log_key = log_key

        if log_post.archived:
            await log_post.edit(archived=False)

        await log_post.send(embeds=[log_embed])
        await log_channel.send(embeds=[log_embed])

        if details.target and not details.target.bot:
            dm_embed = await self.create_embed(
                title=f"{action_name} Notification",
                description=self.get_notification_message(details),
                color=self.get_action_color(details.action),
            )
            components = (
                [
                    interactions.Button(
                        style=interactions.ButtonStyle.URL,
                        label="Appeal",
                        url="https://discord.com/channels/1150630510696075404/1230132503273013358",
                    )
                ]
                if details.action == ActionType.LOCK
                else []
            )
            actions_set = {
                ActionType.LOCK,
                ActionType.UNLOCK,
                ActionType.DELETE,
                ActionType.BAN,
                ActionType.UNBAN,
                ActionType.SHARE_PERMISSIONS,
                ActionType.REVOKE_PERMISSIONS,
            }
            if details.action in actions_set:
                await self.send_dm(details.target, dm_embed, components)

    @staticmethod
    def get_action_color(action: ActionType) -> int:
        color_mapping: Dict[ActionType, EmbedColor] = {
            ActionType.LOCK: EmbedColor.WARN,
            ActionType.BAN: EmbedColor.ERROR,
            ActionType.DELETE: EmbedColor.WARN,
            ActionType.UNLOCK: EmbedColor.INFO,
            ActionType.UNBAN: EmbedColor.INFO,
            ActionType.EDIT: EmbedColor.INFO,
            ActionType.SHARE_PERMISSIONS: EmbedColor.INFO,
            ActionType.REVOKE_PERMISSIONS: EmbedColor.WARN,
        }
        return color_mapping.get(action, EmbedColor.DEBUG).value

    @staticmethod
    async def send_dm(
        target: interactions.Member,
        embed: interactions.Embed,
        components: List[interactions.Button],
    ) -> None:
        try:
            await target.send(embeds=[embed], components=components)
        except Exception:
            logger.warning(f"Failed to send DM to {target.mention}", exc_info=True)

    @staticmethod
    def get_notification_message(details: ActionDetails) -> str:
        cm = details.channel.mention if details.channel else "the thread"
        a = details.action

        base_messages = {
            ActionType.LOCK: f"{cm} has been locked.",
            ActionType.UNLOCK: f"{cm} has been unlocked.",
            ActionType.DELETE: f"Your message has been deleted from {cm}.",
            ActionType.BAN: f"You have been banned from {cm}. If you continue to attempt to post, your comments will be deleted.",
            ActionType.UNBAN: f"You have been unbanned from {cm}.",
            ActionType.SHARE_PERMISSIONS: f"You have been granted permissions to {cm}.",
            ActionType.REVOKE_PERMISSIONS: f"Your permissions for {cm} have been revoked.",
        }

        if a == ActionType.EDIT:
            if details.additional_info and "tag_updates" in details.additional_info:
                updates = details.additional_info["tag_updates"]
                actions = [
                    f"{update['Action']}ed tag '{update['Tag']}'" for update in updates
                ]
                return f"Tags have been modified in {cm}: {', '.join(actions)}."
            return f"Changes have been made to {cm}."

        m = base_messages.get(
            a, f"An action ({a.name.lower()}) has been performed in {cm}."
        )

        if a not in {
            ActionType.BAN,
            ActionType.UNBAN,
            ActionType.SHARE_PERMISSIONS,
            ActionType.REVOKE_PERMISSIONS,
        }:
            m += f" Reason: {details.reason}"

        return m

    @staticmethod
    def format_additional_info(info: Mapping[str, Any]) -> str:
        return "\n".join(
            (
                f"**{k.replace('_', ' ').title()}**:\n"
                + "\n".join(f"- {ik}: {iv}" for d in v for ik, iv in d.items())
                if isinstance(v, list) and v and isinstance(v[0], dict)
                else f"**{k.replace('_', ' ').title()}**: {v}"
            )
            for k, v in info.items()
        )

    # Base commands

    module_base: interactions.SlashCommand = interactions.SlashCommand(
        name="threads", description="Threads commands"
    )

    # Convert commands

    @module_base.subcommand(
        "convert",
        sub_cmd_description="Convert channel names between different Chinese variants",
    )
    @interactions.slash_option(
        name="source",
        description="Source language variant",
        required=True,
        opt_type=interactions.OptionType.STRING,
        choices=[
            interactions.SlashCommandChoice(
                name="Simplified Chinese (Mainland China)", value="cn"
            ),
            interactions.SlashCommandChoice(
                name="Traditional Chinese (Taiwan)", value="tw"
            ),
            interactions.SlashCommandChoice(
                name="Traditional Chinese (Hong Kong)", value="hk"
            ),
            interactions.SlashCommandChoice(
                name="Traditional Chinese (Mainland China)", value="cnt"
            ),
            interactions.SlashCommandChoice(name="Japanese Shinjitai", value="jp"),
        ],
    )
    @interactions.slash_option(
        name="target",
        description="Target language variant",
        required=True,
        opt_type=interactions.OptionType.STRING,
        choices=[
            interactions.SlashCommandChoice(
                name="Simplified Chinese (Mainland China)", value="cn"
            ),
            interactions.SlashCommandChoice(
                name="Traditional Chinese (Taiwan)", value="tw"
            ),
            interactions.SlashCommandChoice(
                name="Traditional Chinese (Hong Kong)", value="hk"
            ),
            interactions.SlashCommandChoice(
                name="Traditional Chinese (Mainland China)", value="cnt"
            ),
            interactions.SlashCommandChoice(name="Japanese Shinjitai", value="jp"),
        ],
    )
    @interactions.slash_option(
        name="scope",
        description="What to convert",
        required=True,
        opt_type=interactions.OptionType.STRING,
        choices=[
            interactions.SlashCommandChoice(name="All", value="all"),
            interactions.SlashCommandChoice(name="Server Name Only", value="server"),
            interactions.SlashCommandChoice(name="Roles Only", value="roles"),
            interactions.SlashCommandChoice(name="Channels Only", value="channels"),
        ],
    )
    @interactions.slash_default_member_permission(
        interactions.Permissions.ADMINISTRATOR
    )
    @interactions.max_concurrency(interactions.Buckets.GUILD, 1)
    async def convert_names(
        self, ctx: interactions.SlashContext, source: str, target: str, scope: str
    ) -> None:
        if not ctx.author.guild_permissions.ADMINISTRATOR:
            await self.send_error(ctx, "Only administrators can use this command.")
            return

        if source == target:
            await self.send_error(
                ctx, "Source and target languages cannot be the same."
            )
            return

        supported_pairs = {
            ("cn", "tw"),
            ("cn", "hk"),
            ("cn", "cnt"),
            ("cn", "jp"),
            ("tw", "hk"),
            ("tw", "cnt"),
            ("tw", "jp"),
            ("hk", "cnt"),
            ("hk", "jp"),
            ("cnt", "jp"),
        }
        conversion_pair = tuple(sorted([source, target]))
        if conversion_pair not in {tuple(sorted(pair)) for pair in supported_pairs}:
            await self.send_error(
                ctx,
                f"Conversion between {source.upper()} and {target.upper()} is not supported. Please choose a supported language pair.",
            )
            return

        converter_key = f"{source}2{target}"
        if converter_key not in self.model.converters:
            with_phrase = {source, target} == {"cn", "tw"}
            self.model.converters[converter_key] = StarCC.PresetConversion(
                src=source, dst=target, with_phrase=with_phrase
            )

        await self.send_success(
            ctx,
            "Starting conversion task. This may take a while depending on the server size.",
            log_to_channel=True,
        )

        task = asyncio.create_task(
            self.perform_conversion(ctx.guild, f"{source}2{target}", scope)
        )
        self.conversion_task = task
        await task

    async def perform_conversion(
        self,
        guild: interactions.Guild,
        direction: str,
        scope: str,
    ) -> None:
        src, dst = direction.split("2")
        converter = self.model.converters[f"{src}2{dst}"]
        direction_name = f"{direction[:2].upper()} to {direction[3:].upper()}"

        try:
            if scope in ("all", "server"):
                if guild.name and (new_name := converter(guild.name)) != guild.name:
                    await guild.edit(name=new_name)
                    await asyncio.sleep(2)

                if (
                    guild.description
                    and (new_desc := converter(guild.description)) != guild.description
                ):
                    await guild.edit(description=new_desc)
                    await asyncio.sleep(2)

            if scope in ("all", "roles"):
                for i, role in enumerate(guild.roles):
                    if role.name and role.position != 0:
                        if (new_name := converter(role.name)) != role.name:
                            try:
                                await role.edit(name=new_name)
                                await asyncio.sleep(1 + (1 if (i + 1) % 10 == 0 else 0))
                            except Exception as e:
                                logger.error(f"Role update failed: {e}")

            if scope in ("all", "channels"):
                channels = set()
                for channel in guild.channels:
                    if channel.id not in channels:
                        channels.add(channel.id)
                        try:
                            if (
                                channel.name
                                and (new_name := converter(channel.name))
                                != channel.name
                            ):
                                await channel.edit(name=new_name)
                                await asyncio.sleep(2)

                            if isinstance(
                                channel,
                                (interactions.GuildText, interactions.GuildNews),
                            ):
                                if (
                                    channel.topic
                                    and (new_topic := converter(channel.topic))
                                    != channel.topic
                                ):
                                    await channel.edit(topic=new_topic)
                                    await asyncio.sleep(2)

                            if (
                                isinstance(channel, interactions.GuildForum)
                                and channel.available_tags
                            ):
                                for tag in channel.available_tags:
                                    if (
                                        new_tag_name := converter(tag.name)
                                    ) != tag.name:
                                        await channel.edit_tag(
                                            tag.id, name=new_tag_name
                                        )
                                        await asyncio.sleep(2)

                        except Exception as e:
                            logger.error(f"Channel conversion failed: {e}")

                        if isinstance(channel, interactions.GuildCategory):
                            for child in channel.channels:
                                if child.id not in channels:
                                    channels.add(child.id)
                                    try:
                                        if (
                                            child.name
                                            and (new_name := converter(child.name))
                                            != child.name
                                        ):
                                            await child.edit(name=new_name)
                                            await asyncio.sleep(2)

                                        if isinstance(
                                            child,
                                            (
                                                interactions.GuildText,
                                                interactions.GuildNews,
                                            ),
                                        ):
                                            if (
                                                child.topic
                                                and (
                                                    new_topic := converter(child.topic)
                                                )
                                                != child.topic
                                            ):
                                                await child.edit(topic=new_topic)
                                                await asyncio.sleep(2)

                                        if (
                                            isinstance(child, interactions.GuildForum)
                                            and child.available_tags
                                        ):
                                            for tag in child.available_tags:
                                                if (
                                                    new_tag_name := converter(tag.name)
                                                ) != tag.name:
                                                    await child.edit_tag(
                                                        tag.id, name=new_tag_name
                                                    )
                                                    await asyncio.sleep(2)

                                    except Exception as e:
                                        logger.error(f"Channel conversion failed: {e}")

            logger.info(f"Conversion to {direction_name} completed successfully")

        except Exception as e:
            logger.error(f"Error during conversion: {e}", exc_info=True)
            raise

    # Top commands

    @module_base.subcommand("top", sub_cmd_description="Return to the top")
    async def navigate_to_top_post(self, ctx: interactions.SlashContext) -> None:
        thread: Union[interactions.GuildText, interactions.ThreadChannel] = ctx.channel
        if message_url := await self.fetch_oldest_message_url(thread):
            await self.send_success(
                ctx,
                f"Here's the link to the top of the thread: [Click here]({message_url}).",
            )
        else:
            await self.send_error(
                ctx,
                "Unable to find the top message in this thread. This could happen if the thread is empty or if there was an error accessing the message history. Please try again later or contact a moderator if the issue persists.",
            )

    @staticmethod
    async def fetch_oldest_message_url(
        channel: Union[interactions.GuildText, interactions.ThreadChannel]
    ) -> Optional[str]:
        try:
            async for message in channel.history(limit=1):
                url = URL(message.jump_url)
                return str(url.with_path(url.path.rsplit("/", 1)[0] + "/0"))
        except Exception as e:
            logger.error(f"Error fetching oldest message: {e}")
        return None

    # Lock commands

    @module_base.subcommand("lock", sub_cmd_description="Lock the current thread")
    @interactions.slash_option(
        name="reason",
        description="Reason for locking the thread",
        required=True,
        opt_type=interactions.OptionType.STRING,
    )
    @log_action
    async def lock_post(
        self, ctx: interactions.SlashContext, reason: str
    ) -> ActionDetails:
        return await self.toggle_post_lock(ctx, ActionType.LOCK, reason)

    @module_base.subcommand("unlock", sub_cmd_description="Unlock the current thread")
    @interactions.slash_option(
        name="reason",
        description="Reason for unlocking the thread",
        required=True,
        opt_type=interactions.OptionType.STRING,
    )
    @log_action
    async def unlock_post(
        self, ctx: interactions.SlashContext, reason: str
    ) -> ActionDetails:
        return await self.toggle_post_lock(ctx, ActionType.UNLOCK, reason)

    @log_action
    async def toggle_post_lock(
        self, ctx: interactions.SlashContext, action: ActionType, reason: str
    ) -> Optional[ActionDetails]:
        if not await self.validate_channel(ctx):
            await self.send_error(
                ctx,
                "This command can only be used in threads. Please navigate to a thread channel and try again.",
            )
            return None

        thread = ctx.channel
        desired_state = action == ActionType.LOCK
        action_name = action.name.lower()
        action_past_tense: Literal["locked", "unlocked"] = (
            "locked" if desired_state else "unlocked"
        )

        async def check_conditions() -> Optional[str]:
            if thread.archived:
                return f"Unable to {action_name} {thread.mention} because it is archived. Please unarchive the thread first."
            if thread.locked == desired_state:
                return f"This thread is already {action_name}ed. No changes were made."
            permissions_check, error_message = await self.check_permissions(ctx)
            return error_message if not permissions_check else None

        if error_message := await check_conditions():
            await self.send_error(ctx, error_message)
            return None

        try:
            await asyncio.wait_for(thread.edit(locked=desired_state), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout while trying to {action_name} thread {thread.id}")
            await self.send_error(
                ctx,
                f"The request to {action_name} the thread timed out. Please try again. If the issue persists, contact a moderator.",
            )
            return None
        except Exception as e:
            logger.exception(f"Failed to {action_name} thread {thread.id}")
            await self.send_error(
                ctx,
                f"Failed to {action_name} the thread due to an error: {str(e)}. Please try again or contact a moderator if the issue persists.",
            )
            return None

        await self.send_success(
            ctx,
            f"Thread has been successfully {action_past_tense}. All members will be notified of this change.",
        )

        return ActionDetails(
            action=action,
            reason=reason,
            post_name=thread.name,
            actor=ctx.author,
            channel=thread,
            additional_info={
                "previous_state": "Unlocked" if action == ActionType.LOCK else "Locked",
                "new_state": "Locked" if action == ActionType.LOCK else "Unlocked",
            },
        )

    # Message commands

    @interactions.message_context_menu(name="Message in Thread")
    @log_action
    async def message_actions(
        self, ctx: interactions.ContextMenuContext
    ) -> Optional[ActionDetails]:
        if not isinstance(ctx.channel, interactions.ThreadChannel):
            await self.send_error(
                ctx,
                "This command can only be used in threads. Please try this action in a thread channel instead.",
            )
            return None

        post: interactions.ThreadChannel = ctx.channel
        message: interactions.Message = ctx.target

        if not await self.can_manage_message(post, ctx.author):
            await self.send_error(
                ctx,
                "You don't have sufficient permissions to manage this message. You need to be either a moderator or the message author.",
            )
            return None

        options: Tuple[interactions.StringSelectOption, ...] = (
            interactions.StringSelectOption(
                label="Delete Message",
                value="delete",
                description="Delete this message",
            ),
            interactions.StringSelectOption(
                label=f"{'Unpin' if message.pinned else 'Pin'} Message",
                value=f"{'unpin' if message.pinned else 'pin'}",
                description=f"{'Unpin' if message.pinned else 'Pin'} this message",
            ),
        )

        select_menu: interactions.StringSelectMenu = interactions.StringSelectMenu(
            *options,
            placeholder="Select action for message",
            custom_id=f"message_action:{message.id}",
        )

        embed: interactions.Embed = await self.create_embed(
            title="Message Actions",
            description="Select an action to perform on this message.",
        )

        await ctx.send(embeds=[embed], components=[select_menu], ephemeral=True)
        return None

    # Tags commands

    @interactions.message_context_menu(name="Tags in Post")
    @log_action
    async def manage_post_tags(self, ctx: interactions.ContextMenuContext) -> None:
        logger.info(f"manage_post_tags called for post {ctx.channel.id}")

        if not isinstance(ctx.channel, interactions.GuildForumPost):
            logger.warning(f"Invalid channel for manage_post_tags: {ctx.channel.id}")
            await self.send_error(ctx, "This command can only be used in forum posts.")
            return

        has_perm, error_message = await self.check_permissions(
            interactions.SlashContext(ctx)
        )
        if not has_perm:
            logger.warning(f"Insufficient permissions for user {ctx.author.id}")
            await self.send_error(ctx, error_message)
            return

        post: interactions.GuildForumPost = ctx.channel
        try:
            available_tags: Tuple[interactions.ThreadTag, ...] = (
                await self.fetch_available_tags(post.parent_id)
            )
            if not available_tags:
                logger.warning(f"No available tags found for forum {post.parent_id}")
                await self.send_error(ctx, "No tags are available for this forum.")
                return

            logger.info(f"Available tags for post {post.id}: {available_tags}")
        except Exception as e:
            logger.error(f"Error fetching available tags: {e}", exc_info=True)
            await self.send_error(
                ctx, "An error occurred while fetching available tags."
            )
            return

        current_tag_ids: Set[int] = {tag.id for tag in post.applied_tags}
        logger.info(f"Current tag IDs for post {post.id}: {current_tag_ids}")

        options: Tuple[interactions.StringSelectOption, ...] = tuple(
            interactions.StringSelectOption(
                label=f"{'Remove' if tag.id in current_tag_ids else 'Add'}: {tag.name}",
                value=f"{'remove' if tag.id in current_tag_ids else 'add'}:{tag.id}",
                description=f"{'Currently applied' if tag.id in current_tag_ids else 'Not applied'}",
            )
            for tag in available_tags
        )
        logger.info(f"Created {len(options)} options for select menu")

        select_menu: interactions.StringSelectMenu = interactions.StringSelectMenu(
            *options,
            placeholder="Select tags to add or remove",
            custom_id=f"manage_tags:{post.id}",
            max_values=len(options),
        )
        logger.info(f"Created select menu with custom_id: manage_tags:{post.id}")

        embed: interactions.Embed = await self.create_embed(
            title="Tags in Post",
            description="Select tags to add or remove from this post. You can select multiple tags at once.",
        )

        try:
            await ctx.send(
                embeds=[embed],
                components=[select_menu],
                ephemeral=True,
            )
            logger.info(f"Sent tag management menu for post {post.id}")
        except Exception as e:
            logger.error(f"Error sending tag management menu: {e}", exc_info=True)
            await self.send_error(
                ctx, "An error occurred while creating the tag management menu."
            )

    async def fetch_available_tags(
        self, parent_id: int
    ) -> tuple[interactions.ThreadTag, ...]:
        try:
            channel: interactions.GuildChannel = await self.bot.fetch_channel(parent_id)
            if not isinstance(channel, interactions.GuildForum):
                logger.warning(f"Channel {parent_id} is not a forum channel")
                return tuple()

            return tuple(channel.available_tags or ())
        except Exception as e:
            logger.error(f"Error fetching available tags for channel {parent_id}: {e}")
            return tuple()

    # User commands

    @interactions.user_context_menu(name="User in Thread")
    @log_action
    async def manage_user_in_forum_post(
        self, ctx: interactions.ContextMenuContext
    ) -> None:
        if not await self.validate_channel(ctx):
            await self.send_error(
                ctx,
                "This command can only be used in specific forum threads. Please try this command in a valid thread.",
            )
            return

        thread = ctx.channel
        target_user = ctx.target

        if target_user.id == self.bot.user.id:
            await self.send_error(
                ctx,
                "You cannot manage the bot's permissions or status in threads.",
            )
            return

        if target_user.id == ctx.author.id and self.CONGRESS_MEMBER_ROLE not in {
            role.id for role in ctx.author.roles
        }:
            await self.send_error(
                ctx,
                f"Only <@&{self.CONGRESS_MEMBER_ROLE}> have permission to manage their own status in threads. Please contact a <@&{self.CONGRESS_MEMBER_ROLE}> for assistance.",
            )
            return

        if not await self.can_manage_post(thread, ctx.author):
            await self.send_error(
                ctx,
                "You do not have the required permissions to manage users in this thread. Please ensure you have the correct role or permissions.",
            )
            return

        channel_id, thread_id, user_id = map(
            str, (thread.parent_id, thread.id, target_user.id)
        )
        is_banned = await self.is_user_banned(channel_id, thread_id, user_id)
        has_permissions = self.model.has_thread_permissions(thread_id, user_id)

        options = (
            interactions.StringSelectOption(
                label=f"{'Unban' if is_banned else 'Ban'} User",
                value=f"{'unban' if is_banned else 'ban'}",
                description=f"Currently {'banned' if is_banned else 'not banned'}",
            ),
            interactions.StringSelectOption(
                label=f"{'Revoke' if has_permissions else 'Share'} Permissions",
                value=f"{'revoke_permissions' if has_permissions else 'share_permissions'}",
                description=f"Currently has {'shared' if has_permissions else 'no special'} permissions",
            ),
        )

        select_menu: interactions.StringSelectMenu = interactions.StringSelectMenu(
            *options,
            placeholder="Select action for user",
            custom_id=f"manage_user:{channel_id}:{thread_id}:{user_id}",
        )

        embed: interactions.Embed = await self.create_embed(
            title="User in Thread",
            description=f"Select action for {target_user.mention}:\n"
            f"Current status: {'Banned' if is_banned else 'Not banned'} in this thread.\n"
            f"Permissions: {'Shared' if has_permissions else 'Not shared'}",
        )

        await ctx.send(embeds=[embed], components=[select_menu], ephemeral=True)

    # Timeout commands

    @module_base.subcommand(
        "timeout", sub_cmd_description="Start a timeout poll for a user"
    )
    @interactions.slash_option(
        name="user",
        description="The user to timeout",
        required=True,
        opt_type=interactions.OptionType.USER,
    )
    @interactions.slash_option(
        name="reason",
        description="Reason for timeout",
        required=True,
        opt_type=interactions.OptionType.STRING,
    )
    @interactions.slash_option(
        name="duration",
        description="Timeout duration in minutes (max 10)",
        required=True,
        opt_type=interactions.OptionType.INTEGER,
        min_value=1,
        max_value=10,
    )
    async def timeout_poll(
        self,
        ctx: interactions.SlashContext,
        user: Union[
            interactions.PermissionOverwrite, interactions.Role, interactions.User
        ],
        reason: str,
        duration: int,
    ) -> None:
        if not isinstance(user, interactions.Member):
            await self.send_error(ctx, "Invalid user type provided.")
            return

        if any(
            [
                ctx.channel.id not in self.TIMEOUT_CHANNEL_IDS,
                await self.has_admin_permissions(user),
                user.id in self.active_timeout_polls,
            ]
        ):
            await self.send_error(
                ctx,
                next(
                    filter(
                        None,
                        [
                            (
                                "This command can only be used in the designated timeout channels."
                                if ctx.channel.id not in self.TIMEOUT_CHANNEL_IDS
                                else None
                            ),
                            (
                                "You cannot start a timeout poll for administrators."
                                if await self.has_admin_permissions(user)
                                else None
                            ),
                            (
                                f"There is already an active timeout poll for {user.mention}."
                                if user.id in self.active_timeout_polls
                                else None
                            ),
                        ],
                    )
                ),
            )
            return

        embed = await self.create_embed(title="Timeout Poll", color=EmbedColor.WARN)
        embed.add_field(name="User", value=user.mention, inline=True)
        embed.add_field(name="Reason", value=reason, inline=True)
        embed.add_field(name="Duration", value=f"{duration} minutes", inline=True)
        embed.add_field(
            name="Required difference",
            value=f"{self.TIMEOUT_REQUIRED_DIFFERENCE} votes",
            inline=True,
        )

        end_time = int((datetime.now(timezone.utc) + timedelta(minutes=1)).timestamp())
        embed.add_field(
            name="Poll duration", value=f"Ends <t:{end_time}:R>", inline=True
        )

        message = await ctx.send(embeds=[embed])
        await message.add_reaction("👍")
        await message.add_reaction("👎")

        task = asyncio.create_task(
            self.handle_timeout_poll(ctx, message, user, reason, duration)
        )
        self.active_timeout_polls[user.id] = task

        await asyncio.sleep(60)
        await self.active_timeout_polls.pop(user.id, None)

    async def handle_timeout_poll(
        self,
        ctx: interactions.SlashContext,
        message: interactions.Message,
        target: Union[
            interactions.PermissionOverwrite, interactions.Role, interactions.User
        ],
        reason: str,
        duration: int,
    ) -> None:
        try:
            await asyncio.sleep(60)
            reactions = (await ctx.channel.fetch_message(message.id)).reactions

            votes = {
                r.emoji.name: r.count - 1
                for r in reactions
                if r.emoji.name in ("👍", "👎")
            }
            vote_diff = votes.get("👍", 0) - votes.get("👎", 0)

            channel = ctx.channel
            end_time = int(
                (datetime.now(timezone.utc) + timedelta(minutes=duration)).timestamp()
            )

            if vote_diff >= self.TIMEOUT_REQUIRED_DIFFERENCE:
                try:
                    deny_perms = [
                        interactions.Permissions.SEND_MESSAGES,
                        interactions.Permissions.SEND_MESSAGES_IN_THREADS,
                        interactions.Permissions.SEND_TTS_MESSAGES,
                        interactions.Permissions.SEND_VOICE_MESSAGES,
                        interactions.Permissions.ADD_REACTIONS,
                        interactions.Permissions.ATTACH_FILES,
                        interactions.Permissions.CREATE_INSTANT_INVITE,
                        interactions.Permissions.MENTION_EVERYONE,
                        interactions.Permissions.MANAGE_MESSAGES,
                        interactions.Permissions.MANAGE_THREADS,
                        interactions.Permissions.MANAGE_CHANNELS,
                    ]

                    forum_perms = [interactions.Permissions.CREATE_POSTS, *deny_perms]

                    try:
                        if hasattr(channel, "parent_channel"):
                            target_channel = channel.parent_channel
                            perms = forum_perms
                        else:
                            target_channel = channel
                            perms = deny_perms

                        reason_str = f"Member {target.display_name}({target.id}) timeout until <t:{end_time}:f> in Channel {target_channel.name} reason:{reason[:50] if len(reason) > 51 else reason}"
                        await target_channel.add_permission(
                            target, deny=perms, reason=reason_str
                        )

                    except Forbidden:
                        await self.send_error(
                            ctx,
                            "The bot needs to have enough permissions! Please contact technical support!",
                        )
                        return

                    asyncio.create_task(
                        self.restore_permissions(target_channel, target, duration)
                    )

                    result_embed = await self.create_embed(title="Timeout Poll Result")
                    result_embed.add_field(
                        name="Status",
                        value=f"{target.mention} has been timed out until <t:{end_time}:R>.",
                        inline=True,
                    )
                    result_embed.add_field(
                        name="Yes Votes", value=str(votes.get("👍", 0)), inline=True
                    )
                    result_embed.add_field(
                        name="No Votes", value=str(votes.get("👎", 0)), inline=True
                    )

                    await self.send_success(
                        ctx,
                        f"{target.mention} has been timed out until <t:{end_time}:R>.\n"
                        f"- Yes Votes: {votes.get('👍', 0)}\n"
                        f"- No Votes: {votes.get('👎', 0)}",
                        log_to_channel=True,
                    )

                except Exception as e:
                    logger.error(f"Failed to apply timeout: {e}")
                    await self.send_error(
                        ctx, f"Failed to apply timeout to {target.mention}"
                    )
            else:
                result_embed = await self.create_embed(title="Timeout Poll Result")
                result_embed.add_field(
                    name="Status",
                    value=f"Timeout poll for {target.mention} failed.",
                    inline=True,
                )
                result_embed.add_field(
                    name="Yes Votes", value=str(votes.get("👍", 0)), inline=True
                )
                result_embed.add_field(
                    name="No Votes", value=str(votes.get("👎", 0)), inline=True
                )
                result_embed.add_field(
                    name="Required difference",
                    value=f"{self.TIMEOUT_REQUIRED_DIFFERENCE} votes",
                    inline=True,
                )

            await ctx.channel.send(embeds=[result_embed])

        except Exception as e:
            logger.error(f"Error handling timeout poll: {e}", exc_info=True)
            await self.send_error(
                ctx, "An error occurred while processing the timeout poll."
            )

    async def restore_permissions(
        self,
        channel: Union[interactions.GuildChannel, interactions.ThreadChannel],
        member: Union[
            interactions.PermissionOverwrite, interactions.Role, interactions.User
        ],
        duration: int,
    ) -> None:
        try:
            await asyncio.sleep(duration * 60)
            channel = getattr(channel, "parent_channel", channel)

            end_time = int(datetime.now(timezone.utc).timestamp())
            await channel.delete_permission(
                member, reason=f"Timeout expired at <t:{end_time}:f>"
            )

            embed = await self.create_embed(title="Timeout Expired")
            embed.add_field(
                name="Status",
                value=f"{member.mention}'s timeout has expired at <t:{end_time}:f>.",
                inline=True,
            )
            await channel.send(embeds=[embed])

        except Exception as e:
            logger.error(f"Error restoring permissions: {e}", exc_info=True)

    # List commands

    @module_base.subcommand(
        "list", sub_cmd_description="List information for current thread"
    )
    @interactions.slash_option(
        name="type",
        description="Type of information to list",
        required=True,
        opt_type=interactions.OptionType.STRING,
        choices=[
            interactions.SlashCommandChoice(name="Banned Users", value="banned"),
            interactions.SlashCommandChoice(
                name="Thread Permissions", value="permissions"
            ),
            interactions.SlashCommandChoice(name="Post Statistics", value="stats"),
        ],
        argument_name="list_type",
    )
    async def list_thread_info(
        self, ctx: interactions.SlashContext, list_type: str
    ) -> None:
        if not await self.validate_channel(ctx):
            await self.send_error(
                ctx,
                "This command can only be used in threads. Please navigate to a thread channel and try again.",
            )
            return

        if not await self.can_manage_post(ctx.channel, ctx.author):
            await self.send_error(
                ctx,
                "You don't have permission to view information in this thread. Please contact a thread owner or moderator for access.",
            )
            return

        channel_id, post_id = str(ctx.channel.parent_id), str(ctx.channel.id)

        match list_type:
            case "banned":
                banned_users = self.model.banned_users[channel_id][post_id]

                if not banned_users:
                    await self.send_success(
                        ctx,
                        "There are currently no banned users in this thread. The thread is open to all permitted users.",
                    )
                    return

                embeds = []
                current_embed = await self.create_embed(
                    title=f"Banned Users in <#{post_id}>"
                )

                for user_id in banned_users:
                    try:
                        user = await self.bot.fetch_user(int(user_id))
                        current_embed.add_field(
                            name="Banned User",
                            value=f"- User: {user.mention if user else user_id}",
                            inline=True,
                        )

                        if len(current_embed.fields) >= 5:
                            embeds.append(current_embed)
                            current_embed = await self.create_embed(
                                title=f"Banned Users in <#{post_id}>"
                            )
                    except Exception as e:
                        logger.error(f"Error fetching user {user_id}: {e}")
                        continue

                if current_embed.fields:
                    embeds.append(current_embed)

                await self._send_paginated_response(
                    ctx, embeds, "No banned users were found in this thread."
                )

            case "permissions":
                users_with_permissions = self.model.thread_permissions[post_id]

                if not users_with_permissions:
                    await self.send_success(
                        ctx,
                        "No users currently have special permissions in this thread. Only default access rules apply.",
                    )
                    return

                embeds = []
                current_embed = await self.create_embed(
                    title=f"Users with Permissions in <#{post_id}>"
                )

                for user_id in users_with_permissions:
                    try:
                        user = await self.bot.fetch_user(int(user_id))
                        current_embed.add_field(
                            name="User with Permissions",
                            value=f"- User: {user.mention if user else user_id}",
                            inline=True,
                        )

                        if len(current_embed.fields) >= 5:
                            embeds.append(current_embed)
                            current_embed = await self.create_embed(
                                title=f"Users with Permissions in <#{post_id}>"
                            )
                    except Exception as e:
                        logger.error(f"Error fetching user {user_id}: {e}")
                        continue

                if current_embed.fields:
                    embeds.append(current_embed)

                await self._send_paginated_response(
                    ctx,
                    embeds,
                    "No users with special permissions were found in this thread.",
                )

            case "stats":
                stats = self.model.post_stats.get(post_id)

                if not stats:
                    await self.send_success(
                        ctx,
                        "No statistics are currently available for this thread. This may be a new thread or statistics tracking may be disabled.",
                    )
                    return

                embed = await self.create_embed(
                    title=f"Statistics for <#{post_id}>",
                    description=(
                        f"- Message Count: {stats.message_count}\n"
                        f"- Last Activity: {stats.last_activity.strftime('%Y-%m-%d %H:%M:%S UTC')}\n"
                        f"- Post Created: <t:{int(ctx.channel.created_at.timestamp())}:F>"
                    ),
                )

                await ctx.send(embeds=[embed])

    # Debug commands

    @module_base.subcommand("view", sub_cmd_description="View configuration files")
    @interactions.slash_option(
        name="type",
        description="Configuration type to view",
        required=True,
        opt_type=interactions.OptionType.STRING,
        choices=[
            interactions.SlashCommandChoice(name="Banned Users", value="banned"),
            interactions.SlashCommandChoice(
                name="Thread Permissions", value="permissions"
            ),
            interactions.SlashCommandChoice(name="Post Statistics", value="stats"),
            interactions.SlashCommandChoice(name="Featured Threads", value="featured"),
        ],
        argument_name="view_type",
    )
    async def list_debug_info(
        self, ctx: interactions.SlashContext, view_type: str
    ) -> None:
        if not any(role.id == self.THREADS_ROLE_ID for role in ctx.author.roles):
            await self.send_error(
                ctx, "You do not have permission to use this command."
            )
            return

        match view_type:
            case "banned":
                banned_users = await self._get_merged_banned_users()
                embeds = await self._create_banned_user_embeds(banned_users)
                await self._send_paginated_response(
                    ctx, embeds, "No banned users found."
                )
            case "permissions":
                permissions = await self._get_merged_permissions()
                permissions_dict = defaultdict(set)
                for thread_id, user_id in permissions:
                    permissions_dict[thread_id].add(user_id)
                embeds = await self._create_permission_embeds(permissions_dict)
                await self._send_paginated_response(
                    ctx, embeds, "No thread permissions found."
                )
            case "stats":
                stats = await self._get_merged_stats()
                embeds = await self._create_stats_embeds(stats)
                await self._send_paginated_response(
                    ctx, embeds, "No post statistics found."
                )
            case "featured":
                featured_posts = await self._get_merged_featured_posts()
                stats = await self._get_merged_stats()
                embeds = await self._create_featured_embeds(featured_posts, stats)
                await self._send_paginated_response(
                    ctx, embeds, "No featured threads found."
                )

    async def _get_merged_banned_users(self) -> Set[Tuple[str, str, str]]:
        try:
            await self.model.load_banned_users(self.BANNED_USERS_FILE)
            return {
                (channel_id, post_id, user_id)
                for channel_id, channel_data in self.model.banned_users.items()
                for post_id, user_set in channel_data.items()
                for user_id in user_set
            }
        except Exception as e:
            logger.error(f"Error loading banned users: {e}", exc_info=True)
            return set()

    async def _get_merged_permissions(self) -> Set[Tuple[str, str]]:
        try:
            await self.model.load_thread_permissions(self.THREAD_PERMISSIONS_FILE)
            return {
                (thread_id, user_id)
                for thread_id, users in self.model.thread_permissions.items()
                for user_id in users
            }
        except Exception as e:
            logger.error(f"Error loading thread permissions: {e}", exc_info=True)
            return set()

    async def _get_merged_stats(self) -> Dict[str, PostStats]:
        try:
            await self.model.load_post_stats(self.POST_STATS_FILE)
            return self.model.post_stats
        except Exception as e:
            logger.error(f"Error loading post stats: {e}", exc_info=True)
            return {}

    async def _get_merged_featured_posts(self) -> Dict[str, str]:
        try:
            await self.model.load_featured_posts(self.FEATURED_POSTS_FILE)
            return self.model.featured_posts
        except Exception as e:
            logger.error(f"Error loading featured posts: {e}", exc_info=True)
            return {}

    async def _create_banned_user_embeds(
        self, banned_users: Set[Tuple[str, str, str]]
    ) -> List[interactions.Embed]:
        embeds: List[interactions.Embed] = []
        current_embed = await self.create_embed(title="Banned Users List")

        for channel_id, post_id, user_id in banned_users:
            try:
                channel = await self.bot.fetch_channel(int(channel_id))
                post = await self.bot.fetch_channel(int(post_id))
                user = await self.bot.fetch_user(int(user_id))

                field_value = []
                if post:
                    field_value.append(f"- Thread: <#{post_id}>")
                else:
                    field_value.append(f"- Thread ID: {post_id}")

                if user:
                    field_value.append(f"- User: {user.mention}")
                else:
                    field_value.append(f"- User ID: {user_id}")

                if channel:
                    field_value.append(f"- Channel: <#{channel_id}>")
                else:
                    field_value.append(f"- Channel ID: {channel_id}")

                current_embed.add_field(
                    name="Ban Entry",
                    value="\n".join(field_value),
                    inline=True,
                )

                if len(current_embed.fields) >= 5:
                    embeds.append(current_embed)
                    current_embed = await self.create_embed(title="Banned Users List")

            except Exception as e:
                logger.error(f"Error fetching ban info: {e}", exc_info=True)
                current_embed.add_field(
                    name="Ban Entry",
                    value=(
                        f"- Channel: <#{channel_id}>\n"
                        f"- Post: <#{post_id}>\n"
                        f"- User: {user_id}\n"
                        "(Unable to fetch complete information)"
                    ),
                    inline=True,
                )

        if current_embed.fields:
            embeds.append(current_embed)

        return embeds

    async def _create_permission_embeds(
        self, permissions: DefaultDict[str, Set[str]]
    ) -> List[interactions.Embed]:
        embeds: List[interactions.Embed] = []
        current_embed = await self.create_embed(title="Thread Permissions List")

        for post_id, user_ids in permissions.items():
            try:
                post = await self.bot.fetch_channel(int(post_id))
                if not post:
                    logger.warning(f"Could not fetch channel {post_id}")
                    continue

                for user_id in user_ids:
                    try:
                        user = await self.bot.fetch_user(int(user_id))
                        if not user:
                            continue

                        current_embed.add_field(
                            name="Permission Entry",
                            value=(
                                f"- Thread: <#{post_id}>\n" f"- User: {user.mention}"
                            ),
                            inline=True,
                        )

                        if len(current_embed.fields) >= 5:
                            embeds.append(current_embed)
                            current_embed = await self.create_embed(
                                title="Thread Permissions List"
                            )
                    except Exception as e:
                        logger.error(f"Error fetching user {user_id}: {e}")
                        continue

            except Exception as e:
                logger.error(f"Error fetching thread {post_id}: {e}")
                current_embed.add_field(
                    name="Permission Entry",
                    value=(
                        f"- Thread: <#{post_id}>\n"
                        "(Unable to fetch complete information)"
                    ),
                    inline=True,
                )

        if current_embed.fields:
            embeds.append(current_embed)

        return embeds

    async def _create_stats_embeds(
        self, stats: Dict[str, PostStats]
    ) -> List[interactions.Embed]:
        embeds: List[interactions.Embed] = []
        current_embed = await self.create_embed(title="Post Statistics")

        sorted_stats = sorted(
            stats.items(), key=lambda item: item[1].message_count, reverse=True
        )

        for post_id, post_stats in sorted_stats:
            try:
                last_active = post_stats.last_activity.strftime("%Y-%m-%d %H:%M:%S UTC")

                current_embed.add_field(
                    name="Post Stats",
                    value=(
                        f"- Post: <#{post_id}>\n"
                        f"- Messages: {post_stats.message_count}\n"
                        f"- Last Active: {last_active}"
                    ),
                    inline=True,
                )

                if len(current_embed.fields) >= 5:
                    embeds.append(current_embed)
                    current_embed = await self.create_embed(title="Post Statistics")

            except Exception as e:
                logger.error(f"Error fetching post stats: {e}", exc_info=True)
                continue

        if current_embed.fields:
            embeds.append(current_embed)

        return embeds

    async def _create_featured_embeds(
        self, featured_posts: Dict[str, str], stats: Dict[str, PostStats]
    ) -> List[interactions.Embed]:
        embeds: List[interactions.Embed] = []
        current_embed: interactions.Embed = await self.create_embed(
            title="Featured Posts"
        )

        for forum_id, post_id in featured_posts.items():
            try:
                post_stats = stats.get(post_id, PostStats())
                timestamp = post_stats.last_activity.strftime("%Y-%m-%d %H:%M:%S UTC")

                field_value = (
                    f"- Forum: <#{forum_id}>\n"
                    f"- Post: <#{post_id}>\n"
                    f"- Messages: {post_stats.message_count}\n"
                    f"- Last Active: {timestamp}"
                )

                current_embed.add_field(
                    name="Featured Post", value=field_value, inline=True
                )

                if len(current_embed.fields) >= 5:
                    embeds.append(current_embed)
                    current_embed = await self.create_embed(title="Featured Posts")

            except Exception as e:
                logger.error(f"Error fetching featured post info: {e}", exc_info=True)
                continue

        if current_embed.fields:
            embeds.append(current_embed)

        return embeds

    async def _send_paginated_response(
        self,
        ctx: interactions.SlashContext,
        embeds: List[interactions.Embed],
        empty_message: str,
    ) -> None:
        if not embeds:
            await self.send_success(ctx, empty_message)
            return

        paginator = Paginator.create_from_embeds(self.bot, *embeds, timeout=120)
        await paginator.send(ctx)

    # Serve

    @log_action
    async def share_revoke_permissions(
        self,
        ctx: interactions.ContextMenuContext,
        member: interactions.Member,
        action: ActionType,
    ) -> Optional[ActionDetails]:
        if not await self.validate_channel(ctx):
            await self.send_error(
                ctx,
                "This command can only be used in threads. Please navigate to a thread channel and try again.",
            )
            return None

        thread = ctx.channel
        author = ctx.author

        if thread.parent_id != self.CONGRESS_ID and thread.owner_id != author.id:
            await self.send_error(
                ctx,
                "Only the thread owner can manage thread permissions. If you need to modify permissions, please contact the thread owner.",
            )
            return None

        if thread.parent_id == self.CONGRESS_ID:
            author_roles = {role.id for role in author.roles}

            if self.CONGRESS_MEMBER_ROLE in author_roles:
                await self.send_error(
                    ctx,
                    f"Members with the <@&{self.CONGRESS_MEMBER_ROLE}> role cannot manage thread permissions. This is a restricted action.",
                )
                return None

            if self.CONGRESS_MOD_ROLE not in author_roles:
                await self.send_error(
                    ctx,
                    f"You need the <@&{self.CONGRESS_MOD_ROLE}> role to manage thread permissions in this forum. Please contact a moderator for assistance.",
                )
                return None
        else:
            if not await self.can_manage_post(thread, author):
                await self.send_error(
                    ctx,
                    f"You can only {action.name.lower()} permissions for threads you manage. Please ensure you have the correct permissions or contact a moderator.",
                )
                return None

        thread_id = str(thread.id)
        user_id = str(member.id)

        match action:
            case ActionType.SHARE_PERMISSIONS:
                self.model.thread_permissions.setdefault(thread_id, set()).add(user_id)
                action_name = "shared"
            case ActionType.REVOKE_PERMISSIONS:
                if thread_id in self.model.thread_permissions:
                    self.model.thread_permissions[thread_id].discard(user_id)
                action_name = "revoked"
            case _:
                await self.send_error(
                    ctx,
                    "Invalid action type detected. Please try again with a valid permission action.",
                )
                return None

        await self.model.save_thread_permissions(self.THREAD_PERMISSIONS_FILE)
        await self.send_success(
            ctx,
            f"Permissions have been {action_name} successfully for {member.mention}. They will be notified of this change.",
        )

        return ActionDetails(
            action=action,
            reason=f"Permissions {action_name} by {author.mention}",
            post_name=thread.name,
            actor=author,
            target=member,
            channel=thread,
            additional_info={
                "action_type": f"{action_name.capitalize()} permissions",
                "affected_user": str(member),
                "affected_user_id": member.id,
            },
        )

    manage_user_regex_pattern = re.compile(r"manage_user:(\d+):(\d+):(\d+)")

    @interactions.component_callback(manage_user_regex_pattern)
    @log_action
    async def on_manage_user(
        self, ctx: interactions.ComponentContext
    ) -> Optional[ActionDetails]:
        logger.info(f"on_manage_user called with custom_id: {ctx.custom_id}")

        if not (match := self.manage_user_regex_pattern.match(ctx.custom_id)):
            logger.warning(f"Invalid custom ID format: {ctx.custom_id}")
            await self.send_error(
                ctx,
                "There was an issue processing your request due to an invalid format. Please try the action again, or contact a moderator if the issue persists.",
            )
            return None

        channel_id, post_id, user_id = match.groups()
        logger.info(
            f"Parsed IDs - channel: {channel_id}, post: {post_id}, user: {user_id}"
        )

        if not ctx.values:
            logger.warning("No action selected")
            await self.send_error(
                ctx,
                "No action was selected from the menu. Please select an action (ban/unban or share/revoke permissions) and try again.",
            )
            return None

        try:
            action = ActionType[ctx.values[0].upper()]
        except KeyError:
            logger.warning(f"Invalid action: {ctx.values[0]}")
            await self.send_error(
                ctx,
                f"The selected action `{ctx.values[0]}` is not valid. Please select a valid action from the menu and try again.",
            )
            return None

        try:
            member = await ctx.guild.fetch_member(int(user_id))
        except NotFound:
            logger.warning(f"User with ID {user_id} not found in the server")
            await self.send_error(
                ctx,
                "Unable to find the user in the server. They may have left or been removed. Please verify the user is still in the server before trying again.",
            )
            return None
        except ValueError:
            logger.warning(f"Invalid user ID: {user_id}")
            await self.send_error(
                ctx,
                "There was an error processing the user information. Please try the action again, or contact a moderator if the issue persists.",
            )
            return None

        match action:
            case ActionType.BAN | ActionType.UNBAN:
                return await self.ban_unban_user(ctx, member, action)
            case ActionType.SHARE_PERMISSIONS | ActionType.REVOKE_PERMISSIONS:
                return await self.share_revoke_permissions(ctx, member, action)
            case _:
                await self.send_error(
                    ctx,
                    "The selected action is not supported. Please choose either ban/unban or share/revoke permissions from the menu.",
                )
                return None

    @log_action
    async def ban_unban_user(
        self,
        ctx: interactions.ContextMenuContext,
        member: interactions.Member,
        action: ActionType,
    ) -> Optional[ActionDetails]:
        if not await self.validate_channel(ctx):
            await self.send_error(
                ctx,
                "This command can only be used in threads. Please navigate to a thread channel and try again.",
            )
            return None

        thread = ctx.channel
        author_roles, member_roles = map(
            lambda x: {role.id for role in x.roles}, (ctx.author, member)
        )
        if any(
            role_id in member_roles and thread.parent_id in channels
            for role_id, channels in self.ROLE_CHANNEL_PERMISSIONS.items()
        ):
            await self.send_error(
                ctx,
                "Unable to ban users with management permissions. These users have special privileges that prevent them from being banned.",
            )
            return None

        if thread.parent_id == self.CONGRESS_ID:
            if self.CONGRESS_MEMBER_ROLE in author_roles:
                if action is ActionType.BAN or (
                    action is ActionType.UNBAN and member.id != ctx.author.id
                ):
                    await self.send_error(
                        ctx,
                        f"<@&{self.CONGRESS_MEMBER_ROLE}> members can only unban themselves.",
                    )
                    return None
            elif self.CONGRESS_MOD_ROLE not in author_roles:
                await self.send_error(
                    ctx,
                    f"You need to be a <@&{self.CONGRESS_MOD_ROLE}> to manage bans in this forum.",
                )
                return None
        elif not await self.can_manage_post(thread, ctx.author):
            await self.send_error(
                ctx,
                f"You can only {action.name.lower()} users from threads you manage.",
            )
            return None

        if member.id == thread.owner_id:
            await self.send_error(
                ctx,
                "Thread owners cannot be banned from their own threads. This is a built-in protection for thread creators.",
            )
            return None

        channel_id, thread_id, user_id = map(
            str, (thread.parent_id, thread.id, member.id)
        )

        async with self.ban_lock:
            banned_users = self.model.banned_users
            thread_users = banned_users.setdefault(
                channel_id, defaultdict(set)
            ).setdefault(thread_id, set())

            match action:
                case ActionType.BAN:
                    thread_users.add(user_id)
                case ActionType.UNBAN:
                    thread_users.discard(user_id)
                case _:
                    await self.send_error(
                        ctx,
                        "Invalid action requested. Please select either ban or unban and try again.",
                    )
                    return None

            if not thread_users:
                del banned_users[channel_id][thread_id]
                if not banned_users[channel_id]:
                    del banned_users[channel_id]
            await self.model.save_banned_users(self.BANNED_USERS_FILE)

        await self.model.invalidate_ban_cache(channel_id, thread_id, user_id)

        action_name = "banned" if action is ActionType.BAN else "unbanned"
        await self.send_success(
            ctx,
            f"User has been successfully {action_name}. {'They will no longer be able to participate in this thread.' if action is ActionType.BAN else 'They can now participate in this thread again.'}",
        )

        return ActionDetails(
            action=action,
            reason=f"{action_name.capitalize()} by {ctx.author.mention}",
            post_name=thread.name,
            actor=ctx.author,
            target=member,
            channel=thread,
            additional_info={
                "action_type": action_name.capitalize(),
                "affected_user": str(member),
                "affected_user_id": member.id,
            },
        )

    message_action_regex_pattern = re.compile(r"message_action:(\d+)")

    @interactions.component_callback(message_action_regex_pattern)
    @log_action
    async def on_message_action(
        self, ctx: interactions.ComponentContext
    ) -> Optional[ActionDetails]:
        if not (match := self.message_action_regex_pattern.match(ctx.custom_id)):
            await self.send_error(
                ctx,
                "The message action format is invalid. Please ensure you're using the correct command format.",
            )
            return None

        message_id: int = int(match.group(1))
        action: str = ctx.values[0].lower()

        try:
            message = await ctx.channel.fetch_message(message_id)
        except NotFound:
            await self.send_error(
                ctx,
                "The message could not be found. It may have been deleted or you may not have permission to view it.",
            )
            return None
        except Exception as e:
            logger.error(f"Error fetching message {message_id}: {e}", exc_info=True)
            await self.send_error(
                ctx,
                "Unable to retrieve the message. This could be due to a temporary issue or insufficient permissions. Please try again later.",
            )
            return None

        if not isinstance(ctx.channel, interactions.ThreadChannel):
            await self.send_error(
                ctx,
                "This action can only be performed within threads. Please ensure you're in a thread channel before using this command.",
            )
            return None

        post = ctx.channel

        match action:
            case "delete":
                return await self.delete_message_action(ctx, post, message)
            case "pin" | "unpin":
                return await self.pin_message_action(
                    ctx, post, message, action == "pin"
                )
            case _:
                await self.send_error(
                    ctx,
                    "The selected action is not valid. Available actions are: delete, pin, and unpin. Please choose one of these options.",
                )
                return None

    async def delete_message_action(
        self,
        ctx: interactions.ComponentContext,
        post: interactions.ThreadChannel,
        message: interactions.Message,
    ) -> Optional[ActionDetails]:
        try:
            await message.delete()
            await self.send_success(
                ctx, f"Message successfully deleted from thread `{post.name}`."
            )

            return ActionDetails(
                action=ActionType.DELETE,
                reason=f"User-initiated message deletion by {ctx.author.mention}",
                post_name=post.name,
                actor=ctx.author,
                channel=post,
                target=message.author,
                additional_info={
                    "deleted_message_id": str(message.id),
                    "deleted_message_content": (
                        message.content[:1000] if message.content else "N/A"
                    ),
                    "deleted_message_attachments": (
                        [a.url for a in message.attachments]
                        if message.attachments
                        else []
                    ),
                },
            )
        except Exception as e:
            logger.error(f"Failed to delete message {message.id}: {e}", exc_info=True)
            await self.send_error(ctx, "Unable to delete the message.")
            return None

    async def pin_message_action(
        self,
        ctx: interactions.ComponentContext,
        post: interactions.ThreadChannel,
        message: interactions.Message,
        pin: bool,
    ) -> Optional[ActionDetails]:
        try:
            action_type, action_desc = (
                (ActionType.PIN, "pinned") if pin else (ActionType.UNPIN, "unpinned")
            )
            await message.pin() if pin else await message.unpin()

            await self.send_success(
                ctx, f"Message has been successfully {action_desc}."
            )

            return ActionDetails(
                action=action_type,
                reason=f"User-initiated message {action_desc} by {ctx.author.mention}",
                post_name=post.name,
                actor=ctx.author,
                channel=post,
                target=message.author,
                additional_info={
                    f"{action_desc}_message_id": str(message.id),
                    f"{action_desc}_message_content": (
                        message.content[:10] if message.content else "N/A"
                    ),
                },
            )
        except Exception as e:
            logger.error(
                f"Failed to {'pin' if pin else 'unpin'} message {message.id}: {e}",
                exc_info=True,
            )
            await self.send_error(ctx, f"Unable to {action_desc} the message.")
            return None

    manage_tags_regex_pattern = re.compile(r"manage_tags:(\d+)")

    @interactions.component_callback(manage_tags_regex_pattern)
    @log_action
    async def on_manage_tags(
        self, ctx: interactions.ComponentContext
    ) -> Optional[ActionDetails]:
        logger.info(f"on_manage_tags invoked with custom_id: {ctx.custom_id}")
        if not (match := self.manage_tags_regex_pattern.match(ctx.custom_id)):
            logger.warning(f"Invalid custom ID format: {ctx.custom_id}")
            await self.send_error(ctx, "Invalid custom ID format.")
            return None
        post_id = int(match.group(1))
        try:
            post = await self.bot.fetch_channel(post_id)
            parent_forum = await self.bot.fetch_channel(ctx.channel.parent_id)
        except Exception as e:
            logger.error(
                f"Channel fetch error for post_id {post_id}: {e}", exc_info=True
            )
            await self.send_error(ctx, "Failed to retrieve necessary channels.")
            return None
        if not isinstance(post, interactions.GuildForumPost):
            logger.warning(f"Channel {post_id} is not a GuildForumPost.")
            await self.send_error(ctx, "Invalid forum post.")
            return None
        tag_updates = {
            action: frozenset(
                int(value.split(":")[1])
                for value in ctx.values
                if value.startswith(f"{action}:")
            )
            for action in ("add", "remove")
        }
        logger.info(f"Processing tag updates for post {post_id}: {tag_updates}")
        current_tags = frozenset(tag.id for tag in post.applied_tags)
        new_tags = (current_tags | tag_updates["add"]) - tag_updates["remove"]

        if new_tags == current_tags:
            await self.send_success(ctx, "No tag changes detected.")
            return None

        if len(new_tags) > 5:
            await self.send_error(ctx, "A post can have a maximum of 5 tags.")
            return None

        try:
            await post.edit(applied_tags=list(new_tags))
            logger.info(f"Tags successfully updated for post {post_id}.")
        except Exception as e:
            logger.exception(f"Failed to edit tags for post {post_id}: {e}")
            await self.send_error(ctx, "Error updating tags. Please try again later.")
            return None

        tag_names = {tag.id: tag.name for tag in parent_forum.available_tags}
        updates = [
            f"Tag `{tag_names.get(str(tag_id), 'Unknown')}` {'added to' if action == 'add' else 'removed from'} the post."
            for action, tag_ids in tag_updates.items()
            if tag_ids
            for tag_id in tag_ids
        ]

        await self.send_success(ctx, "\n".join(updates))
        return ActionDetails(
            action=ActionType.EDIT,
            reason="Tags modified in the post",
            post_name=post.name,
            actor=ctx.author,
            channel=post,
            additional_info={
                "tag_updates": [
                    {
                        "Action": action.capitalize(),
                        "Tag": tag_names.get(str(tag_id), "Unknown"),
                    }
                    for action, tag_ids in tag_updates.items()
                    if tag_ids
                    for tag_id in tag_ids
                ]
            },
        )

    @staticmethod
    async def process_new_post(
        thread: interactions.GuildPublicThread, create_poll: bool = True
    ) -> None:
        try:
            timestamp = datetime.now(timezone.utc).strftime("%y%m%d%H%M")
            new_title = f"[{timestamp}] {thread.name}"

            tasks = [thread.edit(name=new_title)]
            if create_poll:
                poll = interactions.Poll.create(
                    question="您对此持何意见？What is your position?",
                    duration=48,
                    answers=["正  In Favor", "反  Opposed", "无  Abstain"],
                )
                tasks.append(thread.send(poll=poll))

            for task in tasks:
                await task

        except Exception as e:
            error_msg = f"Error processing thread {thread.id}: {str(e)}"
            logger.error(error_msg, exc_info=True)

    async def process_link(self, event: MessageCreate) -> None:
        if not self.should_process_link(event):
            return

        msg_content = event.message.content
        if (
            not (new_content := await self.transform_links(msg_content))
            or new_content == msg_content
        ):
            return

        await asyncio.gather(
            self.send_warning(event.message.author, self.get_warning_message()),
            self.replace_message(event, new_content),
        )

    async def send_warning(self, user: interactions.Member, message: str) -> None:
        embed: interactions.Embed = await self.create_embed(
            title="Warning", description=message, color=EmbedColor.WARN
        )
        try:
            await user.send(embeds=[embed])
        except Exception as e:
            logger.warning(f"Failed to send warning DM to {user.mention}: {e}")

    @contextlib.asynccontextmanager
    async def create_temp_webhook(
        self,
        channel: Union[interactions.GuildText, interactions.ThreadChannel],
        name: str,
    ) -> AsyncGenerator[interactions.Webhook, None]:
        webhook: interactions.Webhook = await channel.create_webhook(name=name)
        try:
            yield webhook
        finally:
            with contextlib.suppress(Exception):
                await webhook.delete()

    async def replace_message(self, event: MessageCreate, new_content: str) -> None:
        channel: Union[interactions.GuildText, interactions.ThreadChannel] = (
            event.message.channel
        )
        async with self.create_temp_webhook(channel, "Temp Webhook") as webhook:
            try:
                await webhook.send(
                    content=new_content,
                    username=event.message.author.display_name,
                    avatar_url=event.message.author.avatar_url,
                )
                await event.message.delete()
            except Exception as e:
                logger.exception(f"Failed to replace message: {str(e)}")
                raise

    @functools.lru_cache(maxsize=1024)
    def sanitize_url(
        self, url_str: str, preserve_params: frozenset[str] = frozenset({"p"})
    ) -> str:
        u = URL(url_str)
        query_params = frozenset(u.query.keys())
        return str(
            u.with_query({k: u.query[k] for k in preserve_params & query_params})
        )

    @functools.lru_cache(maxsize=1)
    def get_link_transformations(self) -> list[tuple[re.Pattern, Callable[[str], str]]]:
        return [
            (
                re.compile(
                    r"https?://(?:www\.)?(?:b23\.tv|bilibili\.com/video/(?:BV\w+|av\d+))",
                    re.IGNORECASE,
                ),
                lambda url: (
                    self.sanitize_url(url)
                    if "bilibili.com" in url.lower()
                    else str(URL(url).with_host("b23.tf"))
                ),
            )
        ]

    async def transform_links(self, content: str) -> str:
        patterns = self.get_link_transformations()
        return await asyncio.to_thread(
            lambda: re.sub(
                r"https?://\S+",
                lambda m: next(
                    (
                        transform(str(m.group(0)))
                        for pattern, transform in patterns
                        if pattern.match(str(m.group(0)))
                    ),
                    m.group(0),
                ),
                content,
                flags=re.IGNORECASE,
            )
        )

    @functools.lru_cache(maxsize=1)
    def get_warning_message(self) -> str:
        return "The link you sent may expose your ID. To protect the privacy of members, sending such links is prohibited."

    # Task methods

    @interactions.Task.create(interactions.IntervalTrigger(hours=1))
    async def rotate_featured_posts_periodically(self) -> None:
        try:
            while True:
                try:
                    await self.adjust_thresholds()
                    await self.update_featured_posts_rotation()
                except Exception as e:
                    logger.error(
                        f"Error in rotating selected posts: {e}", exc_info=True
                    )
                await asyncio.sleep(self.rotation_interval.total_seconds())
        except asyncio.CancelledError:
            logger.info("Featured posts rotation task cancelled")
            raise
        except Exception as e:
            logger.error(
                f"Fatal error in featured posts rotation task: {e}", exc_info=True
            )
            raise

    # Event methods

    @interactions.listen(ExtensionLoad)
    async def on_extension_load(self) -> None:
        self.rotate_featured_posts_periodically.start()

    @interactions.listen(ExtensionUnload)
    async def on_extension_unload(self) -> None:
        tasks_to_stop: tuple = (self.rotate_featured_posts_periodically,)
        for task in tasks_to_stop:
            task.stop()

        pending_tasks = [
            task for task in asyncio.all_tasks() if task.get_name().startswith("Task-")
        ]
        await asyncio.gather(
            *map(functools.partial(asyncio.wait_for, timeout=10.0), pending_tasks),
            return_exceptions=True,
        )

    @interactions.listen(MessageCreate)
    async def on_message_create_for_stats(self, event: MessageCreate) -> None:
        if not event.message.guild:
            return
        if not isinstance(event.message.channel, interactions.GuildForumPost):
            return
        if event.message.channel.parent_id not in self.FEATURED_CHANNELS:
            return

        await self.increment_message_count("{}".format(event.message.channel.id))

    @interactions.listen(MessageCreate)
    async def on_message_create_for_actions(self, event: MessageCreate) -> None:
        msg = event.message
        if not all(
            (
                msg.guild,
                msg.message_reference,
                isinstance(msg.channel, interactions.ThreadChannel),
            )
        ):
            return

        action = {
            "del": "delete",
            "pin": "pin",
            "unpin": "unpin",
        }.get(msg.content.casefold().strip())

        if not action:
            return

        try:
            if not (referenced_message := await msg.fetch_referenced_message()):
                return

            if not await self.can_manage_message(msg.channel, msg.author):
                return

            await msg.delete()

            await (
                self.delete_message_action(msg, msg.channel, referenced_message)
                if action == "delete"
                else self.pin_message_action(
                    msg, msg.channel, referenced_message, action == "pin"
                )
            )

        except Exception as e:
            logger.error(f"Error processing message action: {e}", exc_info=True)

    @interactions.listen(MessageCreate)
    async def on_message_create_for_link(self, event: MessageCreate) -> None:
        if not event.message.guild:
            return

        link_task = (
            asyncio.create_task(self.process_link(event))
            if self.should_process_link(event)
            else None
        )

        if self.should_process_message(event):
            channel_id, post_id, author_id = map(
                str,
                (
                    event.message.channel.parent_id,
                    event.message.channel.id,
                    event.message.author.id,
                ),
            )

            if await self.is_user_banned(channel_id, post_id, author_id):
                await event.message.delete()

        if link_task:
            await link_task

    @interactions.listen(NewThreadCreate)
    async def on_new_thread_create_for_poll(self, event: NewThreadCreate) -> None:
        thread = event.thread
        parent_id = thread.parent_id

        if not isinstance(thread, interactions.GuildForumPost):
            logger.debug(f"Thread {thread.id} is not a forum post, skipping")
            return

        if not (parent_id in self.POLL_FORUM_ID or parent_id == self.CONGRESS_ID):
            logger.debug(
                f"Thread {thread.id} parent {parent_id} is not a monitored forum, skipping"
            )
            return

        owner_id = event.thread.owner_id
        if not owner_id:
            logger.debug(f"Thread {thread.id} has no owner, skipping")
            return

        guild = await self.bot.fetch_guild(self.GUILD_ID)
        owner = await guild.fetch_member(owner_id)
        if not owner or owner.bot:
            logger.debug(
                f"Thread {thread.id} owner {owner_id} is invalid or bot, skipping"
            )
            return

        forum_id = parent_id if isinstance(parent_id, int) else parent_id[0]
        skip_tags = {
            self.POLL_FORUM_ID: {1242530950970216590, 1184022078278602825},
            self.CONGRESS_ID: {1196707934877528075, 1276909294565986438},
        }.get(forum_id, set())

        thread_tags = {tag.id for tag in thread.applied_tags}
        create_poll = not bool(skip_tags & thread_tags)

        logger.info(
            f"Processing thread {thread.id} in forum {forum_id}. Thread tags: {thread_tags}. Skip tags: {skip_tags}. Tags intersection: {skip_tags & thread_tags}. Will create poll: {create_poll}"
        )

        await self.process_new_post(thread, create_poll=create_poll)

    @interactions.listen(MessageCreate)
    async def on_message_create_for_banned_users(self, event: MessageCreate) -> None:
        if not (
            event.message.guild
            and isinstance(event.message.channel, interactions.ThreadChannel)
        ):
            return

        channel_id, post_id, author_id = map(
            str,
            (
                event.message.channel.parent_id,
                event.message.channel.id,
                event.message.author.id,
            ),
        )

        await self.is_user_banned(
            channel_id, post_id, author_id
        ) and event.message.delete()

    # Check methods

    async def can_manage_post(
        self,
        thread: interactions.ThreadChannel,
        user: interactions.Member,
    ) -> bool:
        user_roles = {role.id for role in user.roles}
        user_id = user.id
        thread_parent = thread.parent_id

        if thread_parent == self.CONGRESS_ID:
            return bool(
                user_roles & {self.CONGRESS_MOD_ROLE, self.CONGRESS_MEMBER_ROLE}
            )

        return (
            thread.owner_id == user_id
            or self.model.has_thread_permissions(str(thread.id), str(user_id))
            or next(
                (
                    True
                    for role_id, channels in self.ROLE_CHANNEL_PERMISSIONS.items()
                    if role_id in user_roles and thread_parent in channels
                ),
                False,
            )
        )

    async def check_permissions(
        self, ctx: interactions.SlashContext
    ) -> tuple[bool, str]:
        r, p_id, perms = (
            ctx.author.roles,
            ctx.channel.parent_id,
            self.ROLE_CHANNEL_PERMISSIONS,
        )
        return (
            any(role.id in perms and p_id in perms[role.id] for role in r),
            "You do not have permission for this action."
            * (not any(role.id in perms and p_id in perms[role.id] for role in r)),
        )

    async def validate_channel(self, ctx: interactions.InteractionContext) -> bool:
        return (
            isinstance(
                ctx.channel,
                (
                    interactions.GuildForumPost,
                    interactions.GuildPublicThread,
                    interactions.ThreadChannel,
                ),
            )
            and ctx.channel.parent_id in self.ALLOWED_CHANNELS
        )

    def should_process_message(self, event: MessageCreate) -> bool:
        return (
            event.message.guild
            and event.message.guild.id == self.GUILD_ID
            and isinstance(event.message.channel, interactions.ThreadChannel)
            and bool(event.message.content)
        )

    @staticmethod
    async def has_admin_permissions(member: interactions.Member) -> bool:
        return any(
            role.permissions & interactions.Permissions.ADMINISTRATOR
            for role in member.roles
        )

    async def can_manage_message(
        self,
        thread: interactions.ThreadChannel,
        user: interactions.Member,
    ) -> bool:
        return await self.can_manage_post(thread, user)

    def should_process_link(self, event: MessageCreate) -> bool:
        guild = event.message.guild
        return (
            guild
            and guild.id == self.GUILD_ID
            and (member := guild.get_member(event.message.author.id))
            and event.message.content
            and not any(role.id == self.TAIWAN_ROLE_ID for role in member.roles)
        )

    async def is_user_banned(
        self, channel_id: str, post_id: str, author_id: str
    ) -> bool:
        return await asyncio.to_thread(
            self.model.is_user_banned, channel_id, post_id, author_id
        )
