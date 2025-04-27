import os
from typing import Any, Dict, List, Optional, Set, Tuple, Union, cast

import interactions


async def flatten_history_iterator(
    history: interactions.ChannelHistory, reverse: bool = False
) -> List[interactions.Message]:
    """Flatten the ChannelHistory iterator while handling all kinds of errors.

    Args:
        history: Iterator for channel message history.
        reverse: Whether to output the list from beginning to end. Defaults to False.

    Returns:
        A list of messages from the channel history.
    """
    messages: List[interactions.Message] = []

    TERMINAL_ERROR_CODES: Set[int] = {50083, 10003, 50001, 50013}

    async for message in history:
        try:
            messages.append(message)
        except interactions.errors.HTTPException as error:
            try:
                error_code: int = int(error.code)
                if error_code in TERMINAL_ERROR_CODES:
                    break
                # Non-terminal errors (10008, 50021, 160005, etc.) are ignored
            except ValueError:
                continue
        except Exception:
            continue

    return messages[::-1] if reverse else messages


async def fetch_or_create_webhook(
    channel: interactions.WebhookMixin,
) -> interactions.Webhook:
    """Fetch the webhook from a destination channel or create one if it doesn't exist.

    Args:
        channel: Destination channel that supports webhooks.

    Returns:
        A webhook object associated with the channel.
    """
    channel = cast(interactions.WebhookMixin, channel)
    webhooks: List[interactions.Webhook] = await channel.fetch_webhooks()
    webhook_name: str = "Dyad Webhook"
    webhook_avatar: interactions.Absent[interactions.UPLOADABLE_TYPE] = (
        interactions.MISSING
    )

    try:
        return next(webhook for webhook in webhooks if webhook.name == webhook_name)
    except StopIteration:
        return await channel.create_webhook(name=webhook_name, avatar=webhook_avatar)


def format_poll_as_message(poll: interactions.Poll) -> str:
    """Convert a poll with its Q&A and results to a formatted message text.

    Args:
        poll: Discord Poll object to format.

    Returns:
        A string representation of the poll suitable for posting in a message.
    """

    def format_poll_media(poll_media: interactions.PollMedia) -> str:
        if isinstance(poll_media, interactions.PollMedia):
            poll_media = poll_media.to_dict()

        emoji_part = ""
        if emoji := poll_media.get("emoji"):
            emoji_part = (
                f"<:{emoji['name']}:{emoji['id']}>" if "id" in emoji else emoji["name"]
            )

        text_part = f" {poll_media['text']}" if "text" in poll_media else ""
        return f"{emoji_part}{text_part}"

    question_text: str = format_poll_media(poll.question)

    if poll.results:
        vote_counts: List[int] = [
            answer_count.count for answer_count in poll.results.answer_counts
        ]
        answers_iter = (
            f"{count:04d} - {format_poll_media(answer.poll_media)}"
            for count, answer in zip(vote_counts, poll.answers)
        )
        prefix = "(Poll finished) "
    else:
        answers_iter = (format_poll_media(answer.poll_media) for answer in poll.answers)
        prefix = ""

    return f"{prefix}{question_text}\n{chr(10).join(answers_iter)}"


