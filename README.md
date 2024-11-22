# Threads

The **Threads** module is designed to manage thread-based conversations, user interactions, and content moderation within a Discord server. It supports all thread types.

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
- Manage post tags with an interactive interface
- Automatically feature high-activity posts
- Pin and unpin messages within threads
- View comprehensive statistics for posts and forums
- Dynamic threshold adjustment based on server activity
- Timeout voting system for temporary user restrictions

## Usage

### Slash Commands

- `/threads top`: Navigate to the top of the current thread
- `/threads lock`: Lock the current thread
  - Options: `reason` (string, required) - Reason for locking the thread
- `/threads unlock`: Unlock the current thread
  - Options: `reason` (string, required) - Reason for unlocking the thread
- `/threads timeout`: Start a timeout poll for a user
  - Options:
    - `user` (user, required) - The user to timeout
    - `reason` (string, required) - Reason for timeout
    - `duration` (integer, required) - Timeout duration in minutes (1-10)
- `/threads list`: List information for current thread
  - Options: `type` (choice, required) - Select data type to view:
    - `Banned Users`: View banned users in current thread
    - `Thread Permissions`: View users with special permissions in current thread
    - `Post Statistics`: View statistics for current post
- `/threads view`: View configuration data (requires Threads role)
  - Options: `type` (choice, required) - Select data type to view:
    - `Banned Users`: View all banned users across threads
    - `Thread Permissions`: View all thread permission assignments
    - `Post Statistics`: View post activity statistics
    - `Featured Threads`: View featured threads and their metrics

### Context Menus

- Message Context Menu:
  - **Message in Thread**: Delete, pin, or unpin the selected message within a thread
  - **Tags in Post**: Manage tags associated with a post (add or remove) through an interactive interface

- User Context Menu:
  - **User in Thread**: Ban, unban, share permissions, or revoke permissions for a specific user within a thread

### Quick Replies

Messages can be managed by replying with these commands:

- `del`: Delete the replied message
- `pin`: Pin the replied message
- `unpin`: Unpin the replied message

## Configuration

Customize the module by adjusting the configuration variables and constants defined in `main.py`. Key configuration options include:

- `LOG_CHANNEL_ID`: ID of the channel where logs will be sent
- `LOG_FORUM_ID`: ID of the forum channel for logging purposes
- `POLL_FORUM_ID`: ID of the forum channel where polls are created
- `TAIWAN_ROLE_ID`: ID of the role exempt from link transformation
- `THREADS_ROLE_ID`: ID of the role required for debug commands
- `GUILD_ID`: ID of your Discord server
- `TIMEOUT_CHANNEL_IDS`: Channels where timeout voting is allowed
- `TIMEOUT_REQUIRED_DIFFERENCE`: Required vote difference for timeout action
- `ROLE_CHANNEL_PERMISSIONS`: Defines roles and their associated channels for permission management
- `ALLOWED_CHANNELS`: Tuple of channel IDs where the module is active
- `FEATURED_CHANNELS`: Channels where featured posts are selected
- `message_count_threshold`: Dynamic threshold for featuring posts (auto-adjusted)
- `rotation_interval`: Dynamic interval for rotating featured posts (auto-adjusted)
- `BANNED_USERS_FILE`: Path to the JSON file storing banned users
- `THREAD_PERMISSIONS_FILE`: Path to the JSON file storing thread permissions
- `POST_STATS_FILE`: Path to the JSON file storing post statistics
- `FEATURED_POSTS_FILE`: Path to the JSON file storing featured posts
