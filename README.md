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

## Usage

### Slash Commands

- `/posts top`: Navigate to the top of the current thread
- `/posts lock`: Lock the current thread
  - Options: `reason` (string, required) - Reason for locking the thread
- `/posts unlock`: Unlock the current thread
  - Options: `reason` (string, required) - Reason for unlocking the thread
- `/posts list banned`: View banned users in current thread
- `/posts list permissions`: View users with special permissions in current thread
- `/posts list stats`: View statistics for current post
- `/posts debug featured`: View all featured threads (requires Threads role)
- `/posts debug stats`: View post activity statistics (requires Threads role)
- `/posts debug banned`: View all banned users across threads (requires Threads role)
- `/posts debug permissions`: View all thread permission assignments (requires Threads role)

### Context Menus

- Message Context Menu:
  - **Message in Thread**: Delete, pin, or unpin the selected message within a thread
  - **Tags in Post**: Manage tags associated with a post (add or remove) through an interactive interface

- User Context Menu:
  - **User in Thread**: Ban, unban, share permissions, or revoke permissions for a specific user within a thread

## Configuration

Customize the module by adjusting the configuration variables and constants defined in `Posts.py`. Key configuration options include:

- `LOG_CHANNEL_ID`: ID of the channel where logs will be sent
- `LOG_FORUM_ID`: ID of the forum channel for logging purposes
- `POLL_FORUM_ID`: ID of the forum channel where polls are created
- `TAIWAN_ROLE_ID`: ID of the role to be assigned dynamic permissions
- `THREADS_ROLE_ID`: ID of the role required for debug commands
- `GUILD_ID`: ID of your Discord server
- `ROLE_CHANNEL_PERMISSIONS`: Defines roles and their associated channels for permission management
- `ALLOWED_CHANNELS`: Tuple of channel IDs where the module is active
- `FEATURED_CHANNELS`: Channels where featured posts are selected
- `message_count_threshold`: Minimum number of messages required for a post to be considered featured
- `rotation_interval`: Time interval for rotating featured posts
- `BANNED_USERS_FILE`: Path to the JSON file storing banned users
- `THREAD_PERMISSIONS_FILE`: Path to the JSON file storing thread permissions
- `POST_STATS_FILE`: Path to the JSON file storing post statistics
- `FEATURED_POSTS_FILE`: Path to the JSON file storing featured posts

The module automatically adjusts thresholds and rotation intervals based on server activity to maintain optimal engagement.
