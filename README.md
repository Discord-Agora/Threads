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
- AI-powered content moderation
- Timeout voting system for temporary user restrictions

## Usage

### Slash Commands

- `/threads top`: Navigate to the top of the current thread
- `/threads lock`: Lock the current thread
  - Options: `reason` (string, required) - Reason for locking the thread
- `/threads unlock`: Unlock the current thread
  - Options: `reason` (string, required) - Reason for unlocking the thread
- `/threads convert`: Convert channel names between different Chinese variants
  - Options:
    - `source` (choice, required) - Source language variant
    - `target` (choice, required) - Target language variant
    - `scope` (choice, required) - What to convert (all/server/roles/channels)
- `/threads timeout`: Timeout management commands
  - `/threads timeout poll`: Start a timeout poll for a user
    - Options:
      - `user` (user, required) - The user to timeout
      - `reason` (string, required) - Reason for timeout
      - `duration` (integer, required) - Timeout duration in minutes (1-10)
  - `/threads timeout check`: Check message content with AI
    - Options: `message` (string, required) - ID or URL of the message to check
  - `/threads timeout set`: Set bot configurations (Admin only)
    - Options: `key` (string, required) - Set the GROQ API key
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
- `/threads debug`: Debug commands (Admin only)
  - `/threads debug export`: Export files from the extension directory
    - Options: `type` (choice, required) - Type of files to export

### Context Menus

- Message Context Menu:
  - **Message in Thread**: Manage messages (delete, pin/unpin, AI check)
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
- `LOG_POST_ID`: ID of the post where logs will be sent
- `POLL_FORUM_ID`: ID of the forum channel where polls are created
- `TAIWAN_ROLE_ID`: ID of the role exempt from link transformation
- `THREADS_ROLE_ID`: ID of the role required for debug commands
- `GUILD_ID`: ID of your Discord server
- `CONGRESS_ID`: ID of the congress channel
- `CONGRESS_MEMBER_ROLE`: ID of the congress member role
- `CONGRESS_MOD_ROLE`: ID of the congress moderator role
- `ROLE_CHANNEL_PERMISSIONS`: Defines roles and their associated channels for permission management
- `ALLOWED_CHANNELS`: Tuple of channel IDs where the module is active
- `FEATURED_CHANNELS`: Channels where featured posts are selected
- `TIMEOUT_CHANNEL_IDS`: Channels where timeout voting is allowed
- `TIMEOUT_REQUIRED_DIFFERENCE`: Required vote difference for timeout action
- `TIMEOUT_DURATION`: Default timeout duration in minutes

### Files

The module uses several JSON files to store data:

- `banned_users.json`: Stores banned user information
- `thread_permissions.json`: Stores thread permission assignments
- `post_stats.json`: Stores post activity statistics
- `featured_posts.json`: Stores featured post information
- `timeout_history.json`: Stores timeout history for users
- `.groq_key`: Stores the GROQ API key for AI moderation

### AI Moderation

The module uses GROQ's AI API for content moderation. Messages are scored on a scale of 0-10:

- 0-3: No abuse or very mild negative language
- 4-6: Moderate negativity but not direct harassment
- 7-8: Clear harassment or hostile personal attacks
- 9-10: Severe harassment, threats, or extreme personal attacks

Scores of 9 or higher will result in automatic timeout actions.

### Algorithms

1. Featured Posts Threshold
   - Calculates average message count across all posts
   - Adjusts threshold based on server activity:
     - Reduces threshold by 50% if activity stays below minimum for 7 days
     - Minimum threshold is maintained at 10 messages
   - Dynamically adjusts based on recent activity:
     - High activity (>100 messages/day): 12 hours rotation interval
     - Low activity (<10 messages/day): 48 hours rotation interval
     - Normal activity: 24 hours rotation interval
   - Prevents excessive rotations during low activity periods
   - Uses exponential moving average to smooth out activity spikes
   - Considers thread age and engagement patterns
   - Factors in reactions and replies separately from messages

2. Base Duration Calculation
   - `base_duration`: Starting timeout duration (default: 300s)
   - `multiplier`: Increases with each violation (1.2-2.0)
   - `violation_count`: Number of previous violations
   - Adjusts parameters based on:
     - Server activity level (busier servers get longer timeouts)
     - Violation rate (frequent violations increase duration)
     - Time since last violation (decay factor)
     - Severity of current violation (AI score impact)
   - Decay period reduces violation count over time:
     - 30 days for minor violations
     - 90 days for severe violations
   - Maximum duration cap of 28 days
   - Minimum duration floor of 5 minutes

3. Rate Limiting AI Moderation
   - Per-user limits:
     - 10 requests per minute
     - 2000 tokens per minute
     - Cooldown period: 5 minutes after hitting limit
   - Global limits:
     - 30 requests per minute
     - 7000 tokens per minute
     - 7000 requests per day
     - 500000 tokens per day
   - Priority queue system:
     - Higher priority for moderator requests
     - Emergency override for critical situations
   - Automatic backoff when approaching limits
   - Token usage optimization:
     - Message batching when possible
     - Content truncation for long messages
     - Caching of recent similar checks
