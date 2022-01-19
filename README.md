# JDG-Cogs-V3
Cogs for Red-DiscordBot V3 by JDG

## Installation
```
[p]repo add JDG https://github.com/jakehlee/JDG-Cogs-V3
[p]cog install JDG [cog]
[p]load [cog]
```

## Cogs

### Wordle
Share your wordle results and track your stats!

![wordle_ex2](https://user-images.githubusercontent.com/1744665/150098234-15f95e13-9c8c-40a0-a3a8-f6d1772a86ca.PNG)

| Command | Description |
| -- | -- |
| `[p]help Wordle` |  Display help message |
| `[p]wordlechannel` | Set where the bot should parse Wordle results (admin) |
| `[p]wordlereparse [limit]` | Reparse Wordle results from channel history up to `limit` messages (default 1000) (admin)|
| `[p]wordlestats @user` | Get Wordle statistics for a user |
| `[p]wordletop` | Get Wordle leaderboards for the server |

**Known Issues and Planned Features:**

- Daily reminder feature (#2)
- Reparse wordle double confirmation (#6)
