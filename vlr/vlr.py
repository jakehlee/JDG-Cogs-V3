import asyncio
import re
from datetime import datetime, timezone

import requests
from bs4 import BeautifulSoup

import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.menus import start_adding_reactions

def str_to_min(time_str):
    """ parsing match time info """
    total_minutes = 0
    
    if time_str is None:
        return total_minutes

    parts = time_str.split()
    for part in parts:
        if 'd' in part:
            days = int(part.replace('d', ''))
            total_minutes += days * 24 * 60  # Convert days to minutes
        elif 'h' in part:
            hours = int(part.replace('h', ''))
            total_minutes += hours * 60  # Convert hours to minutes
        elif 'm' in part:
            minutes = int(part.replace('m', ''))
            total_minutes += minutes
            
    return total_minutes

def get_flag_unicode(flag_str):
    country_code = flag_str.split('-')[-1].upper()
    flag_unicode = ''.join(chr(ord(letter) + 127397) for letter in country_code)
    
    return flag_unicode

class VLR(commands.Cog):
    """VLR cog to track valorant esports matches and teams"""

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=611188252769002, force_registration=True)

        self.POLLING_RATE = 300 # global polling rate in seconds
        self.BASE_URL = "https://www.vlr.gg"

        default_global = {
            'match_cache': [],
            'result_cache': [],
            'cache_time': None
        }
        self.config.register_global(**default_global)

        default_guild = {
            'channel_id': None,
            'sub_event': ['Game Changers', 'Champions Tour'],
            'sub_team': [],
            "notify_lead": 15
        }
        self.config.register_guild(**default_guild)

        self.parse.start()
        self.parse.change_interval(seconds=self.POLLING_RATE)
    
    def cog_unload(self):
        self.parse.cancel()


    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def vlrchannel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set VLR channel for match notifications.
        
        Example: [p]vlrchannel #valorant
        """

        if channel is not None:
            await self.config.guild(ctx.guild).channel_id.set(channel.id)
            await ctx.send(f"VLR channel has been set to {channel.mention}")
        else:
            await self.config.guild(ctx.guild).channel_id.set(None)
            await ctx.send("VLR channel has been cleared")
    
    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def vlrleadtime(self, ctx: commands.Context, minutes: int):
        """Set how early match notifications should be sent in minutes.

        Example: [p]vlrleadtime 15
        """

        await self.config.guild(ctx.guild).notify_lead.set(minutes)
        ctx.send(f"Match notifications will be sent {minutes} mins before.")

    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def vlrsubevent(self, ctx: commands.Context, event: str):
        """Subscribe or Unsubscribe from an event.

        Notifications will be sent if this substring exists in the event string.
        If there is a space in the event name, wrap it in quotes.
        Example: [p]vlrsubevent "Game Changers"
        """

        sub_event = await self.config.guild(ctx.guild).sub_event()

        if event in sub_event:
            msg = await ctx.send(f"Already subscribed to event \"{event}\", unsubscribe?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            await ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result is True:
                sub_event.remove(event)
                await self.config.guild(ctx.guild).sub_event.set(sub_event)
                await ctx.send(f"Unsubscribed from event. Remaining: {sub_event}")
            else:
                await ctx.send(f"Event subscriptions unchanged: {sub_event}")
        else:
            msg = await ctx.send(f"Subscribe to event \"{event}\"?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            await ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result is True:
                sub_event.append(event)
                await self.config.guild(ctx.guild).sub_event.set(sub_event)
                await ctx.send(f"Event subscription added: {sub_event}")
            else:
                await ctx.send(f"Event subscriptions unchanged: {sub_event}")

    
    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def vlrsubteam(self, ctx: commands.Context, team: str):
        """Subscribe or Unsubscribe from a team.

        Notifications will be sent if a team name matches this string exactly.
        If there is a space in the team name, wrap it in quotes. If the team name has special characters, it must be included.
        Example: [p]vlrsubevent "Sentinels"
        """

        sub_team = await self.config.guild(ctx.guild).sub_team()

        if team in sub_team:
            msg = await ctx.send(f"Already subscribed to team \"{team}\", unsubscribe?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            await ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result is True:
                sub_team.remove(team)
                self.config.guild(ctx.guild).sub_team.set(sub_event)
                await ctx.send(f"Unsubscribed from team. Remaining: {sub_team}")
            else:
                await ctx.send(f"Team subscriptions unchanged: {sub_team}")
        else:
            msg = await ctx.send(f"Subscribe to team \"{event}\"?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            await ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result is True:
                sub_team.append(team)
                self.config.guild(ctx.guild).sub_team.set(sub_team)
                await ctx.send(f"Team subscription added: {sub_team}")
            else:
                await ctx.send(f"Team subscriptions unchanged: {sub_team}")

    async def _getmatches(self):
        """Parse matches from vlr"""

        # Get HTML response for upcoming matches
        url = "https://www.vlr.gg/matches"
        response = requests.get(url)
        # Handle non-200 response
        if response.status_code != 200:
            print(f"Error: {url} responded with {response.status_code}")
            return
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find listed matches
        matches = soup.find_all('a', class_=['wf-module-item', 'match-item'])

        match_data = []
        for match in matches:
            # Extract the URL to the individual match page
            match_url = self.BASE_URL + match.get('href')
            
            # Extract the time information
            # This is hard, webpage adjusts for local timezone
            #match_time = match.find(class_='match-item-time').get_text(strip=True)
            
            # Check if the match is live or upcoming
            live_or_upcoming = match.find(class_='ml-status').get_text(strip=True)
            eta = match.find(class_='ml-eta')
            eta = eta.get_text(strip=True) if eta else None
            
            # Extract participating teams and their flag emojis
            teams = match.find_all(class_='match-item-vs-team')
            teams_info = [{
                'team_name': team.find(class_='match-item-vs-team-name').get_text(strip=True),
                'flag_emoji': team.find('span').get('class')[1]
            } for team in teams]
            
            # Extract event information
            event_info = match.find(class_='match-item-event').get_text().replace('\t', '').strip()

            match_data.append({
                'url': match_url,
                'status': live_or_upcoming,
                'eta': eta,
                'teams': [[t['team_name'], get_flag_unicode(t['flag_emoji'])] for t in teams_info],
                'event': event_info
            })
        
        await self.config.match_cache.set(match_data)
        await self.config.cache_time.set(datetime.now(timezone.utc).isoformat())

    async def _getresults(self):
        """Parse results from vlr"""

        # Get HTML response for upcoming matches
        url = "https://www.vlr.gg/matches/results"
        response = requests.get(url)
        # Handle non-200 response
        if response.status_code != 200:
            print(f"Error: {url} responded with {response.status_code}")
            return
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find listed matches
        matches = soup.find_all('a', class_=['wf-module-item', 'match-item'])

        match_data = []
        for match in matches:
            # Extract the URL to the individual match page
            match_url = self.BASE_URL + match.get('href')
            
            # Check if the match is live or upcoming
            eta = match.find(class_='ml-eta')
            eta = eta.get_text(strip=True) if eta else None
            
            # Extract participating teams and their flag emojis
            teams = match.find_all(class_=['match-item-vs-team'])
            teams_info = [{
                'team_score': int(team.find(class_=['match-item-vs-team-score']).get_text(strip=True)),
                'team_name': team.find(class_='match-item-vs-team-name').get_text(strip=True),
                'is_winner': 'mod-winner' in team.get('class', []),
                'flag_emoji': team.find('span').get('class')[1]
            } for team in teams]
            
            # Extract event information
            event_info = match.find(class_='match-item-event').get_text().replace('\t', '').strip()

            match_data.append({
                'url': match_url,
                'status': 'Completed',
                'eta': eta,
                'teams': [[t['team_name'], get_flag_unicode(t['flag_emoji']), t['team_score'], t['is_winner']] for t in teams_info],
                'event': event_info
            })
        
        await self.config.result_cache.set(match_data)
        await self.config.cache_time.set(datetime.now(timezone.utc).isoformat())

    async def _sendnotif(self):
        """Send out notifications for relevant matches"""

        def sub_check(match, sub_event, sub_team):
            """Check if the match is subscribed to"""
            subscribed = False
            reason = ""
            for se in sub_event:
                if se in match['event']:
                    subscribed = True
                    reason = f"Event: {se}"
                    break
            if not subscribed:
                for st in sub_team:
                    if st == match['teams'][0][0] or st == match['teams'][1][0]:
                        subscribed = True
                        reason = f"Team: {st}"
                        break
            
            return subscribed, reason

        # Get matches
        matches = await self.config.match_cache()
        results = await self.config.result_cache()

        # Need to do this for each guild
        all_guilds = await self.config.all_guilds()
        for guild_id in all_guilds:
            channel_id = all_guilds[guild_id]['channel_id']
            if channel_id is None:
                continue

            channel_obj = self.bot.get_channel(channel_id)

            sub_event = all_guilds[guild_id]['sub_event']
            sub_team = all_guilds[guild_id]['sub_team']
            notify_lead = all_guilds[guild_id]['notify_lead']

            for match in matches:
                eta_min = str_to_min(match['eta'])
                
                # Notify if the eta is sooner than the lead time
                # unless it is even earlier than the lead time - update rate
                # to avoid duplicate notifications
                if eta_min <= notify_lead and eta_min > notify_lead - (self.POLLING_RATE / 60):
                    # Notify if the event or team is subscribed
                    subscribed, reason = sub_check(match, sub_event, sub_team)
                    if subscribed:
                        await self._notify(channel_obj, match, reason)
                
                elif eta_min > notify_lead:
                    # matches are stored in chronological order, so can break safely
                    break
            
            for result in results:
                eta_min = str_to_min(result['eta'])

                # Notify if the eta is just earlier than the update rate
                if eta_min <= (self.POLLING_RATE / 60):
                    # Notify if the event or team is subscribed
                    subscribed, reason = sub_check(result, sub_event, sub_team)

                    if subscribed:
                        await self._result(channel_obj, match, reason)


    async def _notify(self, channel, match_data, reason):
        """ Helper function to send match notification """
        
        # Get HTML response for upcoming matches
        url = match_data["url"]
        response = requests.get(url)
        # Handle non-200 response
        if response.status_code != 200:
            print(f"Error: {url} responded with {response.status_code}")
            return
        soup = BeautifulSoup(response.content, 'html.parser')

        # Team information
        team_names = [
            soup.find(class_=["match-header-link-name mod-1"]).get_text(strip=True),
            soup.find(class_=["match-header-link-name mod-2"]).get_text(strip=True)
        ]
        team_urls = [
            self.BASE_URL + soup.find('a', class_=["match-header-link wf-link-hover mod-1"])['href'],
            self.BASE_URL + soup.find('a', class_=["match-header-link wf-link-hover mod-2"])['href']
        ]
        team_logos = [
            "https:"+soup.find('a', class_=["match-header-link wf-link-hover mod-1"]).find('img')['src'],
            "https:"+soup.find('a', class_=["match-header-link wf-link-hover mod-2"]).find('img')['src']
        ]

        # Event information
        event_info_div = soup.find(class_="match-header-event")
        event_info = event_info_div.get_text().replace('\t', '').replace('\n', ' ').strip()
        event_url = event_info_div['href']
        event_url = self.BASE_URL + event_url if not event_url.startswith('http') else event_url

        # Find match format (e.g., BO1, BO3, BO5)
        date_time = soup.find(class_="match-header-date").get_text().replace('\t', '').replace('\n', ' ').strip()
        match_format = soup.find(class_="match-header-vs-note").get_text(strip=True)

        # Find players
        team1_players = []
        team2_players = []

        team_tables = soup.find('div', class_="vm-stats-game", attrs={"data-game-id": 'all'})
        team_tables = team_tables.find_all('tbody')

        # Process each team table
        for team_index, team_table in enumerate(team_tables):
            player_rows = team_table.find_all('tr')
            for row in player_rows:
                # Extract player name and URL
                player_name_tag = row.find('a')
                player_name = player_name_tag.get_text().split()[0]
                player_url = player_name_tag['href']

                # Make URL absolute if necessary
                player_url = self.BASE_URL + player_url if not player_url.startswith('http') else player_url

                # Extract flag emoji
                flag_tag = row.find('i', class_='flag')
                flag_cls = flag_tag.get('class')[1]
                
                player_info = {
                    'name': player_name,
                    'flag': get_flag_unicode(flag_cls),
                    'url': player_url
                }

                # Append player information to the corresponding team list
                if team_index == 0:
                    team1_players.append(player_info)
                else:
                    team2_players.append(player_info)


        # Build embed
        embed = discord.Embed(
            title=f"\N{BELL} Upcoming Match in {match_data['eta']}",
            description=f"*Subscribed: {reason}*",
            color=0xff4654,
            url=url
        )

        embed.add_field(name=event_info, value=f"{match_format} | {date_time}", inline=False)

        team1_name = team_names[0]
        team1_val = '\n'.join([f"\N{BUSTS IN SILHOUETTE} [Team]({team_urls[0]})"]+[f"{p['flag']} [{p['name']}]({p['url']})" for p in team1_players])
        embed.add_field(name=team1_name, value=team1_val, inline=True)

        team2_name = team_names[1]
        team2_val = '\n'.join([f"\N{BUSTS IN SILHOUETTE} [Team]({team_urls[1]})"]+[f"{p['flag']} [{p['name']}]({p['url']})" for p in team2_players])
        embed.add_field(name=team2_name, value=team2_val, inline=True)

        embed.set_image(url=team_logos[0])
        embed_aux = discord.Embed(url=url).set_image(url=team_logos[1])

        await channel.send(embeds=[embed, embed_aux], allowed_mentions=None)
    
    async def _result(self, channel, result_data, reason):
        """Helper function to send match result"""

        # Build embed
        embed = discord.Embed(
            title=f"\N{WHITE HEAVY CHECK MARK} Match Complete",
            description=f"*Subscribed: {reason}*",
            color=0xff4654,
            url=result_data['url']
        )

        matchup = f"{result_data['teams'][0][1]} {result_data['teams'][0][0]} vs. {result_data['teams'][1][1]} {result_data['teams'][1][0]}"
        trophy = '\N{TROPHY}'
        result = f"{trophy if result_data['teams'][0][3] else ''} {result_data['teams'][0][2]} : {result_data['teams'][1][2]} {trophy if result_data['teams'][1][3] else ''}"

        embed.add_field(name=matchup, value=f"||{result}||", inline=False)
        embed.add_field(name='Event', value=f"*{result_data['event']}*", inline=False)

        await channel.send(embed=embed, allowed_mentions=None)

    #####################
    # PARSING LOOP TASK #
    #####################

    @tasks.loop(seconds=300)
    async def parse(self):
        """ Loop to check for matches from VLR """
        await self._getmatches()
        await self._getresults()
        await self._sendnotif()

    @parse.before_loop
    async def before_parse(self):
        await self.bot.wait_until_ready()

    # @commands.command()
    # async def vlrinterval(self, ctx: commands.Context, seconds: int = 300):
    #     """Set how often to retrieve matches from vlr in seconds. Defaults to 300."""
    #     self.POLLING_RATE = seconds
    #     self.parse.change_interval(seconds=seconds)
    #     await ctx.send(f"Interval changed to {seconds} sec.")

    @commands.command()
    @checks.mod_or_permissions(administrator=True)
    async def vlrupdate(self, ctx: commands.Context):
        """Force update matches from VLR. This does not trigger notifications."""
        await self._getmatches()
        await self._getresults()
        await ctx.send("Updated matches from VLR.")

    ################
    # LIST MATCHES #
    ################

    async def _matchlist(self, ctx: commands.Context, n: int = 5, cond: str = "Valorant"):
        """Helper function for printing matchlists"""

        # Don't print more than 20 matches at any point
        n = min(n, 20)

        matches = await self.config.match_cache()
        cache_time = await self.config.cache_time()

        cache_datetime = datetime.fromisoformat(cache_time)
        now_datetime = datetime.now(timezone.utc)
        delta = int((now_datetime - cache_datetime).total_seconds())

        if len(matches) == 0:
            print("Vlr match cache unpopulated, hard pulling")
            await self._getmatches()
            matches = await self.config.match_cache()
            cache_time = await self.config.cache_time()
        
        if cond == "VCT":
            matches = [m for m in matches if "Champions Tour" in m['event']]
        elif cond == "Game Changers":
            matches = [m for m in matches if "Game Changers" in m['event']]
        
        # Build embed
        embed = discord.Embed(
            title=f"Upcoming {cond} Matches",
            description=f"Retrieved {delta // 60} min {delta % 60} sec ago.",
            color=0xff4654
        )

        for match in matches[:n]:
            if match['status'] == 'LIVE':
                embed_name = "\N{LARGE RED CIRCLE} LIVE"
            else:
                embed_name = f"{match['status']} {match['eta']}"

            teams = "" 
            teams += " ".join(match['teams'][0][::-1])
            teams += " vs. "
            teams += " ".join(match['teams'][1][::-1])
            event = match['event']

            embed_value = f"[{teams}]({match['url']})\n*{event}*"

            embed.add_field(name=embed_name, value=embed_value, inline=False)

        await ctx.send(embed=embed, allowed_mentions=None)

    @commands.command()
    async def vlrmatches(self, ctx: commands.Context, n: int = 5):
        """Get upcoming Valorant esports matches.
        
        Defaults to 5, but request up to 20.
        Example: [p]vlrmatches 20
        """

        await self._matchlist(ctx, n)

    @commands.command()
    async def vlrmatchesvct(self, ctx: commands.Context, n: int = 5):
        """Get upcoming VCT esports matches.
        
        Filters for "Champions Tour" in the event string.
        Defaults to 5, but request up to 20.
        Example: [p]vlrmatchesvct 20
        """

        await self._matchlist(ctx, n, cond="VCT")

    @commands.command()
    async def vlrmatchesgc(self, ctx: commands.Context, n: int = 5):
        """Get upcoming Game Changers matches.
        
        Filters for "Game Changers" in the event string.
        Defaults to 5, but request up to 20.
        Example: [p]vlrmatchesgc 20
        """

        await self._matchlist(ctx, n, cond="Game Changers")
    
    ################
    # LIST RESULTS #
    ################

    async def _resultlist(self, ctx: commands.Context, n: int = 5, cond: str = "Valorant"):
        """Helper function for printing resultlists"""

        # Don't print more than 20 matches at any point
        n = min(n, 20)

        results = await self.config.result_cache()
        cache_time = await self.config.cache_time()

        cache_datetime = datetime.fromisoformat(cache_time)
        now_datetime = datetime.now(timezone.utc)
        delta = int((now_datetime - cache_datetime).total_seconds())

        if len(results) == 0:
            print("Vlr match cache unpopulated, hard pulling")
            await self._getresults()
            results = await self.config.result_cache()
            cache_time = await self.config.cache_time()
        
        if cond == "VCT":
            results = [m for m in results if "Champions Tour" in m['event']]
        elif cond == "Game Changers":
            results = [m for m in results if "Game Changers" in m['event']]
        
        # Build embed
        embed = discord.Embed(
            title=f"Completed {cond} Matches",
            description=f"Retrieved {delta // 60} min {delta % 60} sec ago.",
            color=0xff4654
        )

        for result_data in results[:n]:
            embed_name = f"Completed {result_data['eta']} ago"

            matchup = f"{result_data['teams'][0][1]} {result_data['teams'][0][0]} vs. {result_data['teams'][1][1]} {result_data['teams'][1][0]}"
            trophy = '\N{TROPHY}'
            result = f"{trophy if result_data['teams'][0][3] else ''} {result_data['teams'][0][2]} : {result_data['teams'][1][2]} {trophy if result_data['teams'][1][3] else ''}"

            event = result_data['event']

            embed_value = f"[{matchup}]({result_data['url']})\n||{result}||\n*{event}*"

            embed.add_field(name=embed_name, value=embed_value, inline=False)

        await ctx.send(embed=embed, allowed_mentions=None)

    @commands.command()
    async def vlrresults(self, ctx: commands.Context, n: int = 5):
        """Get completed Valorant esports results.
        
        Defaults to 5, but request up to 20.
        Example: [p]vlrresults 20
        """

        await self._resultlist(ctx, n)

    @commands.command()
    async def vlrresultsvct(self, ctx: commands.Context, n: int = 5):
        """Get completed VCT results.
        
        Filters for "Champions Tour" in the event string.
        Defaults to 5, but request up to 20.
        Example: [p]vlrresultsvct 20
        """

        await self._resultlist(ctx, n, cond="VCT")

    @commands.command()
    async def vlrresultsgc(self, ctx: commands.Context, n: int = 5):
        """Get completed Game Changers results.
        
        Filters for "Game Changers" in the event string.
        Defaults to 5, but request up to 20.
        Example: [p]vlrresultsgc 20
        """

        await self._resultlist(ctx, n, cond="Game Changers")

    # @commands.command()
    # async def testnotif(self, ctx: commands.Context):
    #     """Send out notifications for relevant matches"""

    #     # Get matches
    #     matches = await self.config.match_cache()
    #     results = await self.config.result_cache()
    #     all_guilds = await self.config.all_guilds()

    #     # Need to do this for each guild
    #     for guild_id in all_guilds:
    #         channel_id = all_guilds[guild_id]['channel_id']
    #         channel_obj = self.bot.get_channel(channel_id)

    #         sub_event = all_guilds[guild_id]['sub_event']
    #         sub_team = all_guilds[guild_id]['sub_team']
    #         notify_lead = all_guilds[guild_id]['notify_lead']

    #         for match in matches:
    #             eta_min = str_to_min(match['eta'])
    #             await self._notify(channel_obj, match, "test")
    #             break

    #         for result in results:
    #             eta_min = str_to_min(result['eta'])
    #             await self._result(channel_obj, result, "test")
    #             break