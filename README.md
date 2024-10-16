# Posts

This module is designed to manage forum posts, user interactions, and content moderation within a Discord server.

## Features

- Post locking and unlocking
- User banning and unbanning within specific posts
- Message deletion
- Tag management for forum posts
- Automatic processing of new posts
- Link sanitization for privacy protection
- Logging of moderation actions

## Usage

The module responds to various slash commands and context menu interactions:

- `/posts top`: Navigate to the top of the current post
- `/posts lock`: Lock the current post
- `/posts unlock`: Unlock the current post
- Right-click menu on messages: **Message in Post** for deletion
- Right-click menu on users: **User in Post** for banning/unbanning
- Right-click menu on messages: **Tags in Post** for tag management

## Configuration

You can customize various aspects of the module by modifying the constants and configurations at the beginning of the script, such as:

- `LOG_CHANNEL_ID`
- `LOG_FORUM_ID`
- `POLL_FORUM_ID`
- `TAIWAN_ROLE_ID`
- `GUILD_ID`
- `ROLE_CHANNEL_PERMISSIONS`
- `ALLOWED_CHANNELS`
