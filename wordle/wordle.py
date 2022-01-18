import asyncio
import re

import discord
from redbot.core import Config, checks, commands

class Wordle(commands.Cog):
    """Wordle cog to track statistics and streaks"""

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=13330085047676266, force_registration=True)

        default_guild = {'channelid': None}
        self.config.register_guild(**default_guild)

        default_member = {
            'gameids': [],
            'total_score': 0,
            'last_gameid': 0,
            'curr_streak': 0,
            'qty': [0, 0, 0, 0, 0, 0]
        }

        self.config.register_member(**default_member)
    
    def _parse_message(self, message):
        """Parse message string and check if it's a valid wordle result"""

        # Possible characters in wordle emoji grid
        wordle_charset = {'⬛', '⬜', '🟩', '🟨'}

        # Split into lines
        lines = message.clean_content.split('\n')

        # Early exit for messages with less than 3 lines
        if len(lines) < 3:
            return None

        # Parse first line 
        match = re.match(r"Wordle (\d{3,}) (\d{1})\/6", lines[0])
        if match is not None:
            gameid = int(match.groups()[0])
            attempts = int(match.groups()[1])

            # Early exit if attempts don't make sense
            if attempts > 6:
                return None

            # Early exit for messages without requisite emoji rows
            if len(lines) < attempts+2:
                return None
            
            # Integrity check of emoji grid
            for i in range(2, attempts+2):
                if not set(lines[i]) <= wordle_charset:
                    return None

            # Passed, return game info
            return gameid, attempts
        else:
            return None


    async def _add_result(self, guild, author, gameid, attempts):
        """Add a user's wordle result to their record"""

        # Get previous stats
        prev = await self.config.member(author).get_raw()

        # Avoid duplicates
        if gameid in prev['gameids']:
            return
        else:
            async with self.config.member(author).gameids() as gameids:
                gameids.append(gameid)

        # Update score
        if attempts == 1:
            # First guess gets 10 points
            add_score = 10
        else:
            # Second guess gets 5, third guess gets 4, etc.
            add_score = 7 - attempts
        await self.config.member(author).total_score.set(prev['total_score'] + add_score)

        if gameid - prev['last_gameid'] == 1:
            await self.config.member(author).last_gameid.set(gameid)
            await self.config.member(author).curr_streak.set(prev['curr_streak']+1)
        else:
            await self.config.member(author).last_gameid.set(gameid)
            await self.config.member(author).curr_streak.set(1)

        # Update qty
        newhist = prev['qty'].copy()
        newhist[attempts-1] += 1
        await self.config.member(author).set_raw('qty', value=newhist)

    @commands.command()
    async def wordlehelp(self, ctx: commands.Context):
        """Print help message for wordle"""

        embed = discord.Embed(
            title="Wordle Help",
            description="How to use the Wordle Cog",
            color=await self.bot.get_embed_color(ctx)
        )

        embed.add_field(name="Commands", value= \
"""
`!wordlehelp`: Display this help message
`!setwordlechannel`: Set where Wordle results are posted (admin)
`!reparsewordle`: Reparse Wordle results from history (admin)
`!wordlestats @user`: Get Wordle statistics for the user
""")

        embed.add_field(name="Current Wordle Channel", value=f"{self.bot.get_channel(await self.config.guild(ctx.guild).channelid()).mention}")

        await ctx.send(embed=embed)


    @commands.command()
    async def wordlestats(self, ctx: commands.Context, member: discord.Member):
        """Retrieve Wordle Statistics for a single user

        Statistics to be returned:
        - Solve count histogram (freq 1~6)
        - Total score (inverted score)
        - Current streak (days)
        """

        memberstats = await self.config.member(member).get_raw()

        totalgames = len(memberstats['gameids'])
        
        percs = [int((x/totalgames)*100) for x in memberstats['qty']]
        histmax = max(memberstats['qty'])
        histlens = [int((x/histmax)*10) for x in memberstats['qty']]

        histogram = ""
        histogram += f"Histogram for {totalgames} recorded games:\n"
        histogram += f"1️⃣: {'🟩'*histlens[0]} ({percs[0]}%)\n"
        histogram += f"2️⃣: {'🟩'*histlens[1]} ({percs[1]}%)\n"
        histogram += f"3️⃣: {'🟩'*histlens[2]} ({percs[2]}%)\n"
        histogram += f"4️⃣: {'🟩'*histlens[3]} ({percs[3]}%)\n"
        histogram += f"5️⃣: {'🟩'*histlens[4]} ({percs[4]}%)\n"
        histogram += f"6️⃣: {'🟩'*histlens[5]} ({percs[5]}%)\n"

        embed = discord.Embed(
            title=f"{member.display_name}'s Wordle Statistics",
            description=f"Statistics pulled from messages in {self.bot.get_channel(await self.config.guild(ctx.guild).channelid()).mention}",
            color=await self.bot.get_embed_color(ctx)
        )
        embed.add_field(name="Histogram", value=histogram)
        embed.add_field(name='\u200B', value='\u200B')
        embed.add_field(name='\u200B', value='\u200B')
        embed.add_field(name="Total Score", value=memberstats['total_score'], inline=True)
        embed.add_field(name="Current Streak", value=memberstats['curr_streak'], inline=True)

        await ctx.send(embed=embed)

    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def setwordlechannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set channel where users post wordle scores.
        Not passing a channel stops the bot from parsing any channel.
        """
        if channel is not None:
            await self.config.guild(ctx.guild).channelid.set(channel.id)
            await ctx.send(f"Wordle channel has been set to {channel.mention}")
        else:
            await self.config.guild(ctx.guild).channelid.set(None)
            await ctx.send("Wordle channel has been cleared")

    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def reparsewordle(self, ctx: commands.Context):
        """Reparse wordle results from channel history
        This might take a while for large channels.
        """

        # Make sure a wordle channel is set first.
        if self.config.guild(ctx.guild).channelid() is None:
            ctx.send("Set a wordle channel with !setwordlechannel first!")
            return
        
        # Clear existing data
        # TODO: Emoji menu double check first
        await self.config.clear_all_members(guild=ctx.guild)

        # Go through message history and reload results
        # TODO: We might want a history length limit with the channel.history limit kwarg
        channelid = await self.config.guild(ctx.guild).channelid()
        channel = self.bot.get_channel(channelid)
        async for message in channel.history(limit=1000, oldest_first=True):
            gameinfo = self._parse_message(message)

            if gameinfo is not None:
                await self._add_result(message.guild, message.author, gameinfo[0], gameinfo[1])
        
        await ctx.send("All wordle results from channel history loaded.")

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        """Listen to users posting their wordle results and add them to stats"""
        # Don't listen to messages from bots
        if message.author.bot:
            return
        
        # Only listen to messages from set channel
        if message.channel.id != await self.config.guild(message.guild).channelid():
            return

        # Check if valid message
        gameinfo = self._parse_message(message)
        if gameinfo is not None:
            # Add result
            await self._add_result(message.guild, message.author, gameinfo[0], gameinfo[1])
            
            # Notify user
            if gameinfo[1] <= 3:
                await message.channel.send(
                    f"Great solve, {message.author.mention}! Updated stats."
                )
            elif gameinfo[1] < 6:
                await message.channel.send(
                    f"Nice solve, {message.author.mention}. Updated stats."
                )
            elif gameinfo[1] == 6:
                await message.channel.send(
                    f"Close call, {message.author.mention}. Updated stats."
                )

