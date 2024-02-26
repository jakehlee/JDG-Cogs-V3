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
Keep track of the Valorant esports scene and hold watch parties!

![vlr_ex1](https://github.com/jakehlee/JDG-Cogs-V3/assets/1744665/a885b36c-520d-4226-8810-eaa673e104fd)
![vlr_ex2](https://github.com/jakehlee/JDG-Cogs-V3/assets/1744665/5d245696-fd01-496c-8952-37d6273ecfd1)

| Command | Description |
| -- | -- |
| `[p]help vlr` | Display help message |
| `[p]vlr channel` | Set where the bot should notify matches and results (admin) |
| `[p]vlr leadtime` | Set how early a match notification should be sent in minutes (admin) |
| `[p]vlr matches all [n]` | Get n upcoming Valorant esports matches |
| `[p]vlr matches gc [n]` | Get n upcoming Game Changers matches |
| `[p]vlr matches vct [n]` | Get n upcoming VCT matches |
| `[p]vlr results all [n]` | Get n Valorant esports results |
| `[p]vlr results gc [n]` | Get n Game Changers results |
| `[p]vlr results vct [n]` | Get n VCT results |
| `[p]vlr sub team "team name"` | Subscribe to a Valorant team's matches |
| `[p]vlr sub event "event name"` | Subscribe to an event's matches |
| `[p]vlr update` | Force data pull from VLR. (admin) |
| `[p]vlr vc enable "default voice channel name"` | Enable automatic watch party voice chat creation. Members are moved back to the default VC after the watch party ends. Requires "Move Members" and "Manage Channels" permissions.|
| `[p]vlr vc disable` | Disable watch party voice chats. Deletes all VCs and stops creating more. Requires "Move Members" and "Manage Channels" permissions. |
| `[p]vlr vc force [https://vlr.gg/url]` | Forces a watch party to be created if the match was not notified. |