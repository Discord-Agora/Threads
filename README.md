# Threads

The **Threads** module is designed to manage thread-based conversations, user interactions, and content moderation within a Discord server. It supports all thread types.

Inspired by a bot from Discord server (ID: 1284782072484986993).

## Features

- Lock/unlock threads to control interactions
- Delete inappropriate or unwanted messages
- Manage thread tags and permissions
- Automatic timestamp prefixing for new posts
- Automatic poll creation for new posts (configurable)
- Featured posts system with dynamic rotation
- User ban/unban system for threads
- Permission management for specific users
- Link sanitization for privacy protection
- Comprehensive logging system
- Message count tracking
- AI-powered content moderation
- Timeout voting system

## Usage

### Slash Commands

- `/threads top`: Navigate to the top of the current thread

- `/threads lock`: Lock the current thread
  - `reason` (string, required): Reason for locking the thread

- `/threads unlock`: Unlock the current thread
  - `reason` (string, required): Reason for unlocking the thread

- `/threads convert`: Convert channel names between different Chinese variants
  - `source` (choice, required): Source language variant
    - Simplified Chinese (Mainland China)
    - Traditional Chinese (Taiwan)
    - Traditional Chinese (Hong Kong)
    - Traditional Chinese (Mainland China)
    - Japanese Shinjitai
  - `target` (choice, required): Target language variant (same choices as source)
  - `scope` (choice, required): What to convert
    - All
    - Server Name Only
    - Roles Only
    - Channels Only

- `/threads timeout`: Timeout management commands
  - `/threads timeout poll`: Start a timeout poll for a user
    - `user` (user, required): The user to timeout
    - `reason` (string, required): Reason for timeout
    - `duration` (integer, required): Timeout duration in minutes (1-10)
  - `/threads timeout check`: Check message content with AI
    - `message` (string, required): ID or URL of the message to check
  - `/threads timeout set`: Set bot configurations (ADMIN only)
    - `key` (string, required): Set the GROQ API key

- `/threads list`: List information for current thread
  - `type` (choice, required): Select data type to view
    - `Banned Users`: View banned users in current thread
    - `Thread Permissions`: View users with special permissions in current thread
    - `Post Statistics`: View statistics for current post

- `/threads view`: View configuration data (requires role)
  - `type` (choice, required): Select data type to view
    - `Banned Users`: View all banned users across threads
    - `Thread Permissions`: View all thread permission assignments
    - `Post Statistics`: View post activity statistics
    - `Featured Threads`: View featured threads and their metrics

- `/threads debug`: Debug commands (ADMIN only)
  - `/threads debug export`: Export files from the extension directory
    - `type` (choice, required): Type of files to export

### Context Menus

- Message Context Menu:
  - **Message in Thread**: Manage messages (delete, pin/unpin, AI check)
  - **Tags in Post**: Manage tags associated with a post

- User Context Menu:
  - **User in Thread**: Ban/unban users or manage their thread permissions

### Quick Replies

Messages can be managed by replying with these commands:

- `del`: Delete the replied message
- `pin`: Pin the replied message
- `unpin`: Unpin the replied message

## Configuration

Key configuration options include:

- `LOG_CHANNEL_ID`: ID of the log channel
- `LOG_FORUM_ID`: ID of the log forum
- `LOG_POST_ID`: ID of the log post
- `POLL_FORUM_ID`: ID(s) of forums where polls are created
- `TAIWAN_ROLE_ID`: ID of role exempt from link transformation
- `THREADS_ROLE_ID`: ID of role required for debug commands
- `GUILD_ID`: ID of your Discord server
- `CONGRESS_ID`: ID of the congress channel
- `CONGRESS_MEMBER_ROLE`: ID of congress member role
- `CONGRESS_MOD_ROLE`: ID of congress moderator role
- `ROLE_CHANNEL_PERMISSIONS`: Role-channel permission mappings
- `ALLOWED_CHANNELS`: Channels where the module is active
- `FEATURED_CHANNELS`: Channels eligible for featured posts
- `TIMEOUT_CHANNEL_IDS`: Channels where timeout voting is allowed
- `TIMEOUT_REQUIRED_DIFFERENCE`: Required vote difference for timeout
- `TIMEOUT_DURATION`: Default timeout duration in minutes

### Files

The module uses several JSON files for data storage:

- `banned_users.json`: Banned user records
- `thread_permissions.json`: Thread permission assignments
- `post_stats.json`: Post activity statistics
- `featured_posts.json`: Featured post tracking
- `timeout_history.json`: User timeout history
- `.groq_key`: GROQ API key for AI moderation

### Algorithm

1. AI Moderation
   - Message Analysis:
     - Evaluates message content and up to 15 messages of context
     - Considers user roles, permissions, and interaction history
     - Checks channel type and permission settings
   - Severity Scoring (0-10):
     - 0-2: Normal discussion, constructive criticism, casual conversation
     - 3-4: Mild hostility, borderline content, heated but non-personal arguments
     - 5-7: Direct hostility, targeted behavior, inappropriate content
     - 8: Triggers warning, logs violation, notifies user
     - 9-10: Automatic timeout with multipliers (2x for 9, 3x for 10)
   - Permission Restrictions on Violations:
     - Message sending (regular and thread)
     - File attachments and reactions
     - Channel/thread management
     - Mention privileges
     - Forum post creation
   - System Constraints:
     - Excludes bot messages
     - 24-hour message age limit
     - Exempts thread owners and managers
     - Prevents duplicate checks

2. Featured Posts
   - Threshold:
     - Calculates average post message count
     - Reduces threshold 50% if activity < 50 msgs for 7 days
     - Maintains 10 message minimum
     - Weekly threshold updates
   - Rotation:
     - High activity (>100 msgs/day): 12h rotation
     - Low activity (<10 msgs/day): 48h rotation
     - Normal activity: 24h rotation
   - Criteria:
     - Message count vs threshold
     - Activity within rotation window
     - Post status (not archived/locked)

3. Timeout
   - Base Duration:
     - Initial: 300 seconds
     - Multiplier: 1.2-2.0x
   - Adjustments:
     - Activity-based: ±20%
     - Violation rate: +50%/-25% multiplier
     - AI severity: 2-3x multiplier
     - Progressive penalties with 24-48h decay
     - Global triggers after 3+ violations
     - Hard cap at 1 hour

4. Rate Limiting
   - Per-User Limits:
     - 10 requests/minute
     - 2000 tokens/minute
     - 5-minute breach cooldown
     - 1-hour cache expiry
   - Global Constraints:
     - 30 requests/minute
     - 7000 tokens/minute
     - Daily: 7000 requests, 500000 tokens
   - Resource Distribution:
     - 70% user checks
     - 20% auto-moderation
     - 10% maintenance
   - Model Hierarchy:
     - Primary: `llama-3.2-90b-vision-preview`
     - Secondary: `llama-3.2-11b-vision-preview`
     - Fallback: `llama-3.1-70b-versatile`