async def migrate_message(
    source_msg: interactions.Message,
    target_channel: interactions.GuildChannel,
    thread_id: Optional[int] = None,
) -> Tuple[bool, Optional[int], Optional[interactions.Message]]:
    """Migrate a message to a target channel.

    Args:
        source_msg: The original message object to migrate.
        target_channel: Destination channel for the message.
        thread_id: Destination thread ID in the channel. Use 0 to create a new thread,
            None if not using a thread. Defaults to None.

    Returns:
        A tuple (success, thread_id, target_msg), where success indicates whether the
        operation was successful, thread_id is the destination thread ID (None if not a thread),
        and target_msg is the sent message object.
    """
    if not isinstance(
        target_channel, (interactions.GuildText, interactions.GuildForum)
    ):
        return False, None, None

    content: str = source_msg.content
    embeds: List[interactions.Embed] = source_msg.embeds
    attachments: List[interactions.Asset] = source_msg.attachments
    author_avatar: interactions.Asset = source_msg.author.display_avatar
    author_name: str = source_msg.author.display_name
    channel_name: str = source_msg.channel.name

    thread_reference: interactions.Snowflake_Type = (
        thread_id if thread_id and thread_id != 0 else None
    )
    thread_name: Optional[str] = channel_name if thread_id == 0 else None
    result_thread_id: Optional[int] = None

    webhook: interactions.Webhook = await fetch_or_create_webhook(
        channel=target_channel
    )

    if referenced_msg := source_msg.get_referenced_message():
        if any(
            referenced_msg.type == msg_type
            for msg_type in (
                interactions.MessageType.DEFAULT,
                interactions.MessageType.REPLY,
                interactions.MessageType.THREAD_STARTER_MESSAGE,
            )
        ):
            reply_content = (
                format_poll_as_message(referenced_msg.poll)
                if referenced_msg.poll
                else referenced_msg.content
            )
            quoted_lines = [f"> {line}" for line in reply_content.splitlines(False)]
            quote_header = f"> {referenced_msg.author.display_name} at {referenced_msg.created_at.strftime('%d/%m/%Y %H:%M')} said:"
            content = f"{quote_header}\n{chr(10).join(quoted_lines)}\n{content}"

    if attachments:
        attachment_urls = "\n".join(attachment.url for attachment in attachments)
        content = f"{attachment_urls}\n{content}"

    if source_msg.sticker_items:
        all_stickers = await target_channel.guild.fetch_all_custom_stickers()

        sticker_by_id = {sticker.id: sticker for sticker in all_stickers}
        sticker_by_name = {sticker.name: sticker for sticker in all_stickers}

        available_stickers = []
        unavailable_stickers = []

        for sticker_item in source_msg.sticker_items:
            if sticker := sticker_by_id.get(sticker_item.id) or sticker_by_name.get(
                sticker_item.name
            ):
                available_stickers.append(sticker)
            else:
                unavailable_stickers.append(sticker_item.name)

        if unavailable_stickers:
            content = (
                f"Sticker {','.join(unavailable_stickers)} not available\n{content}"
            )

        for sticker in reversed(available_stickers):
            content = f"{sticker.url}\n{content}"

    if source_msg.poll:
        content = f"{format_poll_as_message(source_msg.poll)}\n{content}"

    sent_msg: Optional[interactions.Message] = None
    MAX_MESSAGE_LENGTH: int = 2000

    for i in range(0, len(content), MAX_MESSAGE_LENGTH):
        chunk = content[i : i + MAX_MESSAGE_LENGTH]

        try:
            sent_msg = await webhook.send(
                content=chunk,
                embeds=embeds,
                username=f"{author_name} at {source_msg.created_at.strftime('%d/%m/%Y %H:%M')}",
                avatar_url=author_avatar.url,
                reply_to=sent_msg,
                allowed_mentions=interactions.AllowedMentions.none(),
                wait=True,
                thread=thread_reference,
                thread_name=thread_name,
            )
        except interactions.errors.HTTPException as error:
            error_reasons = {
                50083: "This thread is archived",
                10003: "The channel is unknown",
                10008: "The message is unknown",
                50001: "The bot has no access",
                50006: "Cannot send an empty message",
                50013: "The bot lacks the write permission to this channel",
                50021: "This thread is archived",
                160005: "This thread is locked",
            }

            error_code = int(error.code) if error.code is not None else None

            if error_code in (10003, 10008, 50001, 50013):
                return False, None, None

            error_description = error_reasons.get(
                error_code,
                (
                    f"Unknown error {error_code}"
                    if error_code
                    else "of unknown error code"
                ),
            )

            sent_msg = await webhook.send(
                content=f"Message {source_msg.jump_url} {source_msg.id} cannot be migrated because {error_description}",
                username=f"{author_name} at {source_msg.created_at.strftime('%d/%m/%Y %H:%M')}",
                avatar_url=author_avatar.url,
                wait=True,
                thread=thread_reference,
                thread_name=thread_name,
            )

        if sent_msg and isinstance(sent_msg.channel, interactions.ThreadChannel):
            result_thread_id = sent_msg.channel.id

    return True, result_thread_id, sent_msg


def is_empty_message(message: interactions.Message) -> bool:
    """Determine if a message has no content or interactive elements.

    Args:
        message: The message to check.

    Returns:
        True if the message is empty, False otherwise.
    """
    return not any(
        (
            getattr(message, attr, None)
            for attr in ("content", "embeds", "poll", "reactions", "sticker_items")
        )
    )


