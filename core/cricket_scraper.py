import requests
from bs4 import BeautifulSoup
import json
import re
import time
import logging

# Configure local logger for the scraper
logger = logging.getLogger(__name__)

class CricketScraper:
    def __init__(self):
        self.base_url = "https://www.espncricinfo.com"
        self.headers = {
            "User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 16_0 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        }
        self.session = requests.Session()
        self.session.headers.update(self.headers)
        self.last_commentary_over = None

    def _get_json_state(self, soup):
        """Extracts and parses the __NEXT_DATA__ JSON state from the soup."""
        try:
            script = soup.find('script', id='__NEXT_DATA__')
            if script:
                return json.loads(script.string)
        except Exception as e:
            logger.error(f"Failed to parse __NEXT_DATA__: {e}")
        return None

    def _get_nested(self, data, path, default=None):
        """Safely get nested data from dict using a dot-separated path."""
        try:
            for key in path.split('.'):
                if isinstance(data, list):
                    key = int(key)
                data = data[key]
            return data
        except (KeyError, IndexError, TypeError, ValueError):
            return default

    def _safe_request(self, url):
        """Helper for optimized and safe GET requests."""
        try:
            response = self.session.get(url, timeout=6)
            response.raise_for_status()
            return response
        except Exception as e:
            logger.error(f"Network error accessing {url}: {e}")
            return None

    def get_live_matches(self):
        """Returns a list of live matches from ESPNcricinfo."""
        url = f"{self.base_url}/live-cricket-score"
        response = self._safe_request(url)
        if not response: return []
        
        try:
            soup = BeautifulSoup(response.content, 'html.parser')
            match_links = []
            
            cards = soup.find_all('div', class_=re.compile(r'ds-bg-fill-content-prime'))
            if not cards:
                cards = soup.find_all('div', class_=re.compile(r'ds-flex ds-flex-col'))

            for card in cards:
                link = card.find('a', href=lambda h: h and '/series/' in h and '/live-cricket-score' in h)
                if link:
                    teams = card.find_all('p', class_=re.compile(r'ds-text-tight-m'))
                    if len(teams) >= 2:
                        match_name = f"{teams[0].text.strip()} vs {teams[1].text.strip()}"
                    else:
                        match_name = link.text.strip()
                        match_name = re.sub(r'^Live\d*', '', match_name).strip()
                    
                    full_url = link['href']
                    if not full_url.startswith('http'):
                        full_url = self.base_url + full_url
                        
                    match_links.append({"name": match_name or "Live Match", "url": full_url})
            
            seen = set()
            unique_matches = []
            for m in match_links:
                if m['url'] not in seen:
                    seen.add(m['url'])
                    unique_matches.append(m)
            return unique_matches
        except Exception as e:
            logger.error(f"Parsing error in get_live_matches: {e}")
            return []

    def get_all_live_scores(self):
        """Fetches scores for all live matches optimized via JSON state."""
        url = f"{self.base_url}/live-cricket-score"
        response = self._safe_request(url)
        if not response: return []
        
        try:
            soup = BeautifulSoup(response.content, 'html.parser')
            state = self._get_json_state(soup)
            if not state: 
                logger.warning("No JSON state found, falling back to basic list scrape")
                return [] # Or implement basic fallback if needed

            matches_data = self._get_nested(state, "props.appPageProps.data.content.matches", [])
            matches = []
            
            for m in matches_data:
                team_objs = m.get('teams', [])
                if len(team_objs) >= 2:
                    matches.append({
                        "teamA": team_objs[0].get('team', {}).get('name', 'Unknown'),
                        "teamB": team_objs[1].get('team', {}).get('name', 'Unknown'),
                        "scoreA": team_objs[0].get('score') or 'Yet to bat',
                        "scoreB": team_objs[1].get('score') or 'Yet to bat',
                        "status": m.get('statusText', 'Live'),
                        "url": self.base_url + f"/series/{m.get('series', {}).get('slug')}/{m.get('slug')}-{m.get('objectId')}/live-cricket-score" if m.get('slug') else ""
                    })
            return matches
        except Exception as e:
            logger.error(f"Scraping failed in get_all_live_scores: {e}")
            return []

    def get_series_matches(self, series_url):
        """Fetches all matches from a specific series page."""
        response = self._safe_request(series_url)
        if not response: return []
        
        try:
            soup = BeautifulSoup(response.content, 'html.parser')
            state = self._get_json_state(soup)
            if not state: return []

            content = self._get_nested(state, "props.appPageProps.data.content", {})
            # Combine fixtures and results
            fixtures = content.get('recentFixtures', []) or []
            results = content.get('recentResults', []) or []
            matches_raw = fixtures + results
            
            matches = []
            for m in matches_raw:
                team_objs = m.get('teams', [])
                if len(team_objs) >= 2:
                    match_id = m.get('objectId')
                    slug = m.get('slug')
                    series_slug = m.get('series', {}).get('slug')
                    
                    url = ""
                    if slug and series_slug and match_id:
                        url = self.base_url + f"/series/{series_slug}/{slug}-{match_id}/live-cricket-score"

                    matches.append({
                        "teamA": team_objs[0].get('team', {}).get('name', 'Unknown'),
                        "teamB": team_objs[1].get('team', {}).get('name', 'Unknown'),
                        "scoreA": team_objs[0].get('score') or 'Yet to bat',
                        "scoreB": team_objs[1].get('score') or 'Yet to bat',
                        "status": m.get('statusText', 'Upcoming'),
                        "url": url,
                        "startTime": m.get('startTime'),
                        "venue": m.get('ground', {}).get('name')
                    })
            return matches
        except Exception as e:
            logger.error(f"Error in get_series_matches: {e}")
            return []

    def scrape_match_data(self, match_url):
        """Scrapes detailed data using high-fidelity JSON extraction."""
        try:
            # Check if this is a series URL instead of a match URL
            if "/series/" in match_url and match_url.count("/") <= 5:
                # Basic series URL detection (e.g. series/slug-id)
                # Match URLs usually have one more segment after the series part
                return self.get_series_matches(match_url)

            comm_url = match_url.replace("/live-cricket-score", "/ball-by-ball-commentary")
            if "/live-cricket-score" not in match_url and "/full-scorecard" not in match_url:
                 comm_url = match_url 

            response = self._safe_request(comm_url)
            if not response: return None

            soup = BeautifulSoup(response.content, 'html.parser')
            state = self._get_json_state(soup)
            if not state:
                logger.error("Match detail JSON state not found.")
                return None

            # Root for match details - handle different page structures
            # Live score page uses .data.data.match, Commentary page uses .data.match
            data_root = self._get_nested(state, "props.appPageProps.data")
            if data_root.get('data'): # Handle nested .data.data
                data_inner = data_root['data']
            else:
                data_inner = data_root

            match_info = data_inner.get('match', {})
            content = data_inner.get('content', {})
            live_perf = content.get('livePerformance', {})
            # Fallback for commentary page where performance is under supportInfo
            if not live_perf:
                live_perf = self._get_nested(content, "supportInfo.liveSummary", {})

            recent_comm = self._get_nested(content, "recentBallCommentary.ballComments", [])
            
            # 3.5 Extended Narrative (Over Summaries & Match State)
            # Match State (e.g., Stumps, Tea, Result)
            match_state = ""
            comments = content.get('comments', [])
            if comments:
                first_comm = comments[0]
                status_list = first_comm.get('commentPostTextItems') or []
                match_state = " ".join([i.get('html', '') for i in status_list if isinstance(i, dict) and i.get('html')])
                match_state = re.sub('<[^<]+?>', '', match_state).strip()

            over_summaries = {}
            for c in comments:
                if c.get('type') == 'OVER_END' and c.get('over'):
                    o_data = c['over']
                    ov_num = o_data.get('overNumber')
                    if ov_num:
                        # Bowler Spell Stats (e.g., 4-1-7-1)
                        bowler_info = ""
                        end_bowlers = o_data.get('overEndBowlers') or []
                        if end_bowlers:
                            b = end_bowlers[0]
                            bowler_info = f"{b.get('overs')}-{b.get('maidens')}-{b.get('conceded')}-{b.get('wickets')}"
                        
                        over_summaries[ov_num] = {
                            "runs": o_data.get('overRuns'),
                            "wickets": o_data.get('overWickets'),
                            "total_score": f"{o_data.get('totalRuns')}/{o_data.get('totalWickets')}",
                            "bowler_spell": bowler_info
                        }

            data = {
                "teamA": "Team A", "teamB": "Team B", "score": "Live", "overs": "0",
                "batters": [], "bowlers": [], "commentary": [], "history": [],
                "runRate": None, "reqRunRate": None, "projectedScore": None, "partnership": None,
                "matchState": match_state
            }

            # 1. Basic Info
            teams = match_info.get('teams', [])
            if len(teams) >= 2:
                data['teamA'] = teams[0].get('team', {}).get('longName') or teams[0].get('team', {}).get('name', "Team A")
                data['teamB'] = teams[1].get('team', {}).get('longName') or teams[1].get('team', {}).get('name', "Team B")
                
                # Check which team is currently batting (Double-guard against None)
                curr_inning_idx = ((match_info.get('liveInning') or 1) - 1) % 2
                if isinstance(teams, list) and curr_inning_idx < len(teams):
                    curr_inning = teams[curr_inning_idx]
                else:
                    curr_inning = {}
                    
                data['score'] = curr_inning.get('score', "Live")
                overs_info = curr_inning.get('scoreInfo') or "0"
                data['overs'] = str(overs_info).replace(" ov", "")

            # 2. Live Performance (Batters/Bowlers)
            if not isinstance(live_perf, dict):
                live_perf = {}
                
            # Batters can be in .batsmen or .livePerformance.batsmen
            batsmen = live_perf.get('batsmen') or []
            if not isinstance(batsmen, list): batsmen = []
            for b in batsmen:
                data['batters'].append({
                    "name": b.get('player', {}).get('name') or b.get('player', {}).get('longName', 'Unknown'),
                    "runs": b.get('runs', 0),
                    "balls": b.get('balls', 0),
                    "fours": b.get('fours', 0),
                    "sixes": b.get('sixes', 0),
                    "active": b.get('isStriker', False) or b.get('active', True)
                })
            
            bowlers = live_perf.get('bowlers') or live_perf.get('bowlers', [])
            for b in bowlers:
                data['bowlers'].append({
                    "name": b.get('player', {}).get('name') or b.get('player', {}).get('longName', 'Unknown'),
                    "overs": b.get('overs', 0),
                    "wickets": b.get('wickets', 0),
                    "runs": b.get('runs', 0),
                    "active": b.get('active', True)
                })

            # 3. Support Info (CRR, RRR, Partnership)
            support_info = content.get('supportInfo', {})
            live_info = support_info.get('liveInfo', {})
            data['runRate'] = live_info.get('currentRunRate')
            data['reqRunRate'] = live_info.get('requiredRunrate')
            data['projectedScore'] = live_info.get('projectedScore')
            data['partnership'] = self._get_nested(support_info, "liveSummary.partnershipText")

            # 4. Commentary & History
            for idx, ball in enumerate(recent_comm[:12]):
                ov_actual = ball.get('oversActual')
                over_num = ball.get('overNumber')
                over_label = ov_actual or f"{over_num}.{ball.get('ballNumber', 0)}"
                
                # Synthesize full commentary text
                title = ball.get('title', '')
                
                # Main text (ball description)
                text_items = ball.get('commentTextItems') or []
                text_main = " ".join([i.get('html', '') for i in text_items if isinstance(i, dict) and i.get('html')])
                
                # Post text (handled via matchState usually but keep for safety)
                post_items = ball.get('commentPostTextItems') or []
                text_post = " ".join([i.get('html', '') for i in post_items if isinstance(i, dict) and i.get('html')])
                
                # Dismissal text (wicket details)
                dismissal = ball.get('dismissalText', {})
                text_dismissal = dismissal.get('commentary', '') if isinstance(dismissal, dict) else ''
                
                # Over Summary Injection (Attractively formatted)
                ov_sum_text = ""
                if over_num in over_summaries:
                    s = over_summaries[over_num]
                    ov_sum_text = f"OVER {over_num} | {s['runs']} runs | {s['total_score']} | Bowler: {s['bowler_spell']}"

                # Combine parts - only show match_state on the absolute latest ball
                parts = []
                if idx == 0 and match_state: parts.append(match_state)
                parts.extend([p for p in [ov_sum_text, title, text_main, text_post, text_dismissal] if p])
                full_text = " — ".join(parts)
                
                # Strip HTML tags
                full_text = re.sub('<[^<]+?>', '', full_text).strip()
                
                # Precise History Ticker Formatting
                total = ball.get('totalRuns', 0)
                is_wicket = ball.get('isWicket')
                is_four = ball.get('isFour')
                is_six = ball.get('isSix')
                
                if is_wicket: event = 'W'
                elif is_four: event = '4'
                elif is_six: event = '6'
                else:
                    # Check for extras suffixes
                    suffix = ""
                    if ball.get('wides'): suffix = "w"
                    elif ball.get('noballs'): suffix = "nb"
                    elif ball.get('legbyes'): suffix = "lb"
                    elif ball.get('byes'): suffix = "b"
                    
                    if suffix:
                        event = f"{total}{suffix}"
                    elif total == 0:
                        event = '•'
                    else:
                        event = str(total)

                data['commentary'].append({
                    "over": str(over_label),
                    "text": full_text,
                    "type": "wicket" if event == 'W' else ("four" if event == '4' else ("six" if event == '6' else ""))
                })
                # Add to history (only for the newest 6 balls)
                if idx < 6:
                    data['history'].insert(0, event)

            return data
        except Exception as e:
            logger.error(f"Match detail JSON scraping failed: {e}")
            import traceback
            traceback.print_exc()
            return None

if __name__ == "__main__":
    scraper = CricketScraper()
    print("Testing optimized multi-feed scrape...")
    scores = scraper.get_all_live_scores()
    for s in scores:
        print(f"[{s['teamA']} {s['scoreA']}] vs [{s['teamB']} {s['scoreB']}] | Status: {s['status']}")
