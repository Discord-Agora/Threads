from __future__ import annotations

import asyncio
import contextlib
import functools
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum, auto
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
)

import aiofiles
import aiofiles.os
import cysimdjson
import interactions
import re2 as re
from cachetools import TTLCache
from interactions.api.events import MessageCreate, NewThreadCreate
from interactions.client.errors import Forbidden, NotFound
from yarl import URL

LOG_DIR: Final[str] = os.path.join(os.path.dirname(__file__), "logs")
os.makedirs(LOG_DIR, exist_ok=True)
LOG_FILE: Final[str] = os.path.join(LOG_DIR, "posts.log")
logger: Final[logging.Logger] = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)
file_handler: Final[logging.handlers.RotatingFileHandler] = (
    logging.handlers.RotatingFileHandler(
        LOG_FILE, maxBytes=1 * 1024 * 1024, backupCount=1
    )
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
        self.ban_lock = asyncio.Lock()
        self.ban_cache: Dict[Tuple[str, str, str], Tuple[bool, datetime]] = {}
        self.CACHE_DURATION = timedelta(minutes=5)
        self.parser = cysimdjson.JSONParser()

    async def load_banned_users(self, file_path: str) -> None:
        try:
            async with aiofiles.open(file_path, "r") as file:
                content = await file.read()
                self.banned_users = (
                    self.parser.parse(content) if content.strip() else {}
                )
        except Exception as e:
            logger.error(f"Error loading banned users data: {e}")
            self.banned_users = {}

    async def save_banned_users(self, file_path: str) -> None:
        async with aiofiles.open(file_path, "w") as file:
            await file.write(self.parser.dumps(self.banned_users, indent=4))

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
                await self.log_action(action_details)
        return result

    return wrapper


# Controller


class Posts(interactions.Extension):
    def __init__(self, bot: interactions.Client):
        self.bot = bot
        self.model = Model()
        self.BANNED_USERS_FILE: str = f"{os.path.dirname(__file__)}/banned_users.json"
        self.LOG_CHANNEL_ID: Final[int] = 1166627731916734504
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
        self, title: str, description: str = "", color: EmbedColor = EmbedColor.INFO
    ) -> interactions.Embed:
        embed = interactions.Embed(
            title=title, description=description, color=color.value
        )
        guild = await self.bot.fetch_guild(self.GUILD_ID)
        if guild and guild.icon:
            embed.set_footer(text=guild.name, icon_url=guild.icon.url)
        embed.timestamp = datetime.now()
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

    async def log_action(self, details: ActionDetails) -> None:
        timestamp = int(datetime.now().timestamp())

        log_message_parts = [
            f"{details.actor.mention} {details.action.name.lower()}ed",
            f"{details.target.mention} in" if details.target else None,
            f"{details.channel.mention}" if details.channel else None,
            f"({details.post_name}) on <t:{timestamp}:F> (<t:{timestamp}:R>).",
            f"Result: {details.result}. Reason: {details.reason}.",
        ]

        log_message = " ".join(filter(None, log_message_parts))

        if details.additional_info:
            log_message += " Additional info: " + ", ".join(
                f"{k}: {v}" for k, v in details.additional_info.items()
            )

        log_channel = await self.bot.fetch_channel(self.LOG_CHANNEL_ID)
        log_post = await log_channel.fetch_post(self.LOG_POST_ID)

        tasks = []
        if log_post.archived:
            tasks.append(log_post.edit(archived=False))

        log_embed = await self.create_embed(
            title="Action Log", description=log_message, color=EmbedColor.INFO
        )
        tasks.append(log_post.send(embeds=[log_embed]))

        if details.target and not details.target.bot:
            dm_embed = await self.create_embed(
                title=f"{details.action.name.capitalize()} Notification",
                description=self.get_notification_message(details),
                color=self.get_notification_color(details.action),
            )

            components = []
            if details.action == ActionType.LOCK:
                appeal_button = interactions.Button(
                    style=interactions.ButtonStyle.URL,
                    label="申诉",
                    url="https://discord.com/channels/1150630510696075404/1230132503273013358",
                )
                components.append(appeal_button)

            tasks.append(self.send_dm(details.target, dm_embed, components))

        await asyncio.gather(*tasks)

    async def send_dm(self, target, embed, components):
        try:
            await target.send(embeds=[embed], components=components)
        except Exception:
            logger.warning(f"Failed to send DM to {target.mention}")

    def get_notification_message(self, details: ActionDetails) -> str:
        action_name = details.action.name.lower()
        channel_mention = details.channel.mention if details.channel else "the post"
        target_mention = details.target.mention if details.target else "the user"

        notification_messages: Final[Mapping[ActionType, Callable[[], str]]] = {
            ActionType.LOCK: lambda: f"{channel_mention} has been locked. Reason: {details.reason}.",
            ActionType.UNLOCK: lambda: f"{channel_mention} has been unlocked. Reason: {details.reason}.",
            ActionType.BAN: lambda: f"{target_mention} has been banned from {channel_mention}. Reason: {details.reason}.",
            ActionType.UNBAN: lambda: f"{target_mention} has been unbanned from {channel_mention}. Reason: {details.reason}.",
            ActionType.DELETE: lambda: f"A message has been deleted from {channel_mention}. Reason: {details.reason}.",
            ActionType.EDIT: lambda: (
                f"A tag has been {details.additional_info.get('tag_action', 'modified')} "
                f"{'to' if details.additional_info.get('tag_action') == 'add' else 'from'} "
                f"{channel_mention}. Reason: {details.reason}."
            ),
        }

        return notification_messages.get(
            details.action,
            lambda: f"An action ({action_name}) has been performed in {channel_mention}. Reason: {details.reason}.",
        )()

    def get_notification_color(action: ActionType) -> EmbedColor:
        color_mapping: Final[Mapping[ActionType, EmbedColor]] = {
            ActionType.LOCK: EmbedColor.WARN,
            ActionType.BAN: EmbedColor.WARN,
            ActionType.DELETE: EmbedColor.WARN,
            ActionType.UNLOCK: EmbedColor.INFO,
            ActionType.UNBAN: EmbedColor.INFO,
            ActionType.EDIT: EmbedColor.INFO,
        }
        return color_mapping.get(action, EmbedColor.DEBUG)

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
            return

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
    ) -> ActionDetails:
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

        try:
            deletion_task = asyncio.create_task(message.delete())
            success_message_task = asyncio.create_task(
                self.send_success(ctx, "Message deleted successfully.")
            )

            await asyncio.gather(deletion_task, success_message_task)
        except asyncio.CancelledError:
            with contextlib.suppress(Exception):
                deletion_task.cancel()
                success_message_task.cancel()
            await self.send_error(
                ctx, "The deletion operation was cancelled. Please try again."
            )
            return None
        except Exception as e:
            logger.exception(f"Failed to delete message: {e}")
            await self.send_error(
                ctx,
                "An error occurred while deleting the message. Please try again later.",
            )
            return None

        return ActionDetails(
            action=ActionType.DELETE,
            reason="User-initiated message deletion",
            post_name=post.name,
            actor=ctx.author,
            channel=post,
            additional_info={
                "deleted_message_id": message.id,
                "deleted_message_author": str(message.author),
            },
        )

    @interactions.message_context_menu(name="Tags in Post")
    @log_action
    async def manage_post_tags(self, ctx: interactions.ContextMenuContext) -> None:
        if not await self.validate_channel(ctx) or not isinstance(
            ctx.channel, interactions.GuildForumPost
        ):
            await self.send_error(ctx, "This command can only be used in forum posts.")
            return

        if not await self.check_permissions(ctx):
            return

        post: Final[interactions.GuildForumPost] = ctx.channel
        available_tags: Final[Tuple[interactions.ForumTag, ...]] = (
            await self.fetch_available_tags(post.parent_id)
        )
        current_tag_ids: Final[Set[int]] = {tag.id for tag in post.applied_tags}

        options: Final[Tuple[interactions.StringSelectOption, ...]] = tuple(
            interactions.StringSelectOption(
                label=f"{'Remove' if tag.id in current_tag_ids else 'Add'}: {tag.name}",
                value=f"{'remove' if tag.id in current_tag_ids else 'add'}:{tag.id}",
                description=f"{'Currently applied' if tag.id in current_tag_ids else 'Not applied'}",
            )
            for tag in available_tags
        )

        select_menu: Final[interactions.StringSelectMenu] = (
            interactions.StringSelectMenu(
                *options,
                placeholder="Select tags to add or remove",
                custom_id=f"manage_tags:{post.id}",
                min_values=1,
                max_values=len(options),
            )
        )

        embed: Final[interactions.Embed] = await self.create_embed(
            title="Tags in Post",
            description="Select tags to add or remove from this post. You can select multiple tags at once.",
            color=EmbedColor.INFO,
        )

        await ctx.send(
            embeds=[embed],
            components=[select_menu],
            ephemeral=True,
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

        if target_user.id in {ctx.author.id, self.bot.user.id}:
            await self.send_error(ctx, "You cannot manage yourself or the bot.")
            return

        if post.owner_id != ctx.author.id:
            await self.send_error(
                ctx, "You don't have permission to manage users in this post."
            )
            return

        channel_id, post_id, user_id = map(
            str, (post.parent_id, post.id, target_user.id)
        )
        is_banned: Final[bool] = await self.model.is_user_banned(
            channel_id, post_id, user_id
        )

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

        if post.archived:
            await self.send_error(
                ctx,
                f"{post.mention} is archived and cannot be {action.name.lower()}ed.",
            )
            return None

        current_state: Final[bool] = post.locked
        desired_state: Final[bool] = action == ActionType.LOCK

        if current_state == desired_state:
            await self.send_error(ctx, f"The post is already {action.name.lower()}ed.")
            return None

        permissions_check, error_message = await self.check_permissions(ctx)
        if not permissions_check:
            await self.send_error(ctx, error_message)
            return None

        try:
            await post.edit(locked=desired_state)
        except Exception as e:
            logger.exception(f"Failed to {action.name.lower()} post")
            await self.send_error(
                ctx,
                f"An error occurred while trying to {action.name.lower()} the post: {str(e)}",
            )
            return None

        action_past_tense: Final[Literal["locked", "unlocked"]] = (
            "locked" if desired_state else "unlocked"
        )
        await self.send_success(ctx, f"Post has been {action_past_tense} successfully.")

        return ActionDetails(
            action=action,
            reason=reason,
            post_name=post.name,
            actor=ctx.author,
            channel=post,
        )

    manage_user_regex_pattern: Final = re.compile(r"manage_user:(\d+):(\d+):(\d+)")

    @interactions.component_callback(manage_user_regex_pattern)
    @log_action
    async def on_manage_user(self, ctx: interactions.ComponentContext) -> ActionDetails:
        if not (match := self.manage_user_regex_pattern.match(ctx.custom_id)):
            return await self.send_error(
                ctx, "Invalid custom ID format. Please try the action again."
            )

        channel_id, post_id, user_id = match.groups()
        try:
            action = ActionType[ctx.values[0].upper()]
        except KeyError:
            return await self.send_error(
                ctx, f"Invalid action: {ctx.values[0]}. Please try again."
            )

        try:
            member = await ctx.guild.fetch_member(int(user_id))
        except NotFound:
            return await self.send_error(
                ctx, f"User with ID {user_id} not found in the server."
            )
        except ValueError:
            return await self.send_error(
                ctx, f"Invalid user ID: {user_id}. Please try the action again."
            )

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
            return await self.send_error(
                ctx, "This command can only be used in specific forum posts."
            )

        post = ctx.channel
        if post.owner_id != ctx.author.id:
            return await self.send_error(
                ctx, f"You can only {action.name.lower()} users from your own posts."
            )

        channel_id, post_id, user_id = map(str, (post.parent_id, post.id, member.id))

        async with self.ban_lock:
            banned_users = self.banned_users
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

            await self.save_banned_users()

        await self.model.invalidate_ban_cache(channel_id, post_id, user_id)

        action_name: Literal["banned", "unbanned"] = (
            "unbanned" if action == ActionType.UNBAN else "banned"
        )
        dm_message = f"You have been {action_name} from {post.mention}."
        if action == ActionType.BAN:
            dm_message += (
                " If you continue to attempt to post, your comments will be deleted."
            )

        dm_embed = await self.create_embed(
            title=f"{action_name.capitalize()} Notification",
            description=dm_message,
            color=EmbedColor.INFO if action == ActionType.UNBAN else EmbedColor.WARN,
        )

        try:
            await member.send(embeds=[dm_embed])
        except Forbidden:
            logger.warning(
                f"Unable to send DM to {member.mention}. They may have DMs disabled."
            )

        await self.send_success(ctx, f"User has been {action_name} successfully.")

        return ActionDetails(
            action=action,
            reason=f"{action_name} by {ctx.author.mention}",
            post_name=post.name,
            actor=ctx.author,
            target=member,
            channel=post,
        )

    manage_tags_regex_pattern: Final = re.compile(r"manage_tags:(\d+)")

    @interactions.component_callback(manage_tags_regex_pattern)
    @log_action
    async def on_manage_tags(self, ctx: interactions.ComponentContext) -> ActionDetails:
        if not (match := self.manage_tags_regex_pattern.match(ctx.custom_id)):
            return await self.send_error(ctx, "Invalid custom ID format.")

        post_id = int(match.group(1))
        if not isinstance(
            post := await self.bot.fetch_channel(post_id), interactions.GuildForumPost
        ):
            return await self.send_error(ctx, "This is not a valid forum post.")

        action, tag_id = ctx.values[0].split(":")
        tag_id = int(tag_id)
        current_tag_ids = frozenset(tag.id for tag in post.applied_tags)

        tag_operation = {
            "add": (current_tag_ids.__contains__, current_tag_ids.union, "added to"),
            "remove": (
                current_tag_ids.__contains__,
                current_tag_ids.difference,
                "removed from",
            ),
        }.get(action)

        if not tag_operation:
            return await self.send_error(ctx, f"Invalid action: {action}")

        check_func, set_func, action_description = tag_operation

        if check_func(tag_id) == (action == "add"):
            return await self.send_error(
                ctx,
                f"Tag is already {'applied to' if action == 'add' else 'not in'} the post.",
            )

        new_tag_ids = set_func({tag_id})

        try:
            await post.edit(applied_tags=list(new_tag_ids))
        except Exception as e:
            logger.exception(f"Error editing post tags: {e}")
            return await self.send_error(
                ctx,
                "An error occurred while updating the post tags. Please try again later.",
            )

        tag_name = next(
            (tag.name for tag in post.parent.available_tags if tag.id == tag_id),
            "Unknown",
        )
        await self.send_success(
            ctx, f"Tag `{tag_name}` successfully {action_description} the post."
        )

        return ActionDetails(
            action=ActionType.EDIT,
            reason=f"Tag `{tag_name}` {action}ed {action_description} the post.",
            post_name=post.name,
            actor=ctx.author,
            channel=post,
            additional_info={
                "tag_action": action,
                "tag_id": tag_id,
                "tag_name": tag_name,
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

    @functools.lru_cache(maxsize=128)
    async def fetch_oldest_message_url(
        self, channel: interactions.GuildChannel
    ) -> Optional[str]:
        async for message in channel.history(limit=1):
            url = URL(message.jump_url)
            return str(url.with_path(url.path.rsplit("/", 1)[0] + "/0"))
        return None

    # Event methods

    @interactions.listen(MessageCreate)
    async def on_message_create(self, event: MessageCreate) -> None:
        tasks = [self.process_link(event)]

        if self.should_process_message(event):
            channel_id, post_id, author_id = map(
                str,
                (
                    event.message.channel.parent_id,
                    event.message.channel.id,
                    event.message.author.id,
                ),
            )

            if await self.model.is_user_banned(channel_id, post_id, author_id):
                tasks.append(event.message.delete())

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

        if await self.model.is_user_banned(channel_id, post_id, author_id):
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
        return all(
            (
                event.message.guild is not None,
                event.message.guild.id == self.GUILD_ID,
                (member := event.message.guild.get_member(event.message.author.id))
                is not None,
                not any(role.id == self.TAIWAN_ROLE_ID for role in member.roles),
                bool(event.message.content),
            )
        )

    # Utility methods

    async def is_user_banned(
        self, channel_id: str, post_id: str, author_id: str
    ) -> bool:
        return await asyncio.to_thread(
            self.model.is_user_banned, channel_id, post_id, author_id
        )

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
