# Threads

The **Threads** module is designed to manage thread-based conversations, user interactions, and content moderation within a Discord server. It supports all thread types including forum posts, text channel threads, and announcement threads.

## Features

- Prevent or allow further interactions within any thread
- Remove inappropriate or unwanted messages from threads
- Add or remove tags to categorize and highlight posts
- Rename new posts with timestamps and set up polls automatically
- Automatically select and rotate featured posts based on activity metrics
- Restrict or allow specific users from participating in particular threads
- Dynamically manage user permissions for specific threads
- Automatically process and sanitize links to protect user privacy
- Record all moderation actions for auditing and review
- Track message counts and activity within each post

## Usage

### Slash Commands

- `/posts top`: Navigate to the top of the current thread
- `/posts lock`: Lock the current thread
  - Options: `reason` (string, required) - Reason for locking the thread
- `/posts unlock`: Unlock the current thread
  - Options: `reason` (string, required) - Reason for unlocking the thread

### Context Menus

- Message Context Menu:
  - **Message in Thread**: Delete the selected message within a thread
  - **Tags in Post**: Manage tags associated with a post (add or remove).

- User Context Menu:
  - **User in Thread**: Ban, unban, share permissions, or revoke permissions for a specific user within a thread

## Configuration

Customize the module by adjusting the configuration variables and constants defined in `Posts.py`. Key configuration options include:

- `LOG_CHANNEL_ID`: ID of the channel where logs will be sent
- `LOG_FORUM_ID`: ID of the forum channel for logging purposes
- `POLL_FORUM_ID`: ID of the forum channel where polls are created
- `TAIWAN_ROLE_ID`: ID of the role to be assigned dynamic permissions
- `GUILD_ID`: ID of your Discord server
- `ROLE_CHANNEL_PERMISSIONS`: Defines roles and their associated channels for permission management
- `ALLOWED_CHANNELS`: Tuple of channel IDs where the module is active
- `SELECTED_CHANNELS`: Channels where featured posts are selected
- `message_count_threshold`: Minimum number of messages required for a post to be considered featured
- `rotation_interval`: Time interval for rotating featured posts
- `BANNED_USERS_FILE`: Path to the JSON file storing banned users
- `THREAD_PERMISSIONS_FILE`: Path to the JSON file storing thread permissions
- `POST_STATS_FILE`: Path to the JSON file storing post statistics
- `SELECTED_POSTS_FILE`: Path to the JSON file storing selected posts
