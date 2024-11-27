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
  - Options: `reason` (string, required) - Reason for locking the thread
- `/threads unlock`: Unlock the current thread
  - Options: `reason` (string, required) - Reason for unlocking the thread
- `/threads convert`: Convert channel names between different Chinese variants
  - Options:
    - `source` (choice, required) - Source language variant:
      - Simplified Chinese (Mainland China)
      - Traditional Chinese (Taiwan)
      - Traditional Chinese (Hong Kong)
      - Traditional Chinese (Mainland China)
      - Japanese Shinjitai
    - `target` (choice, required) - Target language variant (same options as source)
    - `scope` (choice, required) - What to convert:
      - All
      - Server Name Only
      - Roles Only
      - Channels Only
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

### AI Moderation

The module uses GROQ's API for content moderation. Messages are scored on a scale of 0-10:

- 0-2: Acceptable content
  - Normal discussion and debate
  - Constructive criticism
  - Casual conversation

- 3-4: Minor concerns
  - Mild rudeness
  - Borderline inappropriate content
  - Heated but non-personal arguments

- 5-6: Moderate concerns
  - Direct hostility
  - Pattern of targeting
  - Inappropriate content

- 7-8: Serious concerns
  - Sustained harassment
  - Hate speech
  - Sexual harassment
  - Privacy violations

- 9-10: Critical violations
  - Explicit threats
  - Extreme hate speech
  - Encouraging self-harm
  - Doxxing
  - Predatory behavior

Scores of 9 or higher trigger automatic timeout actions.

### Algorithms

1. Featured Posts
   - Adjusts thresholds based on server activity:
     - Reduces threshold by 50% if activity stays below minimum for 7 days
     - Minimum threshold is maintained at 10 messages
   - Dynamic rotation intervals:
     - High activity (>100 messages/day): 12 hours
     - Low activity (<10 messages/day): 48 hours
     - Normal activity: 24 hours
   - Selection criteria:
     - Message count weight: 40%
     - Recent activity weight: 30%
     - User engagement weight: 20%
     - Age factor weight: 10%

2. Timeout Duration
   - Base duration calculation:
     - Initial duration: 300 seconds
     - Multiplier range: 1.2 to 2.0
   - Dynamic adjustments:
     - Server activity factor:
       - High activity: +20% duration
       - Low activity: -20% duration
     - Violation rate impact:
       - High rate (>5/hour): +50% multiplier
       - Low rate (<1/day): -25% multiplier
     - Time decay:
       - 30 days for minor violations
       - 90 days for severe violations
       - Exponential decay function
   - AI severity integration:
     - Score 9: 2x multiplier
     - Score 10: 3x multiplier
   - Limits and constraints:
     - Minimum duration: 5 minutes
     - Maximum duration: 28 days
     - Cooldown periods between timeouts

3. Rate Limiting
   - Per-user limits:
     - Request quotas:
       - 10 requests per minute
       - 2000 tokens per minute
     - Cooldown:
       - 5 minutes after limit breach
       - Progressive backoff
   - Global limits:
     - Rate constraints:
       - 30 requests per minute
       - 7000 tokens per minute
       - 7000 requests per day
       - 500000 tokens per day
     - Distribution:
       - 70% for user requests
       - 20% for moderation
       - 10% for system tasks
