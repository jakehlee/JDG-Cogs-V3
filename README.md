# JDG-Cogs-V3
Cogs for Red-DiscordBot V3 by JDG
Contact on Discord: JDG#1270 

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

### VLR
Keep track of the Valorant esports scene!

| Command | Description |
| -- | -- |
| `[p]help VLR` | Display help message |
| `[p]vlrchannel` | Set where the bot should notify matches and results (admin) |
| `[p]vlrleadtime` | Set how early a match notification should be sent in minutes (admin) |
| `[p]vlrmatches [n]` | Get n upcoming Valorant esports matches |
| `[p]vlrmatchesgc [n]` | Get n upcoming Game Changers matches |
| `[p]vlrmatchesvct [n]` | Get n upcoming VCT matches |
| `[p]vlresults [n]` | Get n Valorant esports results |
| `[p]vlrresultsgc [n]` | Get n Game Changers results |
| `[p]vlrresultsvct [n]` | Get n VCT results |
| `[p]vlrsubteam "team name"` | Subscribe to a Valorant team's matches |
| `[p]vlrsubevent "event name"` | Subscribe to an event's matches |
| `[p]vlrupdate` | Force data pull from VLR. Does not trigger notifications (admin) |
