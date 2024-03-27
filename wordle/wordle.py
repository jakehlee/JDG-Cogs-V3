import asyncio
import re

import discord
from redbot.core import Config, checks, commands
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.menus import start_adding_reactions


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

        # Wordle verification regex
        self.w = re.compile(r"Wordle (\d{0,3},?\d{3}) (\d{1})\/6")

    def _parse_message(self, message):
        """Parse message string and check if it's a valid wordle result"""

        # Possible characters in wordle emoji grid
        wordle_charset = {'\N{BLACK LARGE SQUARE}', \
                          '\N{WHITE LARGE SQUARE}', \
                          '\N{LARGE GREEN SQUARE}', \
                          '\N{LARGE YELLOW SQUARE}',\
                          '\N{LARGE ORANGE SQUARE}', \
                          '\N{LARGE BLUE SQUARE}'}

        # Split into lines
        lines = message.clean_content.split('\n')

        # Early exit for messages with less than 3 lines
        if len(lines) < 3:
            return None

        # Parse first line 
        match = self.w.match(lines[0])
        if match is not None:
            gameid = int(match.groups()[0].replace(",", ""))
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
        prev = await self.config.member(author).all()

        # Avoid duplicates
        async with self.config.member(author).gameids() as gameids:
            if gameid in gameids:
                return
            else:
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
    async def wordlestats(self, ctx: commands.Context, member: discord.Member):
        """Retrieve Wordle Statistics for a single user

        Statistics to be returned:
        - Solve count histogram (freq 1~6)
        - Total score (inverted score)
        - Current streak (days)
        """

        memberstats = await self.config.member(member).all()

        totalgames = len(memberstats['gameids'])

        # Build embed
        channelid = await self.config.guild(ctx.guild).channelid()
        refchannel = ctx.guild.get_channel(channelid).mention if channelid is not None else "N/A"
        embed = discord.Embed(
            title=f"{member.display_name}'s Wordle Statistics",
            description=f"Pulled from messages in {refchannel}",
            color=await self.bot.get_embed_color(ctx)
        )

        if totalgames == 0:
            # No games found
            embed.add_field(name="Error", value=f"No games found for {member.display_name}")
            await ctx.send(embed=embed, allowed_mentions=None)
            return

        # Calculate values for histogram
        percs = [int((x/totalgames)*100) for x in memberstats['qty']]
        histmax = max(memberstats['qty'])
        histlens = [int((x/histmax)*10) for x in memberstats['qty']]
        histbars = ['\N{LARGE GREEN SQUARE}'*h for h in histlens]

        # Build histogram
        histogram = ""
        histogram += f"{totalgames} recorded games\n"
        histogram += f"1\N{COMBINING ENCLOSING KEYCAP} {histbars[0]} {memberstats['qty'][0]} ({percs[0]}%)\n"
        histogram += f"2\N{COMBINING ENCLOSING KEYCAP} {histbars[1]} {memberstats['qty'][1]} ({percs[1]}%)\n"
        histogram += f"3\N{COMBINING ENCLOSING KEYCAP} {histbars[2]} {memberstats['qty'][2]} ({percs[2]}%)\n"
        histogram += f"4\N{COMBINING ENCLOSING KEYCAP} {histbars[3]} {memberstats['qty'][3]} ({percs[3]}%)\n"
        histogram += f"5\N{COMBINING ENCLOSING KEYCAP} {histbars[4]} {memberstats['qty'][4]} ({percs[4]}%)\n"
        histogram += f"6\N{COMBINING ENCLOSING KEYCAP} {histbars[5]} {memberstats['qty'][5]} ({percs[5]}%)\n"

        embed.add_field(name="Histogram", value=histogram)
        embed.add_field(name="Total Score", value=memberstats['total_score'], inline=False)
        embed.add_field(name="Current Streak", value=memberstats['curr_streak'], inline=True)

        await ctx.send(embed=embed, allowed_mentions=None)

    @commands.command()
    async def wordletop(self, ctx: commands.Context):
        """Show the Wordle top-5 leaderboard for total points and average attempts per solve."""

        # Get scores and sort them 
        memberstats = await self.config.all_members(guild=ctx.guild)
        members = memberstats.keys()

        # Total scores (higher=better)
        scores = [{'member': m, 'total_score': memberstats[m]['total_score'], 'n_games': len(memberstats[m]['gameids'])} for m in members]
        scores = sorted(scores, key=lambda d: d['total_score'], reverse=True)

        # Average attempts (lower=better)
        avg_attempts = [{'member': m, 'avg_attempt': sum([q*s for q, s in zip(memberstats[m]['qty'], [1,2,3,4,5,6])]) / len(memberstats[m]['gameids'])} for m in members]
        avg_attempts = sorted(avg_attempts, key=lambda d: d['avg_attempt'])

        # Build total score leaderboard
        prefixes = [f"\N{FIRST PLACE MEDAL}", f"\N{SECOND PLACE MEDAL}", f"\N{THIRD PLACE MEDAL}", "4.", "5."]
        leaderboard = ""
        if len(members) == 0:
            leaderboard = "No members found."
        else:
            for i in range(min(5, len(members))):
                this_member = ctx.guild.get_member(scores[i]['member'])
                if this_member is None:
                    # Member left the server
                    leaderboard += f"{prefixes[i]} <unknown> ({scores[i]['total_score']} points, {scores[i]['n_games']} solves)\n"
                else:
                    leaderboard += f"{prefixes[i]} {this_member.mention} ({scores[i]['total_score']} points, {scores[i]['n_games']} solves)\n"
        leaderboard = leaderboard.rstrip()

        # Build avg attempt leaderboard
        avgboard = ""
        if len(members) == 0:
            avgboard = "No members found."
        else:
            for i in range(min(5, len(members))):
                this_member = ctx.guild.get_member(avg_attempts[i]['member'])
                if this_member is None:
                    # Member left the server
                    avgboard += f"{prefixes[i]} <unknown> ({avg_attempts[i]['avg_attempt']:.2f} per solve)\n"
                else:
                    avgboard += f"{prefixes[i]} {this_member.mention} ({avg_attempts[i]['avg_attempt']:.2f} per solve)\n"
        avgboard = avgboard.rstrip()

        # Build embed
        channelid = await self.config.guild(ctx.guild).channelid()
        refchannel = ctx.guild.get_channel(channelid).mention if channelid is not None else "N/A"
        embed = discord.Embed(
            title=f"{ctx.guild.name} Wordle Leaderboard",
            description=f"Share your results in {refchannel}",
            color=await self.bot.get_embed_color(ctx)
        )
        embed.add_field(name="Total Points", value=leaderboard)
        embed.add_field(name="Average Attempts", value=avgboard, inline=True)

        embed.add_field(name="Point Values", value="1 attempt: 10 pts\n2 attempts: 5 pts\n3 attempts: 4 pts\n4 attempts: 3 pts\n5 attempts: 2 pts\n6 attempts: 1 pt", inline=False)

        await ctx.send(embed=embed, allowed_mentions=None)


    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def wordlechannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
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
    async def wordlereparse(self, ctx: commands.Context, history_limit: int = 1000):
        """Reparse wordle results from channel history. Number specifies message limit.
        This might take a while for large channels.
        """

        # Make sure a wordle channel is set first.
        channelid = await self.config.guild(ctx.guild).channelid()
        if channelid is None:
            await ctx.send("Set a wordle channel with !setwordlechannel first!")
            return

        #Reaction poll
        msg = await ctx.send(f"Reparse {history_limit} msgs in {ctx.guild.get_channel(channelid).mention}?")
        start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)

        pred = ReactionPredicate.yes_or_no(msg, ctx.author)
        await ctx.bot.wait_for("reaction_add", check=pred)
        if pred.result is True:
            await ctx.send("Starting reparse.")
            # Clear existing data
            await self.config.clear_all_members(guild=ctx.guild)

            # Go through message history and reload results
            channel = ctx.guild.get_channel(channelid)
            async for message in channel.history(limit=history_limit, oldest_first=True):
                gameinfo = self._parse_message(message)

                if gameinfo is not None:
                    await self._add_result(message.guild, message.author, gameinfo[0], gameinfo[1])
            
            await ctx.send(f"Wordle results successfully loaded.")
        else:
            await ctx.send("Nevermind then.")
            return

    @commands.Cog.listener()
    async def on_message_without_command(self, message: discord.Message):
        """Listen to users posting their wordle results and add them to stats"""
        # Don't listen to messages from bots
        if message.author.bot: return

        # Don't listen to DMs
        if message.guild is None: return

        # Only listen to messages from set channel
        if message.channel.id != await self.config.guild(message.guild).channelid(): return

        # Check if valid message
        gameinfo = self._parse_message(message)
        if gameinfo is not None:
            # Add result
            await self._add_result(message.guild, message.author, gameinfo[0], gameinfo[1])

            # Notify user
            if gameinfo[1] == 1:
                await message.channel.send(
                    f"Fantastic solve, {message.author.mention}!!! Updated stats."
                )
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

