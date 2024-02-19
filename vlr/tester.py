import requests
from bs4 import BeautifulSoup

if __name__ == "__main__":
    response = requests.get("https://www.vlr.gg/matches")
    print(response.status_code)
    # await ctx.send("Error handling")
    soup = BeautifulSoup(response.content, 'html.parser')

    matches = soup.find_all('a', class_=['wf-module-item', 'match-item'], limit=5)

    for match in matches:
        # Extract the URL to the individual match page
        match_url = 'https://www.vlr.gg' + match.get('href')
        
        # Extract the time information
        # This is difficult, webpage adjusts for perceived local timezone
        #match_time = match.find(class_='match-item-time').get_text(strip=True)
        
        # Check if the match is live or upcoming
        live_or_upcoming = match.find(class_='ml-status').get_text(strip=True)
        eta = match.find(class_='ml-eta')
        time_until = eta.get_text(strip=True) if eta else 'Now'
        
        # Extract participating teams and their flag emojis
        teams = match.find_all(class_='match-item-vs-team')
        teams_info = [{
            'team_name': team.find(class_='match-item-vs-team-name').get_text(strip=True),
            'flag_emoji': team.find('span').get('class')[1]  # This extracts the class name that indicates the flag
        } for team in teams]
        
        # Extract event information
        event_info = match.find(class_='match-item-event').get_text().replace('\t', '').strip().replace('\n', ': ')
        
        match_data = {
            'url': match_url,
            ''
        }

        print(f"Match URL: {match_url}")
        print(f"Time: {match_time}, Status: {live_or_upcoming} ({time_until})")
        print("Teams:")
        for team in teams_info:
            print(f" - {team['team_name']} ({team['flag_emoji']})")
        print(f"Event: {event_info}")
        print("---")