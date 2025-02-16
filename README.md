# Threads

The **Threads** module that provides advanced moderation features and supports all thread types.

Inspired by a bot from Discord server (ID: 1284782072484986993).

## Features

- Thread lifecycle management (creation, locking, archival)
- Content moderation with AI-powered analysis
- Granular permission and role-based access control
- Automated timestamp prefixing ([YYMMDDHHSS])
- Configurable poll generation (48h duration)
- Dynamic featured post rotation system
- Thread-specific user management
- Privacy-focused link sanitization
- Comprehensive activity logging and analytics
- Democratic timeout voting system
- AI-enhanced divination features

## Usage

### Slash Commands

- `/threads top` - Navigate to thread origin
- `/threads lock <reason>` - Lock current thread
- `/threads unlock <reason>` - Unlock current thread
- `/threads convert` - Convert channel names between Chinese variants
  - `source` - Source language variant:
    - Simplified Chinese (Mainland)
    - Traditional Chinese (Taiwan/HK/Mainland)
    - Japanese Shinjitai
  - `target` - Target language variant
  - `scope` - Conversion scope (All/Server/Roles/Channels)
- `/threads timeout`
  - `poll <user> <reason> <duration>` - Start timeout vote
  - `check <message>` - AI content analysis
  - `set <key>` - Configure GROQ API (Admin)
- `/threads list <type>` - View thread data
  - Banned users
  - Permission assignments
  - Post statistics
- `/threads view <type>` - View global configurations
  - Requires appropriate role
- `/threads debate` - Debate management
- `/threads divination`
  - `ball <wish> [ephemeral]` - Crystal ball consultation
  - `draw <target> [ephemeral]` - Fortune drawing
  - `tarot <cards> <query> [ephemeral]` - Tarot reading
  - `meaning <card> [ephemeral]` - Card interpretation
  - `rider <question> [ephemeral]` - AI-enhanced reading
- `/threads debug` (Admin only)
  - `config <file> <major> [minor] <value>` - Configure settings
  - `export <type>` - Export module files

### Context Menus

- Message Context Menu:
  - **Message in Thread**: Manage messages (delete, pin/unpin, AI check)
  - **Tags in Post**: Manage tags associated with a post

- User Context Menu:
  - **User in Thread**: Ban/unban users or manage their thread permissions

### Quick Replies (Reply-based)

- `del` - Delete message
- `pin` - Pin message
- `unpin` - Unpin message

## Configuration

### Core Settings

- Channel IDs
  - `LOG_CHANNEL_ID` - Logging channel
  - `LOG_FORUM_ID` - Log forum
  - `LOG_POST_ID` - Log post
  - `POLL_FORUM_ID` - Poll forums
  - `CONGRESS_ID` - Congress channel

- Role IDs
  - `TAIWAN_ROLE_ID` - Link transform exemption
  - `THREADS_ROLE_ID` - Debug command access
  - `CONGRESS_MEMBER_ROLE` - Congress members
  - `CONGRESS_MOD_ROLE` - Congress moderators

- Timeout Settings
  - `TIMEOUT_CHANNEL_IDS` - Voting channels
  - `TIMEOUT_REQUIRED_DIFFERENCE` - Vote threshold
  - `TIMEOUT_DURATION` - Default duration

### Data Files

- `banned_users.json` - Ban records
- `thread_permissions.json` - Permission data
- `post_stats.json` - Activity metrics
- `featured_posts.json` - Featured content
- `timeout_history.json` - Timeout logs
- `.groq_key` - AI API configuration
- `tarot.json` - Divination data
- `debate.json` - Debate tracking
- `/cards` - Tarot card assets

## Technical Implementation

### AI Moderation

- Message Analysis
  - 15-message context window
  - Role/permission awareness
  - Channel-specific rules

- Severity Scale (0-10)
  - 0-2: Normal discussion
  - 3-4: Mild concerns
  - 5-7: Direct violations
  - 8+: Automated actions

- Permission Controls
  - Message restrictions
  - File/reaction limits
  - Channel access
  - Mention privileges

### Featured Posts

- Dynamic Thresholds
  - Activity-based adjustment
  - Rotation intervals (12h-48h)
  - Tag automation

### Rate Limiting

- Per-User Limits
  - 10 requests/minute
  - 2000 tokens/minute
  - Progressive cooldowns

- Global Constraints
  - 30 requests/minute
  - 7000 tokens/minute
  - Daily caps

### Starboard

- Dynamic Thresholds
  - 5-15 star range
  - Activity/time/quality factors
  - Weighted scoring system

- Statistics
  - Rolling windows (24h/7d/4w)
  - Threshold history
  - Anti-abuse measures

## Acknowledgements

This module incorporates code and ideas from:

- [metabismuth/tarot-json](https://github.com/metabismuth/tarot-json.git) (MIT)
- [penut85420/FriesMeowDiscordBot](https://github.com/penut85420/FriesMeowDiscordBot.git)
- [ajzeigert/tarot.json](https://gist.github.com/32461d73c17cfd8fd475c0049db451f5.git)
