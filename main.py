from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
from logging.handlers import RotatingFileHandler
from typing import (
    Any,
    AsyncGenerator,
    Callable,
    Dict,
    Final,
    List,
    Literal,
    Mapping,
    Optional,
    Set,
    Tuple,
    Union,
)

import aiofiles
import aiofiles.os
import interactions
import orjson
from cachetools import TTLCache
from interactions.api.events import MessageCreate, NewThreadCreate
from interactions.client.errors import NotFound
from yarl import URL

LOG_DIR: Final[str] = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE: Final[str] = os.path.join(LOG_DIR, "posts.log")
logger: Final[logging.Logger] = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
file_handler: Final[RotatingFileHandler] = RotatingFileHandler(
    LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=1
)
file_handler.setLevel(logging.DEBUG)
formatter: Final[logging.Formatter] = logging.Formatter(
    "%(asctime)s - %(name)s - %(levelname)s - %(message)s"
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
    additional_info: Optional[Dict[str, Any]] = None


class Model:
    def __init__(self):
        self.banned_users: Dict[str, Dict[str, Set[str]]] = {}
        self.ban_cache: Dict[Tuple[str, str, str], Tuple[bool, datetime]] = {}
        self.CACHE_DURATION = timedelta(minutes=5)

    async def load_banned_users(self, file_path: str) -> None:
        try:
            async with aiofiles.open(file_path, "rb") as file:
                content = await file.read()
                loaded_data: Dict[str, Dict[str, list]] = (
                    orjson.loads(content) if content.strip() else {}
                )

                self.banned_users: Dict[str, Dict[str, Set[str]]] = defaultdict(
                    lambda: defaultdict(set)
                )
                for channel_id, channel_data in loaded_data.items():
                    for post_id, user_list in channel_data.items():
                        self.banned_users[channel_id][post_id].update(user_list)

        except FileNotFoundError:
            logger.warning(
                f"Banned users file not found: {file_path}. Creating a new one."
            )
            self.banned_users = defaultdict(lambda: defaultdict(set))
            await self.save_banned_users(file_path)
        except (orjson.JSONDecodeError, TypeError) as e:
            logger.error(f"Error decoding JSON data: {e}")
            self.banned_users = defaultdict(lambda: defaultdict(set))
        except Exception as e:
            logger.error(
                f"Unexpected error loading banned users data: {e}", exc_info=True
            )
            self.banned_users = defaultdict(lambda: defaultdict(set))

    async def save_banned_users(self, file_path: str) -> None:
        try:
            serializable_banned_users: Dict[str, Dict[str, list]] = {
                channel_id: {
                    post_id: list(user_set)
                    for post_id, user_set in channel_data.items()
                }
                for channel_id, channel_data in self.banned_users.items()
            }

            json_data = orjson.dumps(
                serializable_banned_users,
                option=orjson.OPT_INDENT_2
                | orjson.OPT_SORT_KEYS
                | orjson.OPT_SERIALIZE_NUMPY,
            )

            async with aiofiles.open(file_path, "wb") as file:
                await file.write(json_data)

            logger.info(f"Successfully saved banned users data to {file_path}")
        except (orjson.JSONEncodeError, TypeError) as e:
            logger.error(f"Error encoding banned users data: {e}")
        except IOError as e:
            logger.error(f"I/O error while saving banned users data: {e}")
        except Exception as e:
            logger.error(
                f"Unexpected error saving banned users data: {e}", exc_info=True
            )

    def is_user_banned(self, channel_id: str, post_id: str, user_id: str) -> bool:
        cache_key = (channel_id, post_id, user_id)
        current_time = datetime.now()

        if cache_key in self.ban_cache:
            is_banned, timestamp = self.ban_cache[cache_key]
            if current_time - timestamp < self.CACHE_DURATION:
                return is_banned

        is_banned = user_id in self.banned_users.get(channel_id, {}).get(post_id, set())

        self.ban_cache[cache_key] = (is_banned, current_time)

        return is_banned

    async def invalidate_ban_cache(
        self, channel_id: str, post_id: str, user_id: str
    ) -> None:
        cache_key = (channel_id, post_id, user_id)
        self.ban_cache.pop(cache_key, None)


# Decorator


def log_action(func):
    @functools.wraps(func)
    async def wrapper(self, ctx, *args, **kwargs):
        action_details = None
        try:
            result = await func(self, ctx, *args, **kwargs)
            if isinstance(result, ActionDetails):
                action_details = result
            else:
                return result
        except Exception as e:
            error_message = str(e)
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
                await self.log_action_internal(action_details)
        return result

    return wrapper


# Controller


class Posts(interactions.Extension):
    def __init__(self, bot: interactions.Client):
        self.bot = bot
        self.model = Model()
        self.ban_lock = asyncio.Lock()
        self.BANNED_USERS_FILE: str = f"{os.path.dirname(__file__)}/banned_users.json"
        self.LOG_CHANNEL_ID: Final[int] = 1166627731916734504
        self.LOG_FORUM_ID: Final[int] = 1159097493875871784
        self.LOG_POST_ID: Final[int] = 1279118293936111707
        self.POLL_FORUM_ID: Final[int] = 1155914521907568740
        self.TAIWAN_ROLE_ID: Final[int] = 1261328929013108778
        self.GUILD_ID: Final[int] = 1150630510696075404
        self.ROLE_CHANNEL_PERMISSIONS: Dict[int, List[int]] = {
            1223635198327914639: [
                1152311220557320202,
                1168209956802142360,
                1230197011761074340,
                1155914521907568740,
                1169032829548630107,
            ],
            1213490790341279754: [1185259262654562355],
        }
        self.ALLOWED_CHANNELS: List[int] = [
            1152311220557320202,
            1168209956802142360,
            1230197011761074340,
            1155914521907568740,
            1169032829548630107,
            1185259262654562355,
            1183048643071180871,
        ]
        asyncio.create_task(self.model.load_banned_users(self.BANNED_USERS_FILE))
        self.url_cache = TTLCache(maxsize=1024, ttl=3600)

    # View methods

    async def create_embed(
        self,
        title: str,
        description: str = "",
        color: Union[EmbedColor, int] = EmbedColor.INFO,
    ) -> interactions.Embed:
        if isinstance(color, EmbedColor):
            color_value = color.value
        else:
            color_value = color

        embed = interactions.Embed(
            title=title, description=description, color=color_value
        )
        guild = await self.bot.fetch_guild(self.GUILD_ID)
        if guild and guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        embed.timestamp = datetime.now()
        embed.set_footer(text="鍵政大舞台")
        return embed

    async def send_response(
        self,
        ctx: interactions.InteractionContext,
        title: str,
        message: str,
        color: EmbedColor,
    ) -> None:
        await ctx.send(
            embed=await self.create_embed(title, message, color),
            ephemeral=True,
        )

    async def send_error(
        self, ctx: interactions.InteractionContext, message: str
    ) -> None:
        await self.send_response(ctx, "Error", message, EmbedColor.ERROR)

    async def send_success(
        self, ctx: interactions.InteractionContext, message: str
    ) -> None:
        await self.send_response(ctx, "Success", message, EmbedColor.INFO)

    async def log_action_internal(self, details: ActionDetails) -> None:
        logger.debug(f"log_action_internal called for action: {details.action}")
        timestamp: Final[int] = int(datetime.now().timestamp())

        log_embed: Final[interactions.Embed] = await self.create_embed(
            title=f"Action Log: {details.action.name.capitalize()}",
            color=self.get_action_color(details.action),
        )

        log_embed.add_field(name="Actor", value=details.actor.mention, inline=True)
        log_embed.add_field(
            name="Post",
            value=f"{details.channel.mention}",
            inline=True,
        )
        log_embed.add_field(
            name="Time", value=f"<t:{timestamp}:F> (<t:{timestamp}:R>)", inline=True
        )

        if details.target:
            log_embed.add_field(
                name="Target", value=details.target.mention, inline=True
            )

        log_embed.add_field(
            name="Result", value=details.result.capitalize(), inline=True
        )
        log_embed.add_field(name="Reason", value=details.reason, inline=False)

        if details.additional_info:
            formatted_info = self.format_additional_info(details.additional_info)
            log_embed.add_field(
                name="Additional Info", value=formatted_info, inline=False
            )

        tasks: List[asyncio.Task] = []

        log_channel, log_forum = await asyncio.gather(
            self.bot.fetch_channel(self.LOG_CHANNEL_ID),
            self.bot.fetch_channel(self.LOG_FORUM_ID),
        )
        log_post = await log_forum.fetch_post(self.LOG_POST_ID)

        if log_post.archived:
            tasks.append(asyncio.create_task(log_post.edit(archived=False)))

        log_key = f"{details.action}_{details.post_name}_{timestamp}"
        if hasattr(self, "_last_log_key") and self._last_log_key == log_key:
            logger.warning(f"Duplicate log detected: {log_key}")
            return

        self._last_log_key = log_key

        tasks.extend(
            [
                asyncio.create_task(log_post.send(embeds=[log_embed])),
                asyncio.create_task(log_channel.send(embeds=[log_embed])),
            ]
        )

        if details.target and not details.target.bot:
            dm_embed: Final[interactions.Embed] = await self.create_embed(
                title=f"{details.action.name.capitalize()} Notification",
                description=self.get_notification_message(details),
                color=self.get_action_color(details.action),
            )

            components: List[interactions.Button] = []
            if details.action == ActionType.LOCK:
                appeal_button: Final[interactions.Button] = interactions.Button(
                    style=interactions.ButtonStyle.URL,
                    label="Appeal",
                    url="https://discord.com/channels/1150630510696075404/1230132503273013358",
                )
                components.append(appeal_button)

            if details.action in {
                ActionType.LOCK,
                ActionType.UNLOCK,
                ActionType.DELETE,
                ActionType.BAN,
                ActionType.UNBAN,
            }:
                tasks.append(
                    asyncio.create_task(
                        self.send_dm(details.target, dm_embed, components)
                    )
                )

        await asyncio.gather(*tasks)

    def get_action_color(self, action: ActionType) -> int:
        color_mapping: Final[Dict[ActionType, EmbedColor]] = {
            ActionType.LOCK: EmbedColor.WARN,
            ActionType.BAN: EmbedColor.ERROR,
            ActionType.DELETE: EmbedColor.WARN,
            ActionType.UNLOCK: EmbedColor.INFO,
            ActionType.UNBAN: EmbedColor.INFO,
            ActionType.EDIT: EmbedColor.INFO,
        }
        return color_mapping.get(action, EmbedColor.DEBUG).value

    async def send_dm(self, target, embed, components):
        try:
            await target.send(embeds=[embed], components=components)
        except Exception:
            logger.warning(f"Failed to send DM to {target.mention}")

    def get_notification_message(self, details: ActionDetails) -> str:
        channel_mention = details.channel.mention if details.channel else "the post"

        notification_messages: Final[Mapping[ActionType, Callable[[], str]]] = {
            ActionType.LOCK: lambda: f"{channel_mention} has been locked.",
            ActionType.UNLOCK: lambda: f"{channel_mention} has been unlocked.",
            ActionType.DELETE: lambda: f"Your message has been deleted from {channel_mention}.",
            ActionType.EDIT: lambda: (
                f"A tag has been {details.additional_info.get('tag_action', 'modified')} "
                f"{'to' if details.additional_info.get('tag_action') == 'add' else 'from'} "
                f"{channel_mention}."
            ),
            ActionType.BAN: lambda: f"You have been banned from {channel_mention}. If you continue to attempt to post, your comments will be deleted.",
            ActionType.UNBAN: lambda: f"You have been unbanned from {channel_mention}.",
        }

        message = notification_messages.get(
            details.action,
            lambda: f"An action ({details.action.name.lower()}) has been performed in {channel_mention}.",
        )()

        if details.action not in {ActionType.BAN, ActionType.UNBAN}:
            message += f" Reason: {details.reason}"

        return message

    def format_additional_info(self, info: Dict[str, Any]) -> str:
        formatted = []
        for key, value in info.items():
            if isinstance(value, list) and value and isinstance(value[0], dict):
                formatted.append(f"**{key.replace('_', ' ').title()}**:")
                for item in value:
                    formatted.extend(f"• {k}: {v}" for k, v in item.items())
            else:
                formatted.append(f"**{key.replace('_', ' ').title()}**: {value}")
        return "\n".join(formatted)

    # Command methods

    module_base: interactions.SlashCommand = interactions.SlashCommand(
        name="posts", description="Posts commands"
    )

    @module_base.subcommand("top", sub_cmd_description="Return to the top")
    async def navigate_to_top_post(self, ctx: interactions.SlashContext):
        post: Final[interactions.GuildForumPost] = ctx.channel
        message_url = await self.fetch_oldest_message_url(post)
        if message_url:
            await self.send_success(
                ctx,
                f"Here's the link to the top of the post: [Click here]({message_url}).",
            )
        else:
            await self.send_error(ctx, "Unable to find the top message in this post.")

    @module_base.subcommand("lock", sub_cmd_description="Lock the current post")
    @interactions.slash_option(
        name="reason",
        description="Reason for locking the post",
        required=True,
        opt_type=interactions.OptionType.STRING,
    )
    @log_action
    async def lock_post(
        self, ctx: interactions.SlashContext, reason: str
    ) -> ActionDetails:
        return await self.toggle_post_lock(ctx, ActionType.LOCK, reason)

    @module_base.subcommand("unlock", sub_cmd_description="Unlock the current post")
    @interactions.slash_option(
        name="reason",
        description="Reason for unlocking the post",
        required=True,
        opt_type=interactions.OptionType.STRING,
    )
    @log_action
    async def unlock_post(
        self, ctx: interactions.SlashContext, reason: str
    ) -> ActionDetails:
        return await self.toggle_post_lock(ctx, ActionType.UNLOCK, reason)

    @interactions.message_context_menu(name="Message in Post")
    @log_action
    async def delete_message(
        self, ctx: interactions.ContextMenuContext
    ) -> Optional[ActionDetails]:
        if not isinstance(ctx.channel, interactions.GuildForumPost):
            await self.send_error(ctx, "This command can only be used in forum posts.")
            return None

        post: Final[interactions.GuildForumPost] = ctx.channel
        message: Final[interactions.Message] = ctx.target

        if not await self.can_delete_message(post, ctx.author, message):
            await self.send_error(
                ctx, "You don't have permission to delete this message."
            )
            return None

        async def execute_deletion() -> Optional[ActionDetails]:
            try:
                deletion_task = asyncio.create_task(message.delete())
                success_message_task = asyncio.create_task(
                    self.send_success(ctx, "Message deleted successfully.")
                )

                await asyncio.gather(deletion_task, success_message_task)

                return ActionDetails(
                    action=ActionType.DELETE,
                    reason="User-initiated message deletion",
                    post_name=post.name,
                    actor=ctx.author,
                    channel=post,
                    target=message.author,
                    additional_info={
                        "deleted_message_id": message.id,
                        "deleted_message_content": (
                            message.content[:1000] if message.content else "N/A"
                        ),
                        "deleted_message_attachments": [
                            att.url for att in message.attachments
                        ],
                    },
                )
            except asyncio.CancelledError:
                await self.send_error(
                    ctx, "The deletion operation was cancelled. Please try again."
                )
            except Exception as e:
                logger.exception(f"Failed to delete message: {e}")
                await self.send_error(
                    ctx,
                    "An error occurred while deleting the message. Please try again later.",
                )
            return None

        return await asyncio.wait_for(execute_deletion(), timeout=10.0)

    @interactions.message_context_menu(name="Tags in Post")
    @log_action
    async def manage_post_tags(self, ctx: interactions.ContextMenuContext) -> None:
        logger.info(f"manage_post_tags called for post {ctx.channel.id}")
        if not await self.validate_channel(ctx) or not isinstance(
            ctx.channel, interactions.GuildForumPost
        ):
            logger.warning(f"Invalid channel for manage_post_tags: {ctx.channel.id}")
            await self.send_error(ctx, "This command can only be used in forum posts.")
            return

        if not await self.check_permissions(ctx):
            logger.warning(f"Insufficient permissions for user {ctx.author.id}")
            return

        post: Final[interactions.GuildForumPost] = ctx.channel
        try:
            available_tags: Final[Tuple[interactions.ForumTag, ...]] = (
                await self.fetch_available_tags(post.parent_id)
            )
            logger.info(f"Available tags for post {post.id}: {available_tags}")
        except Exception as e:
            logger.error(f"Error fetching available tags: {e}", exc_info=True)
            await self.send_error(
                ctx, "An error occurred while fetching available tags."
            )
            return

        current_tag_ids: Final[Set[int]] = {tag.id for tag in post.applied_tags}
        logger.info(f"Current tag IDs for post {post.id}: {current_tag_ids}")

        options: Final[Tuple[interactions.StringSelectOption, ...]] = tuple(
            interactions.StringSelectOption(
                label=f"{'Remove' if tag.id in current_tag_ids else 'Add'}: {tag.name}",
                value=f"{'remove' if tag.id in current_tag_ids else 'add'}:{tag.id}",
                description=f"{'Currently applied' if tag.id in current_tag_ids else 'Not applied'}",
            )
            for tag in available_tags
        )
        logger.info(f"Created {len(options)} options for select menu")

        select_menu: Final[interactions.StringSelectMenu] = (
            interactions.StringSelectMenu(
                *options,
                placeholder="Select tags to add or remove",
                custom_id=f"manage_tags:{post.id}",
                min_values=1,
                max_values=len(options),
            )
        )
        logger.info(f"Created select menu with custom_id: manage_tags:{post.id}")

        embed: Final[interactions.Embed] = await self.create_embed(
            title="Tags in Post",
            description="Select tags to add or remove from this post. You can select multiple tags at once.",
            color=EmbedColor.INFO,
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

    @interactions.user_context_menu(name="User in Post")
    @log_action
    async def manage_user_in_forum_post(
        self, ctx: interactions.ContextMenuContext
    ) -> None:
        if not await self.validate_channel(ctx) or not isinstance(
            ctx.channel, interactions.GuildForumPost
        ):
            await self.send_error(
                ctx, "This command can only be used in specific forum posts."
            )
            return

        post: Final[interactions.GuildForumPost] = ctx.channel
        target_user: Final[interactions.Member] = ctx.target

        # if target_user.id in {ctx.author.id, self.bot.user.id}:
        # await self.send_error(ctx, "You cannot manage yourself or the bot.")
        # return

        if post.owner_id != ctx.author.id:
            await self.send_error(
                ctx, "You don't have permission to manage users in this post."
            )
            return

        channel_id, post_id, user_id = map(
            str, (post.parent_id, post.id, target_user.id)
        )
        is_banned: Final[bool] = await self.is_user_banned(channel_id, post_id, user_id)

        options: Final[Tuple[interactions.StringSelectOption, ...]] = (
            interactions.StringSelectOption(label="Ban", value="ban"),
            interactions.StringSelectOption(label="Unban", value="unban"),
        )
        select_menu: Final[interactions.StringSelectMenu] = (
            interactions.StringSelectMenu(
                *options,
                placeholder="Select action for user",
                custom_id=f"manage_user:{channel_id}:{post_id}:{user_id}",
            )
        )

        embed: Final[interactions.Embed] = await self.create_embed(
            title="User in Post",
            description=f"Select action for {target_user.mention}:\n"
            f"Current status: {'Banned' if is_banned else 'Not banned'} in this post.",
            color=EmbedColor.INFO,
        )

        await ctx.send(embeds=[embed], components=[select_menu], ephemeral=True)

    # Serve

    @log_action
    async def toggle_post_lock(
        self, ctx: interactions.SlashContext, action: ActionType, reason: str
    ) -> Optional[ActionDetails]:
        if not isinstance(ctx.channel, interactions.GuildForumPost):
            await self.send_error(ctx, "This command can only be used in forum posts.")
            return None

        post: Final[interactions.GuildForumPost] = ctx.channel
        desired_state: Final[bool] = action == ActionType.LOCK
        action_name: Final[str] = action.name.lower()
        action_past_tense: Final[Literal["locked", "unlocked"]] = (
            "locked" if desired_state else "unlocked"
        )

        async def check_conditions() -> Optional[str]:
            if post.archived:
                return f"{post.mention} is archived and cannot be {action_name}ed."
            if post.locked == desired_state:
                return f"The post is already {action_name}ed."
            permissions_check, error_message = await self.check_permissions(ctx)
            return error_message if not permissions_check else None

        if error_message := await check_conditions():
            await self.send_error(ctx, error_message)
            return None

        try:
            await asyncio.wait_for(post.edit(locked=desired_state), timeout=5.0)
        except asyncio.TimeoutError:
            logger.warning(f"Timeout while trying to {action_name} post {post.id}")
            await self.send_error(
                ctx, f"Operation timed out while trying to {action_name} the post."
            )
            return None
        except Exception as e:
            logger.exception(f"Failed to {action_name} post {post.id}")
            await self.send_error(
                ctx,
                f"An error occurred while trying to {action_name} the post: {str(e)}",
            )
            return None

        await self.send_success(ctx, f"Post has been {action_past_tense} successfully.")

        return ActionDetails(
            action=action,
            reason=reason,
            post_name=post.name,
            actor=ctx.author,
            channel=post,
            additional_info={
                "previous_state": "Unlocked" if action == ActionType.LOCK else "Locked",
                "new_state": "Locked" if action == ActionType.LOCK else "Unlocked",
            },
        )

    manage_user_regex_pattern = re.compile(r"manage_user:(\d{19}):(\d{19}):(\d{19})")

    @interactions.component_callback(manage_user_regex_pattern)
    @log_action
    async def on_manage_user(
        self, ctx: interactions.ComponentContext
    ) -> Optional[ActionDetails]:
        logger.info(f"on_manage_user called with custom_id: {ctx.custom_id}")

        if not (match := self.manage_user_regex_pattern.match(ctx.custom_id)):
            logger.warning(f"Invalid custom ID format: {ctx.custom_id}")
            await self.send_error(
                ctx, "Invalid custom ID format. Please try the action again."
            )
            return None

        channel_id, post_id, user_id = match.groups()
        logger.info(
            f"Parsed IDs - channel: {channel_id}, post: {post_id}, user: {user_id}"
        )

        if not ctx.values:
            logger.warning("No action selected")
            await self.send_error(ctx, "No action selected. Please try again.")
            return None

        try:
            action = ActionType[ctx.values[0].upper()]
        except KeyError:
            logger.warning(f"Invalid action: {ctx.values[0]}")
            await self.send_error(
                ctx, f"Invalid action: {ctx.values[0]}. Please try again."
            )
            return None

        try:
            member = await ctx.guild.fetch_member(int(user_id))
        except NotFound:
            logger.warning(f"User with ID {user_id} not found in the server")
            await self.send_error(
                ctx, f"User with ID {user_id} not found in the server."
            )
            return None
        except ValueError:
            logger.warning(f"Invalid user ID: {user_id}")
            await self.send_error(
                ctx, f"Invalid user ID: {user_id}. Please try the action again."
            )
            return None

        return await self.ban_unban_user(ctx, member, action)

    @log_action
    async def ban_unban_user(
        self,
        ctx: interactions.ContextMenuContext,
        member: interactions.Member,
        action: ActionType,
    ) -> ActionDetails:
        if not await self.validate_channel(ctx) or not isinstance(
            ctx.channel, interactions.GuildForumPost
        ):
            await self.send_error(
                ctx, "This command can only be used in specific forum posts."
            )
            return None

        post = ctx.channel
        if post.owner_id != ctx.author.id:
            await self.send_error(
                ctx, f"You can only {action.name.lower()} users from your own posts."
            )
            return None

        channel_id, post_id, user_id = map(str, (post.parent_id, post.id, member.id))

        async with self.ban_lock:
            banned_users = self.model.banned_users
            channel_users = banned_users.setdefault(channel_id, {})
            post_users = channel_users.setdefault(post_id, set())

            if action == ActionType.BAN:
                post_users.add(user_id)
            elif action == ActionType.UNBAN:
                post_users.discard(user_id)

            if not post_users:
                del channel_users[post_id]
            if not channel_users:
                del banned_users[channel_id]

            await self.model.save_banned_users(self.BANNED_USERS_FILE)

        await self.model.invalidate_ban_cache(channel_id, post_id, user_id)

        action_name = "banned" if action == ActionType.BAN else "unbanned"
        await self.send_success(ctx, f"User has been {action_name} successfully.")

        return ActionDetails(
            action=action,
            reason=f"{action_name.capitalize()} by {ctx.author.mention}",
            post_name=post.name,
            actor=ctx.author,
            target=member,
            channel=post,
            additional_info={
                "action_type": action_name.capitalize(),
                "affected_user": str(member),
                "affected_user_id": member.id,
            },
        )

    manage_tags_regex_pattern = re.compile(r"manage_tags:(\d{19})")

    @interactions.component_callback(manage_tags_regex_pattern)
    @log_action
    async def on_manage_tags(self, ctx: interactions.ComponentContext) -> ActionDetails:
        logger.info(f"on_manage_tags called with custom_id: {ctx.custom_id}")

        if not (match := self.manage_tags_regex_pattern.match(ctx.custom_id)):
            logger.warning(f"Invalid custom ID format: {ctx.custom_id}")
            return await self.send_error(ctx, "Invalid custom ID format.")

        post_id = int(match.group(1))

        try:
            post, parent_forum = await asyncio.gather(
                self.bot.fetch_channel(post_id),
                self.bot.fetch_channel(ctx.channel.parent_id),
            )
        except Exception as e:
            logger.error(f"Error fetching channels: {e}", exc_info=True)
            return await self.send_error(ctx, "Failed to fetch the required channels.")

        if not isinstance(post, interactions.GuildForumPost):
            logger.warning(f"Channel {post_id} is not a GuildForumPost")
            return await self.send_error(ctx, "This is not a valid forum post.")

        tag_updates: Dict[str, Set[int]] = defaultdict(set)
        for value in ctx.values:
            action, tag_id = value.split(":")
            tag_updates[action].add(int(tag_id))

        logger.info(f"Tag updates for post {post_id}: {dict(tag_updates)}")

        current_tag_ids: Set[int] = {tag.id for tag in post.applied_tags}
        new_tag_ids: Set[int] = (current_tag_ids | tag_updates["add"]) - tag_updates[
            "remove"
        ]

        if new_tag_ids == current_tag_ids:
            return await self.send_success(ctx, "No changes were made to the tags.")

        try:
            await post.edit(applied_tags=list(new_tag_ids))
            logger.info(f"Successfully updated tags for post {post_id}")
        except Exception as e:
            logger.exception(f"Error editing post tags: {e}")
            return await self.send_error(
                ctx,
                "An error occurred while updating the post tags. Please try again later.",
            )

        tag_names: Dict[int, str] = {
            tag.id: tag.name for tag in parent_forum.available_tags
        }

        update_messages: List[str] = [
            f"Tag `{tag_names.get(tag_id, 'Unknown')}` {'added to' if action == 'add' else 'removed from'} the post."
            for action, tag_ids in tag_updates.items()
            for tag_id in tag_ids
        ]

        await self.send_success(ctx, "\n".join(update_messages))

        return ActionDetails(
            action=ActionType.EDIT,
            reason="Tags updated in the post",
            post_name=post.name,
            actor=ctx.author,
            channel=post,
            additional_info={
                "tag_updates": [
                    {
                        "Action": action.capitalize(),
                        "Tag": tag_names.get(tag_id, "Unknown"),
                    }
                    for action, tag_ids in tag_updates.items()
                    for tag_id in tag_ids
                ],
            },
        )

    async def process_new_post(self, thread: interactions.GuildPublicThread) -> None:
        try:
            timestamp = datetime.now().strftime("%y%m%d%H%M")
            new_title = f"[{timestamp}] {thread.name}"
            await thread.edit(name=new_title)

            poll = interactions.Poll.create(
                question="Do you support this petition?",
                duration=48,
                allow_multiselect=False,
                answers=["Support", "Oppose", "Abstain"],
            )
            await thread.send(poll=poll)

        except Exception as e:
            logger.error(
                f"Error processing thread {thread.id}: {str(e)}", exc_info=True
            )

    async def process_link(self, event: MessageCreate) -> None:
        if not self.should_process_link(event):
            return

        new_content = await self.transform_links(event.message.content)
        if new_content == event.message.content:
            return

        await asyncio.gather(
            self.send_warning(event.message.author, self.get_warning_message()),
            self.replace_message(event, new_content),
        )

    async def send_warning(self, user: interactions.Member, message: str) -> None:
        embed = await self.create_embed(
            title="Warning", description=message, color=EmbedColor.WARN
        )
        try:
            await user.send(embeds=[embed])
        except Exception as e:
            logger.warning(f"Failed to send warning DM to {user.mention}: {e}")

    @contextlib.asynccontextmanager
    async def create_temp_webhook(
        self, channel: interactions.TextChannel, name: str
    ) -> AsyncGenerator[interactions.Webhook, None]:
        webhook = await channel.create_webhook(name=name)
        try:
            yield webhook
        finally:
            with contextlib.suppress(Exception):
                await webhook.delete()

    async def replace_message(self, event: MessageCreate, new_content: str) -> None:
        channel = event.message.channel
        async with self.create_temp_webhook(channel, "Temp Webhook") as webhook:
            try:
                await asyncio.gather(
                    webhook.send(
                        content=new_content,
                        username=event.message.author.display_name,
                        avatar_url=event.message.author.avatar_url,
                    ),
                    event.message.delete(),
                )
            except Exception as e:
                logger.exception(f"Failed to replace message: {e}")

    async def fetch_oldest_message_url(
        self, channel: interactions.GuildChannel
    ) -> Optional[str]:
        try:
            async for message in channel.history(limit=1, oldest_first=True):
                url = URL(message.jump_url)
                return str(url.with_path(url.path.rsplit("/", 1)[0] + "/0"))
        except Exception as e:
            logger.error(f"Error fetching oldest message: {e}")
        return None

    # Event methods

    @interactions.listen(MessageCreate)
    async def on_message_create(self, event: MessageCreate) -> None:
        tasks = []

        if event.message.guild:
            tasks.append(self.process_link(event))

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
                    tasks.append(event.message.delete())

        if tasks:
            await asyncio.gather(*tasks)

    @interactions.listen(NewThreadCreate)
    async def on_new_thread_create(self, event: NewThreadCreate) -> None:
        if not (
            isinstance(event.thread, interactions.GuildPublicThread)
            and event.thread.parent_id == self.POLL_FORUM_ID
            and event.thread.owner_id is not None
        ):
            return

        guild = await self.bot.fetch_guild(self.GUILD_ID)
        owner = await guild.fetch_member(event.thread.owner_id)

        if owner and not owner.bot:
            await self.process_new_post(event.thread)

    @interactions.listen(MessageCreate)
    async def on_message_create_for_banned_users(self, event: MessageCreate) -> None:
        if not (
            event.message.guild
            and isinstance(event.message.channel, interactions.GuildForumPost)
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

        if await self.is_user_banned(channel_id, post_id, author_id):
            await event.message.delete()

    # Check methods

    async def check_permissions(
        self, ctx: interactions.SlashContext
    ) -> Tuple[bool, str]:
        author_roles_ids: Set[int] = {role.id for role in ctx.author.roles}
        parent_id: int = ctx.channel.parent_id

        has_perm: bool = any(
            role_id in self.ROLE_CHANNEL_PERMISSIONS
            and parent_id in self.ROLE_CHANNEL_PERMISSIONS[role_id]
            for role_id in author_roles_ids
        )

        return has_perm, (
            "" if has_perm else "You do not have permission for this action."
        )

    async def validate_channel(self, ctx: interactions.InteractionContext) -> bool:
        return (
            isinstance(ctx.channel, interactions.GuildForumPost)
            and ctx.channel.parent_id in self.ALLOWED_CHANNELS
        )

    def should_process_message(self, event: MessageCreate) -> bool:
        return (
            event.message.guild
            and event.message.guild.id == self.GUILD_ID
            and isinstance(event.message.channel, interactions.GuildForumPost)
            and bool(event.message.content)
        )

    async def can_delete_message(
        self,
        post: interactions.GuildForumPost,
        author: interactions.Member,
        message: interactions.Message,
    ) -> bool:
        if post.owner_id == author.id or message.author.id == author.id:
            return True

        author_role_ids = {role.id for role in author.roles}
        return any(
            role_id in author_role_ids and post.parent_id in channels
            for role_id, channels in self.ROLE_CHANNEL_PERMISSIONS.items()
        )

    def should_process_link(self, event: MessageCreate) -> bool:
        if not event.message.guild or event.message.guild.id != self.GUILD_ID:
            return False

        member = event.message.guild.get_member(event.message.author.id)
        if not member:
            return False

        if any(role.id == self.TAIWAN_ROLE_ID for role in member.roles):
            return False

        return bool(event.message.content)

    # Utility methods

    async def is_user_banned(
        self, channel_id: str, post_id: str, author_id: str
    ) -> bool:
        return self.model.is_user_banned(channel_id, post_id, author_id)

    @functools.lru_cache(maxsize=32)
    async def fetch_available_tags(
        self, parent_id: int
    ) -> Tuple[interactions.ForumTag, ...]:
        channel = await self.bot.fetch_channel(parent_id)
        return tuple(channel.available_tags or ())

    @functools.lru_cache(maxsize=1024)
    def sanitize_url(
        self, url_str: str, preserve_params: Tuple[str, ...] = ("p",)
    ) -> str:
        url = URL(url_str)
        query = {k: v for k, v in url.query.items() if k in preserve_params}
        return str(url.with_query(query))

    @functools.lru_cache(maxsize=1)
    def get_link_transformations(
        self,
    ) -> List[Tuple[re.Pattern, Callable[[str], str]]]:
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
            ),
        ]

    async def transform_links(self, content: str) -> str:
        def transform_url(match: re.Match) -> str:
            url = match.group(0)
            for pattern, transform in self.get_link_transformations():
                if pattern.match(url):
                    return transform(url)
            return url

        return await asyncio.to_thread(
            lambda: re.sub(r"https?://\S+", transform_url, content, flags=re.IGNORECASE)
        )

    def get_warning_message(self) -> str:
        return (
            "The link you sent may expose your ID. "
            "To protect the privacy of members, sending such links is prohibited. "
            "Network Security Manual: https://discord.com/channels/1150630510696075404/1268017202397839493."
        )
