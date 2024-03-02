import asyncio
import re
from datetime import datetime, timezone
import time

import requests
from bs4 import BeautifulSoup

import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands
from redbot.core.utils.predicates import ReactionPredicate
from redbot.core.utils.menus import start_adding_reactions

def str_to_min(time_str):
    """ Parsing match time info from VLR's status string"""
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
    """ Getting the actual flag unicode from country code. Magic number."""
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
            'match_cache': [],      # Caches first page of upcoming matches each poll
            'result_cache': [],     # Caches first page of results each poll
            'notify_cache': [],
            'cache_time': None      # Timestamps last cache update
        }
        self.config.register_global(**default_global)

        default_guild = {
            'channel_id': None,                                 # ID of channel where notification embeds are sent
            'sub_event': ['Game Changers', 'Champions Tour'],   # Subscribed events, defaults to GC and VCT
            'sub_team': [],                                     # Subscribed teams, no defaults
            "notified": [],                                     # Match URLs that were notified and results should be sent
            "notify_lead": 15,                                  # How many minutes before the match a notif should be sent
            "vc_enabled": False,                                # Whether watch party VCs are enabled
            "vc_default": None,                                 # Channel ID where members are sent after a watchparty ends
            "vc_category": None,                                # Internal, keeps track of category channel id
            "vc_created": {}                                    # Lists created VCs so they can be destroyed
        }
        self.config.register_guild(**default_guild)

        self.parse.start()

        # Ensure parse loop is using hardcoded polling rate
        self.parse.change_interval(seconds=self.POLLING_RATE)
    
    def cog_unload(self):
        # Safe exit of task loop
        self.parse.cancel()
        self.command_vlr_vc_disable()

    @commands.group(name="vlr")
    async def command_vlr(self, ctx: commands.Context):
        """Commands to get VLR notifications and see upcoming/completed matches."""

    @command_vlr.command(name="channel")
    @checks.mod_or_permissions(administrator=True)
    async def command_vlr_channel(self, ctx: commands.Context, channel: discord.TextChannel = None):
        """Set VLR channel for match notifications.
        
        Example: [p]vlr channel #valorant
        """

        if channel is not None:
            await self.config.guild(ctx.guild).channel_id.set(channel.id)
            await ctx.send(f"VLR channel has been set to {channel.mention}")
        else:
            await self.config.guild(ctx.guild).channel_id.set(None)
            await ctx.send("VLR channel has been cleared")
    
    @command_vlr.command(name="leadtime")
    @checks.mod_or_permissions(administrator=True)
    async def command_vlr_leadtime(self, ctx: commands.Context, minutes: int):
        """Set lead time for match notifications in minutes.

        Example: [p]vlr leadtime 15
        """

        await self.config.guild(ctx.guild).notify_lead.set(minutes)
        ctx.send(f"Match notifications will be sent {minutes} mins before.")

    ##############################
    # NOTIFICATION SUBSCRIPTIONS #
    ##############################

    @command_vlr.group(name="sub")
    async def command_vlr_sub(self, ctx: commands.Context):
        """Subscribe to vlr event and team notifications."""

        # Messy, but prints every time this group is called for easy copy-paste and subscription awareness
        sub_event = await self.config.guild(ctx.guild).sub_event()
        sub_team = await self.config.guild(ctx.guild).sub_team()
        await ctx.send(f"Current subscriptions:\nEvent subs: {sub_event}\nTeam subs: {sub_team}")

    @command_vlr_sub.command("event")
    @checks.mod_or_permissions(administrator=True)
    async def command_vlr_sub_event(self, ctx: commands.Context, event: str):
        """Subscribe or Unsubscribe from an event.

        Notifications will be sent if this substring exists in the event string.
        If there is a space in the event name, wrap it in quotes.
        Example: [p]vlr sub event "Game Changers"
        """

        sub_event = await self.config.guild(ctx.guild).sub_event()

        if event in sub_event:
            # Already subscribed, ask if they want to unsubscribe
            msg = await ctx.send(f"Already subscribed to event \"{event}\", unsubscribe?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            await ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result is True:
                # Unsubscribe
                sub_event.remove(event)
                await self.config.guild(ctx.guild).sub_event.set(sub_event)
                await ctx.send(f"Unsubscribed from event. Remaining: {sub_event}")
            else:
                # No change
                await ctx.send(f"Event subscriptions unchanged: {sub_event}")
        else:
            # Not already subscribed, ask if they want to subscribe
            msg = await ctx.send(f"Subscribe to event \"{event}\"?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            await ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result is True:
                # Subscribe
                sub_event.append(event)
                await self.config.guild(ctx.guild).sub_event.set(sub_event)
                await ctx.send(f"Event subscription added: {sub_event}")
            else:
                # No change
                await ctx.send(f"Event subscriptions unchanged: {sub_event}")

    @command_vlr_sub.command("team")
    @checks.mod_or_permissions(administrator=True)
    async def command_vlr_sub_team(self, ctx: commands.Context, team: str):
        """Subscribe or Unsubscribe from a team.

        Notifications will be sent if a team name matches this string exactly.
        If there is a space in the team name, wrap it in quotes. If the team name has special characters, it must be included.
        Example: [p]vlr sub event "Sentinels"
        """

        sub_team = await self.config.guild(ctx.guild).sub_team()

        if team in sub_team:
            # Already subscribed, ask if they want to unsubscribe
            msg = await ctx.send(f"Already subscribed to team \"{team}\", unsubscribe?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            await ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result is True:
                # Unsubscribe
                sub_team.remove(team)
                self.config.guild(ctx.guild).sub_team.set(sub_event)
                await ctx.send(f"Unsubscribed from team. Remaining: {sub_team}")
            else:
                # No change
                await ctx.send(f"Team subscriptions unchanged: {sub_team}")
        else:
            # Not already subscribed, ask if they want to subscribe
            msg = await ctx.send(f"Subscribe to team \"{event}\"?")
            start_adding_reactions(msg, ReactionPredicate.YES_OR_NO_EMOJIS)
            pred = ReactionPredicate.yes_or_no(msg, ctx.author)
            await ctx.bot.wait_for("reaction_add", check=pred)
            if pred.result is True:
                # Subscribe
                sub_team.append(team)
                self.config.guild(ctx.guild).sub_team.set(sub_team)
                await ctx.send(f"Team subscription added: {sub_team}")
            else:
                # No change
                await ctx.send(f"Team subscriptions unchanged: {sub_team}")
    
    #################
    # Voice Channel #
    #################

    @command_vlr.group(name="vc")
    async def command_vlr_vc(self, ctx: commands.Context):
        """Enable or disable watch party voice channel creation."""

        vc_enabled = await self.config.guild(ctx.guild).vc_enabled()
        await ctx.send(f"Watch party voice channel creation is currently {'enabled' if vc_enabled else 'disabled'}")

    @command_vlr_vc.command(name="enable")
    @commands.bot_has_guild_permissions(move_members=True, manage_channels=True)
    @checks.mod_or_permissions(administrator=True)
    async def command_vlr_vc_enable(self, ctx: commands.Context, default: str = None):
        """ Enable auto-created watch party voice channels.
        After the match ends, all members will be moved to the default voice channel.
        The default voice channel name should be its exact name.
        
        Example: !vlr vc enable "General Chat"
        """
        # Users can't easily tag a voice channel from Discord so we have to take a string and look for it
        default_channel = discord.utils.get(ctx.guild.channels, name=default)
        if default_channel is None:
            await ctx.send(f"Error: Failed to find the default voice channel, please double check the name.")
            return
        
        if await self.config.guild(ctx.guild).vc_enabled():
            return

        # Initialize config storage
        await self.config.guild(ctx.guild).vc_enabled.set(True)
        await self.config.guild(ctx.guild).vc_default.set(default_channel.id)
        
        # Create watch party category for VCs
        category = await ctx.guild.create_category("VLR Watch Parties")
        await self.config.guild(ctx.guild).vc_category.set(category.id)

        await ctx.send(f"Match party voice channel creation enabled with default channel <#{default_channel.id}>")
        await ctx.send(f"Please ensure bot has a role with 'Move Members' and 'Manage Channels' permissions.")


    @command_vlr_vc.command(name="disable")
    @commands.bot_has_guild_permissions(move_members=True, manage_channels=True)
    @checks.mod_or_permissions(administrator=True)
    async def command_vlr_vc_disable(self, ctx: commands.Context):
        """ Disable auto-created watch party voice channels.
        All currently-created voice channels will be removed.

        Example: !vlr vc disable
        """
        if not await self.config.guild(ctx.guild).vc_enabled():
            return

        default_channel = self.bot.get_channel(await self.config.guild(ctx.guild).vc_default())
        vc_category = self.bot.get_channel(await self.config.guild(ctx.guild).vc_category())
        
        await self.config.guild(ctx.guild).vc_enabled.set(False)

        # Delete every watch party voice channel after moving everyone to the default channel
        async with self.config.guild(ctx.guild).vc_created() as vc_created:
            for vc in vc_created:
                this_channel = self.bot.get_channel(vc_created[vc])
                if this_channel is None:
                    # Already deleted
                    continue
                this_members = this_channel.members
                for m in this_members:
                    await m.move_to(default_channel)
                
                await this_channel.delete(reason="VLR VC Disabled")
            
            vc_created.clear()
        
        # Delete the category too
        if vc_category is not None:
            await vc_category.delete(reason="VLR VC Disabled")
        
        await ctx.send(f"Match party voice channel creation disabled.")
        

    @command_vlr_vc.command(name="force")
    @commands.bot_has_guild_permissions(manage_channels=True)
    async def command_vlr_vc_force(self, ctx: commands.Context, url: str):
        """ Force-create a watch party channel if it wasn't notified
        
        Example: !vlr vc force https://www.vlr.gg/111111/link-to-match-page
        """

        # Get HTML response
        response = requests.get(url)
        # Handle non-200 response
        if response.status_code != 200:
            print(f"Error: {url} responded with {response.status_code}")
            return
        # Create soup
        soup = BeautifulSoup(response.content, 'html.parser')

        # Team information
        team_A = soup.find(class_=["match-header-link-name mod-1"]).get_text(strip=True)
        team_B = soup.find(class_=["match-header-link-name mod-2"]).get_text(strip=True)
        matchup_text = f"{'-'.join(team_A.split(' '))}-vs-{'-'.join(team_B.split(' '))}"

        # Create VC
        created_channel = await self._create_vc(ctx.guild, url, matchup_text)
        await ctx.send(f"Match party voice channel created: <#{created_channel.id}>")

        # Update notified so that when results are sent, VC will also be destroyed naturally
        notified = await self.config.guild(ctx.guild).notified()
        if url not in notified:
            notified.append(url)
            await self.config.guild(ctx.guild).notified.set(notified)

    async def _create_vc(self, guild: discord.Guild, url: str, name: str):
        """Create a watch party VC
        
        Returns the created voice channel object
        """

        vc_category_id = await self.config.guild(guild).vc_category()
        vc_category = guild.get_channel(vc_category_id)

        # Create VC
        vc_object = await vc_category.create_voice_channel(name)
        # Keep track of which match is which VC
        async with self.config.guild(guild).vc_created() as vc_created:
            # Bug where empty dicts sometimes return as lists?
            if type(vc_created) is list:
                vc_created = {url: vc_object.id}
            else:
                vc_created[url] = vc_object.id

        return vc_object

    async def _delete_vc(self, guild: discord.Guild, url: str):
        """Delete a watch party VC"""

        vc_default_id = await self.config.guild(guild).vc_default()
        vc_default = guild.get_channel(vc_default_id)

        async with self.config.guild(guild).vc_created() as vc_created:
            # Empty is list
            if type(vc_created) is list:
                return

            channel_id = vc_created.pop(url, None)
            if channel_id is not None:
                channel_obj = self.bot.get_channel(channel_id)
                
                # Move everyone to default channel
                for m in channel_obj.members:
                    await m.move_to(vc_default)
                
                await channel_obj.delete(reason="Match Ended")
            
    #####################
    # Utility Functions #
    #####################

    async def _getmatches(self):
        """Parse matches from vlr"""

        # Get HTML response for upcoming matches
        url = "https://www.vlr.gg/matches"
        response = requests.get(url)
        # Handle non-200 response
        if response.status_code != 200:
            print(f"Error: {url} responded with {response.status_code}")
            return
        # Create Soup
        soup = BeautifulSoup(response.content, 'html.parser')

        # Find listed matches
        matches = soup.find_all('a', class_=['wf-module-item', 'match-item'])

        match_data = []
        for match in matches:
            # Extract the URL to the individual match page
            match_url = self.BASE_URL + match.get('href')
            
            # Extract the time information
            # This is hard, webpage adjusts for local timezone, skip
            #match_time = match.find(class_='match-item-time').get_text(strip=True)
            
            # Check if the match is live or upcoming
            live_or_upcoming = match.find(class_='ml-status').get_text(strip=True)
            eta = match.find(class_='ml-eta')
            eta = eta.get_text(strip=True) if eta else None
            
            # Extract participating teams and their flag emojis
            teams = match.find_all(class_='match-item-vs-team')
            teams_info = [{
                'name': team.find(class_='match-item-vs-team-name').get_text(strip=True),
                'flag': get_flag_unicode(team.find('span').get('class')[1])
            } for team in teams]
            
            # Extract event information
            event_info = match.find(class_='match-item-event').get_text().replace('\t', '').strip()

            # Add to match data cache
            match_data.append({
                'url': match_url,
                'status': live_or_upcoming,
                'eta': eta,
                'teams': teams_info,
                'event': event_info
            })
        
        # Push everything to config
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
        # Create soup
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
                'score': int(team.find(class_=['match-item-vs-team-score']).get_text(strip=True)),
                'name': team.find(class_='match-item-vs-team-name').get_text(strip=True),
                'is_winner': 'mod-winner' in team.get('class', []),
                'flag': get_flag_unicode(team.find('span').get('class')[1])
            } for team in teams]
            
            # Extract event information
            event_info = match.find(class_='match-item-event').get_text().replace('\t', '').strip()

            # Add to result data cache
            match_data.append({
                'url': match_url,
                'status': 'Completed',
                'eta': eta,
                'teams': teams_info,
                'event': event_info
            })
        
        # Push everything to config
        await self.config.result_cache.set(match_data)
        await self.config.cache_time.set(datetime.now(timezone.utc).isoformat())
    
    async def _getmatch(self, match_data: dict):
        response = requests.get(match_data['url'])
        # Handle non-200 response
        if response.status_code != 200:
            print(f"Error: {match_data['url']} responded with {response.status_code}")
            return
        # Create soup
        soup = BeautifulSoup(response.content, 'html.parser')

        # Team information
        data = {'event': {}}
        teamA = {}
        teamB = {}

        teamA['name'] = soup.find(class_=["match-header-link-name mod-1"]).get_text(strip=True)
        teamB['name'] = soup.find(class_=["match-header-link-name mod-2"]).get_text(strip=True)
        teamA['url'] = self.BASE_URL + soup.find('a', class_=["match-header-link wf-link-hover mod-1"])['href']
        teamB['url'] = self.BASE_URL + soup.find('a', class_=["match-header-link wf-link-hover mod-2"])['href']
        teamA['logo'] = "https:"+soup.find('a', class_=["match-header-link wf-link-hover mod-1"]).find('img')['src']
        teamB['logo'] = "https:"+soup.find('a', class_=["match-header-link wf-link-hover mod-2"]).find('img')['src']

        # Event information
        event_info_div = soup.find(class_="match-header-event")
        data['event']['info'] = event_info_div.get_text().replace('\t', '').replace('\n', ' ').strip()
        
        event_url = event_info_div['href']
        event_url = self.BASE_URL + event_url if not event_url.startswith('http') else event_url
        data['event']['url'] = event_url

        # Find match format (e.g., BO1, BO3, BO5)
        data['event']['datetime'] = soup.find(class_="match-header-date").get_text().replace('\t', '').replace('\n', ' ').strip()
        data['event']['format'] = soup.find(class_="match-header-vs-note").get_text(strip=True)

        # Find players
        teamA['players'] = []
        teamB['players'] = []

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
                    teamA['players'].append(player_info)
                else:
                    teamB['players'].append(player_info)

        data['teamA'] = teamA
        data['teamB'] = teamB

        # Matchup String
        team_A = match_data['teams'][0]
        team_B = match_data['teams'][1]
        data['matchup'] = f"{team_A['flag']} {team_A['name']} vs. {team_B['flag']} {team_B['name']}"
        data['matchup_text'] = f"{team_A['name'].replace(' ', '-')}-vs-{team_B['name'].replace(' ', '-')}"

        return data

    async def _sendnotif(self):
        """Send out notifications for relevant matches"""

        def sub_check(match, sub_event, sub_team):
            """Check if the match is subscribed to"""
            subscribed = False
            reason = ""

            # Substring match to find subscribed event
            for se in sub_event:
                if se in match['event']:
                    subscribed = True
                    reason = f"Event: {se}"
                    break
            
            # Exact string match to find subscribed team
            if not subscribed:
                for st in sub_team:
                    if st == match['teams'][0]['name'] or st == match['teams'][1]['name']:
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
            
            # Get notification channel ID, if one isn't sent we don't send notifications
            channel_id = all_guilds[guild_id]['channel_id']
            if channel_id is None:
                continue

            channel_obj = self.bot.get_channel(channel_id)
            guild_obj = self.bot.get_guild(guild_id)

            sub_event = all_guilds[guild_id]['sub_event']
            sub_team = all_guilds[guild_id]['sub_team']
            notify_lead = all_guilds[guild_id]['notify_lead']
            notified_cache = all_guilds[guild_id]['notified']

            for match in matches:
                # For each match, check if it's time to send a notification
                eta_min = str_to_min(match['eta'])
                
                # Notify if the eta is sooner than the lead time or if it's LIVE already
                if eta_min <= notify_lead or match['status'] == 'LIVE':
                    # Check if we're subscribed to this match
                    subscribed, reason = sub_check(match, sub_event, sub_team)
                    # Notify if notification hasn't occurred yet, otherwise it's a duplicate
                    if match['url'] not in notified_cache and subscribed:
                        # This helper function also updates the notified cache
                        await self._notify(guild_obj, channel_obj, match, reason)
                
                elif eta_min > notify_lead:
                    # Matches are sorted soonest to latest so we can break safely 
                    break
            
            for result in results:
                # For each result, check if we should send a notification
                eta_min = str_to_min(result['eta'])

                # Send if we sent a pre-match notification about this match
                if result['url'] in notified_cache:
                    await self._result(guild_obj, channel_obj, result)


    async def _notify(self, guild, channel, match_data, reason):
        """ Helper function to send match notification """
        
        # We want to scrape the match page to get full player information
        # Get HTML response for upcoming matches
        async with self.config.notify_cache() as notify_cache:
            if type(notify_cache) is list:
                print('cache empty')
                full_data = await self._getmatch(match_data)
                notify_cache = {match_data['url']: full_data}
            elif match_data['url'] not in notify_cache:
                print('cache missed')
                full_data = await self._getmatch(match_data)
                notify_cache[match_data['url']] = full_data
            else:
                print('cache hit')
                full_data = notify_cache[match_data['url']]

        # Create voice channel if enabled
        if await self.config.guild(guild).vc_enabled():
            created_vc = await self._create_vc(guild, match_data['url'], full_data['matchup_text'])

        # Build embed
        embed = discord.Embed(
            title=f"\N{BELL} Upcoming Match in {match_data['eta']}",
            description=full_data['matchup'],
            color=0xff4654,
            url=match_data['url']
        )

        # Footer to explain why we're sending this notification
        embed.set_footer(text=f"Subscribed to {reason}")
        embed.add_field(name=full_data['event']['info'], value=f"{full_data['event']['format']} | {full_data['event']['datetime']}", inline=False)

        # Team A information inline
        teamA_name = full_data['teamA']['name']
        teamA_val = f"\N{BUSTS IN SILHOUETTE} [Team]({full_data['teamA']['url']})"
        for p in full_data['teamA']['players']:
            teamA_val += f"\n{p['flag']} [{p['name']}]({p['url']})"
        embed.add_field(name=teamA_name, value=teamA_val, inline=True)

        # Team B information inline
        teamB_name = full_data['teamB']['name']
        teamB_val = f"\N{BUSTS IN SILHOUETTE} [Team]({full_data['teamB']['url']})"
        for p in full_data['teamB']['players']:
            teamB_val += f"\n{p['flag']} [{p['name']}]({p['url']})"
        embed.add_field(name=teamB_name, value=teamB_val, inline=True)

        # Tag the voice channel where the watch party is happening if it's enabled
        if await self.config.guild(guild).vc_enabled():
            embed.add_field(name="Watch Party", value=f"<#{created_vc.id}>", inline=False)

        # Team logo images
        embed.set_image(url=full_data['teamA']['logo'])
        embed_aux = discord.Embed(url=match_data['url']).set_image(url=full_data['teamB']['logo'])

        # Send embed
        await channel.send(embeds=[embed, embed_aux], allowed_mentions=None)

        # Update cache, notification successfully sent
        async with self.config.guild(guild).notified() as notified:
            notified.append(match_data['url'])
    
    async def _result(self, guild, channel, result_data):
        """Helper function to send match result"""

        # Build embed
        # Matchup string
        team_A = result_data['teams'][0]
        team_B = result_data['teams'][1]
        matchup = f"{team_A['flag']} {team_A['name']} vs. {team_B['flag']} {team_B['name']}"

        # Embed object
        embed = discord.Embed(
            title=f"\N{WHITE HEAVY CHECK MARK} Match Complete",
            description=matchup,
            color=0xff4654,
            url=result_data['url']
        )

        # Spoilered match result with trophy emoji
        trophy = '\N{TROPHY}'
        result = f"{trophy if team_A['is_winner'] else ''} {team_A['name']} {team_A['score']} : {team_B['score']} {team_B['name']} {trophy if team_B['is_winner'] else ''}"
        embed.add_field(name='Scoreline', value=f"||{result}||", inline=False)
        embed.add_field(name='Event', value=f"*{result_data['event']}*", inline=False)

        # Send embed
        await channel.send(embed=embed, allowed_mentions=None)

        # Update cache, result successfully sent
        async with self.config.guild(guild).notified() as notified:
            notified.remove(result_data['url'])
        
        # Update notification cache
        async with self.config.notify_cache() as notify_cache:
            if type(notify_cache) is not list:
                notify_cache.pop(result_data['url'], None)
        
        # Delete voice channel if enabled
        vc_enabled = await self.config.guild(guild).vc_enabled()
        if vc_enabled:
            await self._delete_vc(guild, result_data['url'])


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
        # Don't start parsing until the bot is ready
        await self.bot.wait_until_ready()

    @command_vlr.command(name='interval')
    @checks.is_owner()  # Because this is a global parameter
    async def vlr_interval(self, ctx: commands.Context, seconds: int = 300):
        """Set how often to retrieve matches from vlr in seconds. Defaults to 300."""
        self.POLLING_RATE = seconds
        self.parse.change_interval(seconds=seconds)
        await ctx.send(f"Interval changed to {seconds} sec.")

    @command_vlr.command(name='update')
    @checks.is_owner()  # Because this runs a scrape
    async def vlr_update(self, ctx: commands.Context):
        """Force update matches from VLR.
        """
        # Useful if we missed a polling cycle due to VLR server error
        # Notifications can be sent because caching prevents duplicates
        await self._getmatches()
        await self._getresults()
        await self._sendnotif()
        await ctx.send("Updated matches from VLR.")
    
    @command_vlr.command(name='debug')
    @checks.is_owner()
    async def vlr_debug(self, ctx: commands.Context):
        channel_id = await self.config.guild(ctx.guild).channel_id()
        channel_obj = self.bot.get_channel(channel_id)

        matches = await self.config.match_cache()
        await self._notify(ctx.guild, channel_obj, matches[0], 'debug')
        await self._notify(ctx.guild, channel_obj, matches[0], 'debug')

    @command_vlr.command(name='clear')
    @checks.is_owner()
    async def vlr_clear(self, ctx: commands.Context):
        await self.config.guild(ctx.guild).clear()


    ################
    # LIST MATCHES #
    ################

    async def _matchlist(self, ctx: commands.Context, n: int = 5, cond: str = "Valorant"):
        """Helper function for printing matchlists"""

        # Don't print more than 20 matches at any point
        n = min(n, 20)

        # Get match cache
        matches = await self.config.match_cache()
        cache_time = await self.config.cache_time()

        # Get how long the cache was updated
        cache_datetime = datetime.fromisoformat(cache_time)
        now_datetime = datetime.now(timezone.utc)
        delta = int((now_datetime - cache_datetime).total_seconds())

        # Couldn't find anything in the cache, forcing an update
        if len(matches) == 0:
            print("Vlr match cache unpopulated, hard pulling")
            await self._getmatches()
            matches = await self.config.match_cache()
            cache_time = await self.config.cache_time()

        # Filter matches depending on which major categories were requested 
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

        # New field for each match
        for match in matches[:n]:
            if match['status'] == 'LIVE':
                embed_name = "\N{LARGE RED CIRCLE} LIVE"
            else:
                embed_name = f"{match['status']} {match['eta']}"
            team_A = match['teams'][0]
            team_B = match['teams'][1]
            matchup = f"{team_A['flag']} {team_A['name']} vs. {team_B['flag']} {team_B['name']}"
            event = match['event']

            embed_value = f"[{matchup}]({match['url']})\n*{event}*"

            embed.add_field(name=embed_name, value=embed_value, inline=False)

        # Send embed
        await ctx.send(embed=embed, allowed_mentions=None)

    @command_vlr.group(name="matches")
    async def command_vlr_matches(self, ctx: commands.Context):
        """Get upcoming Valorant esports matches."""

    @command_vlr_matches.command(name="all")
    async def command_vlr_matches_all(self, ctx: commands.Context, n: int = 5):
        """Get all upcoming matches.
        
        Defaults to 5, but request up to 20.
        Example: [p]vlr matches all 20
        """

        await self._matchlist(ctx, n)

    @command_vlr_matches.command(name="vct")
    async def command_vlr_matches_vct(self, ctx: commands.Context, n: int = 5):
        """Get upcoming VCT esports matches.
        
        Filters for "Champions Tour" in the event string.
        Defaults to 5, but request up to 20.
        Example: [p]vlr matches vct 20
        """

        await self._matchlist(ctx, n, cond="VCT")

    @command_vlr_matches.command(name="gc")
    async def command_vlr_matches_gc(self, ctx: commands.Context, n: int = 5):
        """Get upcoming Game Changers matches.
        
        Filters for "Game Changers" in the event string.
        Defaults to 5, but request up to 20.
        Example: [p]vlr matches gc 20
        """

        await self._matchlist(ctx, n, cond="Game Changers")
    
    ################
    # LIST RESULTS #
    ################

    async def _resultlist(self, ctx: commands.Context, n: int = 5, cond: str = "Valorant"):
        """Helper function for printing resultlists"""

        # Don't print more than 20 matches at any point
        n = min(n, 20)

        # Get results cache
        results = await self.config.result_cache()
        cache_time = await self.config.cache_time()

        # Get how long ago the cache was updated
        cache_datetime = datetime.fromisoformat(cache_time)
        now_datetime = datetime.now(timezone.utc)
        delta = int((now_datetime - cache_datetime).total_seconds())

        # Couldn't find anything in the cache, forcing an update
        if len(results) == 0:
            print("Vlr match cache unpopulated, hard pulling")
            await self._getresults()
            results = await self.config.result_cache()
            cache_time = await self.config.cache_time()
        
        # Filter results depending on which major categories were requested
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

        # New field for each result
        for result_data in results[:n]:
            embed_name = f"Started {result_data['eta']} ago"

            team_A = result_data['teams'][0]
            team_B = result_data['teams'][1]
            matchup = f"{team_A['flag']} {team_A['name']} vs. {team_B['flag']} {team_B['name']}"
            trophy = '\N{TROPHY}'
            result = f"{trophy if team_A['is_winner'] else ''} {team_A['score']} : {team_B['score']} {trophy if team_B['is_winner'] else ''}"

            event = result_data['event']

            # Needs to be spoilered
            embed_value = f"[{matchup}]({result_data['url']})\n||{result}||\n*{event}*"

            embed.add_field(name=embed_name, value=embed_value, inline=False)

        # Send embed
        await ctx.send(embed=embed, allowed_mentions=None)

    @command_vlr.group(name="results")
    async def command_vlr_results(self, ctx: commands.Context):
        """Get completed Valorant esports results."""

    @command_vlr_results.command(name="all")
    async def command_vlr_results_all(self, ctx: commands.Context, n: int = 5):
        """Get completed Valorant esports results.
        
        Defaults to 5, but request up to 20.
        Example: [p] vlr results 20
        """

        await self._resultlist(ctx, n)

    @command_vlr_results.command(name="vct")
    async def command_vlr_results_vct(self, ctx: commands.Context, n: int = 5):
        """Get completed VCT results.
        
        Filters for "Champions Tour" in the event string.
        Defaults to 5, but request up to 20.
        Example: [p]vlr results vct 20
        """

        await self._resultlist(ctx, n, cond="VCT")

    @command_vlr_results.command(name="gc")
    async def command_vlr_results_gc(self, ctx: commands.Context, n: int = 5):
        """Get completed Game Changers results.
        
        Filters for "Game Changers" in the event string.
        Defaults to 5, but request up to 20.
        Example: [p]vlr results gc 20
        """

        await self._resultlist(ctx, n, cond="Game Changers")
