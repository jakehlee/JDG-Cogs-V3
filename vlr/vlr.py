import asyncio
import re
from datetime import datetime, timezone
import time
from pathlib import Path

from typing import Literal
import requests
from bs4 import BeautifulSoup

import discord
from discord.ext import tasks
from redbot.core import Config, checks, commands, app_commands
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

def validate_match_url(url):
    """ VLR match URLs - match URLs have an integer as the second part of the path (e.g. https://www.vlr.gg/303087/) instead of /event or /team"""
    return Path(url).parts[2].isdigit()

class VLR(commands.Cog):
    """VLR cog to track valorant esports matches and teams"""

    def __init__(self, bot):
        super().__init__()
        self.bot = bot
        self.config = Config.get_conf(self, identifier=611188252769002, force_registration=True)

        self.POLLING_RATE = 60 # global polling rate in seconds
        self.BASE_URL = "https://www.vlr.gg"

        default_global = {
            'match_cache': [],      # Caches first page of upcoming matches each poll
            'result_cache': [],     # Caches first page of results each poll
            'notify_cache': {},     # Caches full match data for notifications
            'cache_time': None      # Timestamps last cache update
        }
        self.config.register_global(**default_global)

        default_guild = {
            'channel_id': None,                                 # ID of channel where notification embeds are sent
            'sub_event': ['Champions Tour'],                    # Subscribed events, defaults to GC and VCT
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

    async def on_guild_join(self, guild):
        # This is illegal for red cogs but it's for a standalone bot
        await self.bot.tree.sync(guild=guild)
        print(f"Joined a new guild {guild.name}")

    vlr = app_commands.Group(name='vlr', description="Commands for Valorie", extras={'red_force_enable': True})

    vlr_config = app_commands.Group(name='config', description="Valorie configuration commands", extras={'red_force_enable': True}, parent=vlr)

    @vlr_config.command(name="notif_channel", description="Set notification channel", extras={'red_force_enable': True})
    @app_commands.describe(channel="Text channel for match notifications")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def notif_channel(self, interaction: discord.Interaction, channel: discord.abc.GuildChannel):
        """Set VLR channel for match notifications.
        
        Example: [p]vlr channel #valorant
        """

        if channel is not None:
            await self.config.guild(interaction.guild).channel_id.set(channel.id)
            await interaction.response.send_message(f"VLR channel has been set to {channel.mention}", ephemeral=True)
        else:
            await self.config.guild(interaction.guild).channel_id.set(None)
            await interaction.response.send_message(f"VLR channel has been cleared", ephemeral=True)

    @vlr_config.command(name="notif_time", description="Set notification time", extras={'red_force_enable': True})
    @app_commands.describe(minutes="How early match notifications will be sent in minutes")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def notif_time(self, interaction: discord.Interaction, minutes: int):
        """Set lead time for match notifications in minutes.

        Example: [p]vlr leadtime 15
        """

        await self.config.guild(interaction.guild).notify_lead.set(minutes)
        await interaction.response.send_message(f"Match notifications will be sent {minutes} mins before.", ephemeral=True)

    ##############################
    # NOTIFICATION SUBSCRIPTIONS #
    ##############################

    sub = app_commands.Group(name="sub", description='Commands to subscribe to notifications', extras={'red_force_enable': True}, parent=vlr)
    unsub = app_commands.Group(name="unsub", description='Commands to unsubscribe from notifications', extras={'red_force_enable': True}, parent=vlr)

    event_choices = [
        app_commands.Choice(name="All", value="ALL"),
        app_commands.Choice(name="VCT All", value="Champions Tour"),
        app_commands.Choice(name="VCT Masters", value="Champions Tour Masters"),
        app_commands.Choice(name="VCT Americas", value="Champions Tour Americas"),
        app_commands.Choice(name="VCT EMEA", value="Champions Tour EMEA"),
        app_commands.Choice(name="VCT Pacific", value="Champions Tour Pacific"),
        app_commands.Choice(name="VCT China", value="Champions Tour China"),
        app_commands.Choice(name="Game Changers", value="Game Changers")
    ]

    @sub.command(name="event", description="Subscribe to notifications for an event", extras={'red_force_enable': True})
    @app_commands.describe(event="The event to receive notifications for")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(event=event_choices)
    async def sub_event(self, interaction: discord.Interaction, event: str):
        """Subscribe to an event."""

        async with self.config.guild(interaction.guild).sub_event() as sub_event:
            if event in sub_event:
                # Already subscribed
                await interaction.response.send_message(f"Already subscribed to this event.", ephemeral=True)
            else:
            # Add subscription
                sub_event.append(event)
                await interaction.response.send_message(f"Subscribed to {event}", ephemeral=True)
    
    @unsub.command(name="event", description="Unsubscribe from notifications for an event", extras={'red_force_enable': True})
    @app_commands.describe(event="The event to stop receiving notifications for")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.choices(event=event_choices)
    async def unsub_event(self, interaction: discord.Interaction, event: str):
        """Unsubscribe from an event."""
        async with self.config.guild(interaction.guild).sub_event() as sub_event:
            if event in sub_event:
                sub_event.remove(event)
                await interaction.response.send_message(f"Unsubscribed from {event}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Not subscribed to this event.", ephemeral=True)

    @sub.command(name="team", description="Subscribe to notifications for a team", extras={'red_force_enable': True})
    @app_commands.describe(team="Team name must be exactly how it is spelled on vlr.gg")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def sub_team(self, interaction: discord.Interaction, team: str):
        """Subscribe to a team."""

        async with self.config.guild(interaction.guild).sub_team() as sub_team:
            if team in sub_team:
                await interaction.response.send_message(f"Already subscribed to this team.", ephemeral=True)
            else:
                sub_team.append(team)
                await interaction.response.send_message(f"Subscribed to {team}", ephemeral=True)

    @unsub.command(name="team", description="Unsubscribe from notifications for a team", extras={'red_force_enable': True})
    @app_commands.describe(team="Team name must be exactly how it is spelled on vlr.gg")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    async def unsub_team(self, interaction: discord.Interaction, team: str):
        """Unsubscribe from a team."""
        async with self.config.guild(interaction.guild).sub_team() as sub_team:
            if team in sub_team:
                sub_team.remove(team)
                await interaction.response.send_message(f"Unsubscribed from {team}", ephemeral=True)
            else:
                await interaction.response.send_message(f"Not subscribed to this team.", ephemeral=True)

    @sub.command(name="list", description="List the current event and team subscriptions", extras={'red_force_enable': True})
    @app_commands.guild_only()
    async def sub_list(self, interaction: discord.Interaction):
        """List the events and teams with subscriptions"""
        sub_team = await self.config.guild(interaction.guild).sub_team()
        sub_event = await self.config.guild(interaction.guild).sub_event()

        await interaction.response.send_message(f"Subscriptions:\nTeams: {sub_team}\nEvents: {sub_event}")


    #################
    # Voice Channel #
    #################

    vc = app_commands.Group(name="vc", description="Voice channel related commands", extras={'red_force_enable': True}, parent=vlr)

    @vc.command(name="enable", description="Enable watch party voice channels", extras={'red_force_enable': True})
    @app_commands.describe(default_channel="After the watch party ends, everyone will be moved to this channel")
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(move_members=True, manage_channels=True)
    async def vc_enable(self, interaction: discord.Interaction, default_channel: discord.VoiceChannel):
        """ Enable auto-created watch party voice channels.
        After the match ends, all members will be moved to the default voice channel.
        """

        if await self.config.guild(interaction.guild).vc_enabled():
            await interaction.response.send_message(f"Watch party voice channels already enabled", ephemeral=True)
            return

        # Initialize config storage
        await self.config.guild(interaction.guild).vc_enabled.set(True)
        await self.config.guild(interaction.guild).vc_default.set(default_channel.id)
        
        # Create watch party category for VCs
        category = await interaction.guild.create_category("VLR Watch Parties")
        await self.config.guild(interaction.guild).vc_category.set(category.id)

        await interaction.response.send_message(f"Match party voice channels enabled with default channel <#{default_channel.id}>", ephemeral=True)

    @vc.command(name="disable", description="Disable watch party voice channels", extras={'red_force_enable': True})
    @app_commands.guild_only()
    @app_commands.checks.has_permissions(administrator=True)
    @app_commands.checks.bot_has_permissions(move_members=True, manage_channels=True)
    async def vc_disable(self, interaction: discord.Interaction):
        """ Disable auto-created watch party voice channels.
        All currently-created voice channels will be removed.
        """
        if not await self.config.guild(interaction.guild).vc_enabled():
            await interaction.response.send_message(f"Watch party voice channels not enabled", ephemeral=True)
            return

        default_channel = self.bot.get_channel(await self.config.guild(interaction.guild).vc_default())
        vc_category = self.bot.get_channel(await self.config.guild(interaction.guild).vc_category())
        
        await self.config.guild(interaction.guild).vc_enabled.set(False)

        # Delete every watch party voice channel after moving everyone to the default channel
        async with self.config.guild(interaction.guild).vc_created() as vc_created:
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
        
        await interaction.response.send_message(f"Match party voice channels disabled", ephemeral=True)
        

    # @command_vlr_vc.command(name="force")
    # @commands.bot_has_guild_permissions(manage_channels=True)
    # async def command_vlr_vc_force(self, ctx: commands.Context, url: str):
    #     """ Force-create a watch-party voice channel with a vlr match url. 
        
    #     Example: !vlr vc force https://www.vlr.gg/111111/link-to-match-page
    #     """
        
    #     if not validate_match_url(url):
    #         await ctx.send(f"{url} is not a valid VLR match URL")

    #     # Get HTML response
    #     response = requests.get(url)
    #     # Handle non-200 response
    #     if response.status_code != 200:
    #         await ctx.send(f"Error: {url} responded with {response.status_code}")
    #         return
    #     # Create soup
    #     soup = BeautifulSoup(response.content, 'html.parser')

    #     # Team information
    #     team_A = soup.find(class_=["match-header-link-name mod-1"]).get_text(strip=True)
    #     team_B = soup.find(class_=["match-header-link-name mod-2"]).get_text(strip=True)
    #     matchup_text = f"{'-'.join(team_A.split(' '))}-vs-{'-'.join(team_B.split(' '))}"

    #     # Create VC
    #     created_channel = await self._create_vc(ctx.guild, url, matchup_text)
    #     await ctx.send(f"Match party voice channel created: <#{created_channel.id}>")

    #     # Update notified so that when results are sent, VC will also be destroyed naturally
    #     notified = await self.config.guild(ctx.guild).notified()
    #     if url not in notified:
    #         notified.append(url)
    #         await self.config.guild(ctx.guild).notified.set(notified)

    async def _create_vc(self, guild: discord.Guild, url: str, name: str):
        """Create a watch party VC
        
        Returns the created voice channel object
        """

        vc_category_id = await self.config.guild(guild).vc_category()
        vc_category = guild.get_channel(vc_category_id)
        if vc_category is None:
            category = await guild.create_category("VLR Watch Parties")
            await self.config.guild(guild).vc_category.set(category.id)
            vc_category = guild.get_channel(vc_category_id)

        # Create VC
        vc_object = await vc_category.create_voice_channel(name)
        # Keep track of which match is which VC
        async with self.config.guild(guild).vc_created() as vc_created:
            vc_created[url] = vc_object.id

        return vc_object

    async def _delete_vc(self, guild: discord.Guild, url: str):
        """Delete a watch party VC"""

        vc_default_id = await self.config.guild(guild).vc_default()
        vc_default = guild.get_channel(vc_default_id)

        async with self.config.guild(guild).vc_created() as vc_created:
            channel_id = vc_created.pop(url, None)
            if channel_id is not None:
                channel_obj = self.bot.get_channel(channel_id)
                if channel_obj is not None:
                    # Move everyone to default channel
                    if vc_default is not None:
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
        """ Helper function to get a single match's information """

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

        data['timestamp'] = datetime.now(timezone.utc).isoformat()
        return data

    async def _sendnotif(self):
        """Send out notifications for relevant matches"""

        def sub_check(match, sub_event, sub_team):
            """Check if the match is subscribed to"""
            subscribed = False
            reason = ""

            # Substring match to find subscribed team
            for st in sub_team:
                if st == match['teams'][0]['name'] or st == match['teams'][1]['name']:
                    subscribed = True
                    reason = f"Team: {st}"
                    break

            # Substring match to find subscribed event
            if not subscribed:
                for se in sub_event:
                    if se == "ALL" or all(s in match['event'] for s in se.split(' ')):
                        subscribed = True
                        reason = f"Event: {se}"
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
                # Send if we sent a pre-match notification about this match
                if result['url'] in notified_cache:
                    await self._result(guild_obj, channel_obj, result)

    async def _notify(self, guild, channel, match_data, reason):
        """ Helper function to send match notification """
        
        # We want to scrape the match page to get full player information
        # Get HTML response for upcoming matches
        async with self.config.notify_cache() as notify_cache:
            if match_data['url'] not in notify_cache:
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
        
        # Delete voice channel if enabled
        vc_enabled = await self.config.guild(guild).vc_enabled()
        if vc_enabled:
            await self._delete_vc(guild, result_data['url'])


    #####################
    # PARSING LOOP TASK #
    #####################

    @tasks.loop(seconds=60)
    async def parse(self):
        """ Loop to check for matches from VLR """
        await self._getmatches()
        await self._getresults()
        await self._sendnotif()
        await self._clear_notif_cache()

    @parse.before_loop
    async def before_parse(self):
        # Don't start parsing until the bot is ready
        await self.bot.wait_until_ready()

    # @command_vlr.command(name='interval')
    # @checks.is_owner()  # Because this is a global parameter
    # async def vlr_interval(self, ctx: commands.Context, seconds: int = 300):
    #     """Set how often to retrieve matches from vlr in seconds. Defaults to 300."""
    #     self.POLLING_RATE = seconds
    #     self.parse.change_interval(seconds=seconds)
    #     await ctx.send(f"Interval changed to {seconds} sec.")

    # @command_vlr.command(name='update')
    # @checks.is_owner()  # Because this runs a scrape
    # async def vlr_update(self, ctx: commands.Context):
    #     """Force update matches from VLR."""
    #     # Useful if we missed a polling cycle due to VLR server error
    #     # Notifications can be sent because caching prevents duplicates
    #     await self._getmatches()
    #     await self._getresults()
    #     await self._sendnotif()
    #     await self._clear_notif_cache()
    #     await ctx.send("Updated matches from VLR.")
    
    # @command_vlr.command(name='debug')
    # @checks.is_owner()
    # async def vlr_debug(self, ctx: commands.Context):
    #     channel_id = await self.config.guild(ctx.guild).channel_id()
    #     channel_obj = self.bot.get_channel(channel_id)

    #     matches = await self.config.match_cache()
    #     await self._notify(ctx.guild, channel_obj, matches[0], 'debug')
    #     await self._notify(ctx.guild, channel_obj, matches[0], 'debug')

    # @command_vlr.command(name='clear')
    # @checks.is_owner()
    # async def vlr_clear(self, ctx: commands.Context):
    #     await self.config.guild(ctx.guild).clear()

    async def _clear_notif_cache(self):
        """ Periodically clear the notification cache to prevent it from growing too large """
        async with self.config.notify_cache() as notify_cache:
            # For each item in the notify_cache dictionary, check if the 'timestamp' is older than 24 hours
            keys = list(notify_cache.keys())
            for key in keys:
                if (datetime.now(timezone.utc) - datetime.fromisoformat(notify_cache[key]['timestamp'])).total_seconds() > 86400:
                    del notify_cache[key]


    ################
    # LIST MATCHES #
    ################

    async def _matchlist(self, interaction: discord.Interaction, n: int = 5, cond: str = "Valorant"):
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
        await interaction.response.send_message(embed=embed, allowed_mentions=None)

    @vlr.command(name="matches", description="List upcoming matches", extras={'red_force_enable': True})
    @app_commands.describe(category="Category of matches to include")
    async def matches(self, interaction: discord.Interaction,
                            category: Literal["All", "VCT", "Game Changers"]):
        """Get upcoming Valorant esports matches."""
        await self._matchlist(interaction, 5, cond=category)

    
    ################
    # LIST RESULTS #
    ################

    async def _resultlist(self, interaction = discord.Interaction, n: int = 5, cond: str = "Valorant"):
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
        await interaction.response.send_message(embed=embed, allowed_mentions=None)


    @vlr.command(name="results", description="List match results", extras={'red_force_enable': True})
    @app_commands.describe(category="Category of results to include")
    async def results(self, interaction: discord.Interaction,
                            category: Literal["All", "VCT", "Game Changers"]):
        """Get completed Valorant esports results."""
        await self._resultlist(interaction, 5, cond=category)