async def migrate_thread(
    source_thread: interactions.ThreadChannel,
    target_channel: Union[interactions.GuildText, interactions.GuildForum],
) -> None:
    """Migrate a thread to a target channel.

    Only supports threads in GuildText and GuildForumPost types.

    Args:
        source_thread: The thread channel to migrate.
        target_channel: The destination channel for the thread.
    """
    is_forum_post_to_forum = isinstance(
        source_thread, interactions.GuildForumPost
    ) and isinstance(target_channel, interactions.GuildForum)
    is_public_thread_to_text = (
        isinstance(source_thread, interactions.GuildPublicThread)
        and not isinstance(source_thread, interactions.GuildForumPost)
        and isinstance(target_channel, interactions.GuildText)
    )

    if not (is_forum_post_to_forum or is_public_thread_to_text):
        return

    messages: List[interactions.Message] = await flatten_history_iterator(
        source_thread.history(0), reverse=True
    )

    parent_message: Optional[interactions.Message] = None
    if isinstance(source_thread, interactions.GuildForumPost):
        parent_message = cast(interactions.GuildForumPost, source_thread).initial_post
    elif isinstance(source_thread, interactions.GuildPublicThread) and not isinstance(
        source_thread, interactions.GuildForumPost
    ):
        parent_message = source_thread.parent_message
    else:
        return

    thread_id: Optional[int] = None

    if parent_message is None:
        webhook = await fetch_or_create_webhook(channel=target_channel)

        if isinstance(target_channel, interactions.GuildForum):
            sent_msg = await webhook.send(
                content="This message has been deleted by original author",
                thread=None,
                thread_name=source_thread.name,
                wait=True,
            )
            thread_id = sent_msg.channel.id
        elif isinstance(target_channel, interactions.GuildText):
            sent_msg = await webhook.send(
                content="This message has been deleted by original author", wait=True
            )
            thread_id = (
                await sent_msg.create_thread(
                    name=source_thread.name, reason="Message migration"
                )
            ).id

    for i, message in enumerate(messages):
        if i == 0:
            if (
                isinstance(source_thread, interactions.GuildForumPost)
                and parent_message is not None
                and message != parent_message
            ):
                success, thread_id, _ = await migrate_message(
                    parent_message, target_channel, thread_id
                )
            elif (
                isinstance(source_thread, interactions.GuildPublicThread)
                and not isinstance(source_thread, interactions.GuildForumPost)
                and parent_message is not None
            ):
                success, _, sent_msg = await migrate_message(
                    parent_message, target_channel
                )
                thread_id = (
                    await sent_msg.create_thread(
                        name=source_thread.name, reason="Message migration"
                    )
                ).id

        if is_empty_message(message):
            continue

        success, new_thread_id, _ = await migrate_message(
            message, target_channel, thread_id
        )

        if new_thread_id is not None:
            thread_id = new_thread_id

        if not success and thread_id is None:
            break


async def migrate_channel(
    source_channel: Union[interactions.GuildText, interactions.GuildForum],
    target_channel: Union[interactions.GuildText, interactions.GuildForum],
    client: interactions.Client,
) -> None:
    """Migrate a channel to another destination channel.

    Only supports migration between GuildText and GuildForum channel types.

    Args:
        source_channel: The channel to migrate from.
        target_channel: The channel to migrate to.
        client: The Discord client instance.
    """
    match source_channel:
        case interactions.GuildForum():
            if not isinstance(target_channel, interactions.GuildForum):
                return

            source_channel = cast(interactions.GuildForum, source_channel)

            archived_data = await client.http.list_public_archived_threads(
                source_channel.id
            )
            archived_ids = [int(thread["id"]) for thread in archived_data["threads"]][
                ::-1
            ]

            for post_id in archived_ids:
                await migrate_thread(
                    await source_channel.fetch_post(id=post_id), target_channel
                )

            for post in (await source_channel.fetch_posts())[::-1]:
                await migrate_thread(post, target_channel)

        case interactions.GuildText():
            if not isinstance(target_channel, interactions.GuildText):
                return

            messages = await flatten_history_iterator(
                cast(interactions.GuildText, source_channel).history(0), reverse=True
            )

            for message in messages:
                (
                    await migrate_thread(message.thread, target_channel)
                    if message.thread
                    else await migrate_message(message, target_channel)
                )
