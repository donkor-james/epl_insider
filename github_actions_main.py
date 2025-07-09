import uuid
import feedparser
import requests
import json
import logging
from datetime import datetime, timedelta
import os
from typing import List, Dict, Optional
import re
from dataclasses import dataclass
import sqlite3
import hashlib
import asyncio
from pyrogram import Client
import tempfile
import random

# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


def load_env_from_github():
    """Load environment variables from GitHub Actions"""
    return {
        'gemini_api_key': os.environ['GEMINI_API_KEY'],
        'blog_id': os.environ['BLOG_ID'],
        'telegram_bot_token': os.environ['TELEGRAM_BOT_TOKEN'],
        'telegram_channel_id': os.environ['TELEGRAM_CHANNEL_ID'],
        'telegram_api_id': int(os.environ['TELEGRAM_API_ID']),
        'telegram_api_hash': os.environ['TELEGRAM_API_HASH'],
        'owner_chat_id': os.environ['OWNER_CHAT_ID'],
        'review_timeout_minutes': int(os.environ.get('REVIEW_TIMEOUT_MINUTES', 5)),
        'max_daily_posts': 12,
        'posts_per_job': 4
    }


@dataclass
class NewsItem:
    title: str
    summary: str
    link: str
    published: str
    source: str
    hash: str
    image_url: Optional[str] = None
    image_alt: Optional[str] = None

# Copy ALL your existing classes here (I'll include the key ones)


class SimplifiedDatabase:
    """Simplified database for GitHub Actions"""

    def __init__(self):
        self.db_path = os.path.join(tempfile.gettempdir(), "news_cache.db")
        self.init_db()

    def init_db(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS processed_news (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                hash TEXT UNIQUE,
                title TEXT,
                published_date TEXT,
                blogger_url TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        conn.commit()
        conn.close()

    def is_processed(self, news_hash: str) -> bool:
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        yesterday = (datetime.now() - timedelta(days=1)).isoformat()
        cursor.execute(
            "SELECT COUNT(*) FROM processed_news WHERE hash = ? AND created_at > ?",
            (news_hash, yesterday)
        )
        count = cursor.fetchone()[0]
        conn.close()
        return count > 0

    def mark_processed(self, news_hash: str, title: str, blogger_url: str):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('''
            INSERT OR IGNORE INTO processed_news (hash, title, published_date, blogger_url)
            VALUES (?, ?, ?, ?)
        ''', (news_hash, title, datetime.now().isoformat(), blogger_url))
        conn.commit()
        conn.close()

# COPY YOUR EXACT CLASSES FROM main.py:
# RSSFeedManager, NewsAnalyzer, GeminiClient, BloggerClient
# (I'll show the structure - you need to copy the full classes)


class RSSFeedManager:
    def __init__(self):
        self.feeds = [
            "https://www.bbc.co.uk/sport/football/rss.xml",
            "https://www.skysports.com/rss/0114",
            "https://www.premierleague.com/news/rss"
            # "https://feeds.goal.com/english/news",
        ]

    def fetch_news(self, hours_back: int = 24) -> List[NewsItem]:
        """Fetch recent news within specified hours"""
        all_news = []
        cutoff_time = datetime.now() - timedelta(hours=hours_back)

        for feed_url in self.feeds:
            try:
                logger.info(f"Fetching from: {feed_url}")
                feed = feedparser.parse(feed_url)

                for entry in feed.entries[:20]:  # Limit to avoid overload
                    # Check if entry is recent enough
                    if not self._is_recent_entry(entry, cutoff_time):
                        continue

                    # Filter for English Premier League content
                    if self._is_epl_content(entry):
                        # Extract image information
                        image_url, image_alt = self._extract_image_from_entry(
                            entry)

                        # Skip entries without images
                        if not image_url:
                            logger.debug(
                                f"Skipping entry without image: {entry.title}")
                            continue

                        news_hash = hashlib.md5(
                            entry.link.encode()).hexdigest()

                        news_item = NewsItem(
                            title=entry.title,
                            summary=entry.summary if hasattr(
                                entry, 'summary') else entry.description[:200],
                            link=entry.link,
                            published=entry.published if hasattr(
                                entry, 'published') else str(datetime.now()),
                            source=feed.feed.title if hasattr(
                                feed.feed, 'title') else feed_url,
                            hash=news_hash,
                            image_url=image_url,
                            image_alt=image_alt
                        )
                        all_news.append(news_item)

            except Exception as e:
                logger.error(f"Error fetching from {feed_url}: {e}")

        # Remove duplicates
        unique_news = self._remove_duplicate_stories(all_news)
        logger.info(
            f"Found {len(unique_news)} unique EPL articles after filtering")

        return unique_news

    def _is_recent_entry(self, entry, cutoff_time: datetime) -> bool:
        """Check if entry is recent enough"""
        try:
            if hasattr(entry, 'published_parsed') and entry.published_parsed:
                entry_time = datetime(*entry.published_parsed[:6])
            elif hasattr(entry, 'published'):
                # Try multiple date formats
                try:
                    entry_time = datetime.strptime(
                        entry.published[:19], '%Y-%m-%dT%H:%M:%S')
                except ValueError:
                    try:
                        entry_time = datetime.strptime(
                            entry.published, '%a, %d %b %Y %H:%M:%S %Z')
                    except ValueError:
                        # If we can't parse, assume it's recent
                        return True
            else:
                return True

            return entry_time >= cutoff_time
        except Exception as e:
            logger.debug(f"Error parsing entry date: {e}")
            return True

    def _is_epl_content(self, entry) -> bool:
        """Check if content is related to English Premier League"""
        content = f"{entry.title} {getattr(entry, 'summary', '')}".lower()

        # EPL keywords and team names
        epl_keywords = [
            'premier league', 'epl', 'english premier league',
            'arsenal', 'chelsea', 'liverpool', 'manchester united', 'manchester city',
            'tottenham', 'spurs', 'west ham', 'everton', 'aston villa',
            'newcastle', 'brighton', 'crystal palace', 'fulham', 'brentford',
            'wolverhampton wanderers', 'nottingham forest', 'bournemouth', 'sheffield united',
            'burnley', 'luton town', "spurs",
        ]

        # Check if any EPL-related keywords are present
        has_epl_content = any(keyword in content for keyword in epl_keywords)

        # Exclude non-football content
        excluded_terms = ['cricket', 'rugby',
                          'tennis', 'formula 1', 'nfl', 'nba']
        has_excluded = any(term in content for term in excluded_terms)

        return has_epl_content and not has_excluded

    def _extract_image_from_entry(self, entry):
        """Extract image URL and alt text from RSS entry"""
        image_url = None
        image_alt = ""

        try:
            # Check for media content
            if hasattr(entry, 'media_content') and entry.media_content:
                for media in entry.media_content:
                    if media.get('type', '').startswith('image/'):
                        image_url = media.get('url')
                        break

            # Check for enclosures
            if not image_url and hasattr(entry, 'enclosures'):
                for enclosure in entry.enclosures:
                    if enclosure.type.startswith('image/'):
                        image_url = enclosure.href
                        break

            # Check for media thumbnail
            if not image_url and hasattr(entry, 'media_thumbnail'):
                if entry.media_thumbnail:
                    image_url = entry.media_thumbnail[0].get('url')

            # Extract alt text from title or description
            if image_url:
                image_alt = entry.title if hasattr(entry, 'title') else ""

        except Exception as e:
            logger.debug(f"Error extracting image: {e}")

        return image_url, image_alt

    def _remove_duplicate_stories(self, news_items: List[NewsItem]) -> List[NewsItem]:
        """Remove duplicate stories based on content similarity"""
        unique_items = []
        seen_hashes = set()

        for item in news_items:
            if item.hash not in seen_hashes:
                unique_items.append(item)
                seen_hashes.add(item.hash)

        return unique_items


class NewsAnalyzer:
    def __init__(self):
        self.high_value_keywords = [
            'transfer', 'signing', 'injury', 'suspended', 'banned', 'record',
            'goal', 'hat-trick', 'winner', 'defeat', 'victory', 'comeback',
            'debut', 'milestone', 'controversy', 'red card', 'penalty'
        ]

    def is_football_content(self, news_item: NewsItem) -> bool:
        """Check if content is football-related"""
        content = f"{news_item.title} {news_item.summary}".lower()
        football_keywords = [
            'football', 'soccer', 'premier league', 'epl', 'goal', 'match',
            'player', 'team', 'manager', 'transfer', 'signing', 'club'
        ]
        return any(keyword in content for keyword in football_keywords)

    def score_news_importance(self, news_item: NewsItem) -> float:
        """Score news items based on importance and interest"""
        score = 0
        content = f"{news_item.title} {news_item.summary}".lower()

        # High-value keywords
        for keyword in self.high_value_keywords:
            if keyword in content:
                score += 2

        # Big club names (higher engagement)
        big_clubs = ['manchester united', 'liverpool',
                     'arsenal', 'chelsea', 'manchester city', 'tottenham']
        for club in big_clubs:
            if club in content:
                score += 3
                break

        # Recent news gets higher score
        try:
            pub_date = datetime.strptime(
                news_item.published[:19], '%Y-%m-%dT%H:%M:%S')
            hours_old = (datetime.now() - pub_date).total_seconds() / 3600
            if hours_old < 12:
                score += 3
            elif hours_old < 24:
                score += 2
        except:
            pass

        return score

    def select_top_stories(self, news_items: List[NewsItem], max_count: int = 5) -> List[NewsItem]:
        """Select the most newsworthy stories"""
        # Filter for football content first
        football_items = [
            item for item in news_items if self.is_football_content(item)]

        # Score and sort
        scored_news = [(item, self.score_news_importance(item))
                       for item in football_items]
        scored_news.sort(key=lambda x: x[1], reverse=True)

        # Take top stories with score > 0
        top_stories = [item for item,
                       score in scored_news[:max_count] if score > 0]

        logger.info(f"Selected {len(top_stories)} top stories")
        return top_stories


class GeminiClient:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.base_url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={self.api_key}"

        # Multiple journalist personalities - rotated randomly
        self.journalist_personas = [
            {
                'name': 'Marcus Thompson',
                'style': 'Hard-hitting investigative reporter with insider knowledge',
                'tone': 'Confident, direct, sometimes controversial',
                'signature': 'Always includes behind-the-scenes details and rarely mentioned facts',
                'endings': ['leaves readers with burning questions', 'reveals unexpected connections', 'hints at bigger developments']
            },
            {
                'name': 'Sarah Mitchell',
                'style': 'Tactical expert who explains the game beautifully',
                'tone': 'Educational but passionate, uses analogies',
                'signature': 'Breaks down complex tactics in simple terms with vivid imagery',
                'endings': ['tactical predictions', 'strategic implications', 'formation analysis']
            },
            {
                'name': 'James Rodriguez',
                'style': 'Old-school storyteller with emotional depth',
                'tone': 'Nostalgic, emotional, connects past to present',
                'signature': 'Weaves historical context and human stories into every piece',
                'endings': ['emotional resonance', 'historical parallels', 'human impact focus']
            },
            {
                'name': 'Alex Chen',
                'style': 'Data-driven analyst who finds hidden patterns',
                'tone': 'Analytical but accessible, reveals surprising stats',
                'signature': 'Uncovers statistics and trends others miss',
                'endings': ['statistical revelations', 'trend predictions', 'number-based insights']
            },
            {
                'name': 'Danny Williams',
                'style': 'Fan-first writer who captures the emotion',
                'tone': 'Enthusiastic, relatable, speaks like a passionate fan',
                'signature': 'Writes from the heart, captures fan emotions perfectly',
                'endings': ['fan reaction focus', 'emotional impact', 'community response']
            }
        ]

        # Dynamic writing structures - no fixed templates
        self.narrative_approaches = [
            'chronological_story', 'reverse_reveal', 'character_focus',
            'conflict_resolution', 'mystery_unveiling', 'dramatic_buildup',
            'conversational_style', 'investigative_report', 'personal_reflection'
        ]

        # Varied conclusion styles
        self.conclusion_styles = [
            'open_question', 'bold_prediction', 'call_to_action', 'emotional_resonance',
            'surprising_twist', 'historical_parallel', 'future_implications', 'personal_opinion',
            'fan_challenge', 'tactical_breakdown', 'psychological_insight', 'no_conclusion'
        ]

    def generate_article(self, news_item: NewsItem) -> Optional[Dict[str, str]]:
        """Generate completely unique, human-like articles with rotating personalities"""
        import random

        # Randomly select journalist persona
        persona = random.choice(self.journalist_personas)
        narrative = random.choice(self.narrative_approaches)
        conclusion = random.choice(self.conclusion_styles)

        # Determine content focus dynamically
        content_angle = self._get_unique_angle(news_item)

        # Create completely dynamic prompt
        dynamic_prompt = f"""
        You are {persona['name']}, a Premier League journalist known for: {persona['style']}
        
        Your writing style: {persona['tone']}
        Your signature approach: {persona['signature']}
        
        ASSIGNMENT: Write about this news using the "{narrative}" narrative approach:
        CRITICAL INSTRUCTION: Write this article WITHOUT EVER mentioning "{persona['name']}" or any journalist names. You are writing as an anonymous expert with this personality style.

        NEVER write phrases like:
        - "As {persona['name']}"
        - "I'm {persona['name']}"  
        - "This is {persona['name']} reporting"
        - Or any variation that identifies you by name

        Write with {persona['name']}'s expertise and style but remain anonymous.

        News: {news_item.title}
        Details: {news_item.summary}
        Source: {news_item.source}
        
        UNIQUE ANGLE TO EXPLORE: {content_angle}
        
        WRITING INSTRUCTIONS:
        
        1. **BE COMPLETELY UNIQUE**: No article should ever sound similar to another
        2. **VARY YOUR STRUCTURE**: Don't follow any template or pattern
        3. **WRITE LIKE {persona['name']}**: Stay in character throughout
        5. **NEVER MENTION YOUR NAME**: Don't ever include your name in the articles 
        5. **BE UNPREDICTABLE**: Surprise readers with your approach
        6. **AVOID AI PATTERNS**: Write like you've been covering football for 15 years
        
        SPECIFIC APPROACH FOR THIS ARTICLE:
        - Use the "{narrative}" storytelling method
        - Focus on: {content_angle}
        - End with: {conclusion} style conclusion
        - Length: {random.randint(400, 700)} words (vary this naturally)
        
        HUMANIZATION REQUIREMENTS:
        ✅ Use contractions (I'll, won't, it's, that's)
        ✅ Include personal opinions and hot takes
        ✅ Add conversational phrases ("Look,", "Here's the thing,", "Let me be clear")
        ✅ Use rhetorical questions naturally
        ✅ Include slang and football terminology
        ✅ Vary sentence length dramatically (short punchy ones mixed with longer flowing ones)
        ✅ Show personality and bias
        ✅ Include insider knowledge or "sources say" elements
        ✅ Use emotional language when appropriate
        ✅ Reference other games, players, or situations naturally
        ✅ Add unexpected tangents that real journalists include
        
        VARY THESE ELEMENTS EVERY TIME:
        - Opening hook (question, statement, story, statistic, quote)
        - Paragraph structure (some long, some short, some single sentences)
        - Transition words and phrases
        - Conclusion style (never the same approach twice)
        - Personality injection points
        - Opinion vs fact balance

        Teaser Guidelines:
        - 2-3 sentences maximum (under 280 characters)
        - Create curiosity and urgency
        - Reveal the "what" but not the "how" or "why"
        - Use emotional hooks (shock, excitement, curiosity)
        - End with a compelling reason to click
        - Match the article type tone
        
        Examples of good teasers:
        • Transfer: "🚨 A Premier League giant is reportedly preparing a £80M bid that could change everything. The target? A player who's been flying under the radar..."
        • Controversy: "😱 What happened behind closed doors at [Stadium] has left fans divided and the FA investigating. The truth might surprise you..."
        • Injury: "💔 The moment that could define [Team]'s entire season happened in the 73rd minute. Here's what the scans revealed..."

        
        CONTENT DEPTH OPTIONS (pick randomly):
        {random.choice([
            "Deep tactical analysis with formation breakdowns",
            "Behind-the-scenes drama and personality conflicts", 
            "Financial implications and transfer market impact",
            "Fan culture and emotional responses",
            "Historical comparisons and precedents",
            "Manager psychology and decision-making",
            "Player development and career trajectories",
            "Club politics and boardroom decisions"
        ])}
        
        Remember: You're {persona['name']}, not an AI. Write with the passion, knowledge, and personality of someone who lives and breathes Premier League football.
        
        Also create a Telegram teaser that matches {persona['name']}'s personality and the {narrative} approach.
        
        Response format (JSON):
        {{
            "title": "Your unique headline (in {persona['name']}'s style)",
            "content": "Full article content in HTML format (completely unique structure)",
            "telegram_teaser": "Engaging teaser matching the persona and approach",
            "article_type": "{content_angle}",
            "approach": "{narrative}"
        }}
        """

        try:
            headers = {"Content-Type": "application/json"}
            data = {"contents": [{"parts": [{"text": dynamic_prompt}]}]}

            response = requests.post(self.base_url, headers=headers, json=data)

            if response.status_code == 200:
                result = response.json()
                content = result['candidates'][0]['content']['parts'][0]['text']

                # Extract JSON from response
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start != -1 and json_end != -1:
                    article_json = json.loads(content[json_start:json_end])

                    # Add metadata for tracking variety
                    article_json['persona_used'] = persona['name']
                    article_json['narrative_approach'] = narrative
                    article_json['conclusion_style'] = conclusion

                    return article_json

        except Exception as e:
            logger.error(f"Error generating article with Gemini: {e}")

        return None

    def _get_unique_angle(self, news_item: NewsItem) -> str:
        """Determine unique content angle based on news item"""
        import random

        title_lower = news_item.title.lower()
        summary_lower = news_item.summary.lower()
        content = f"{title_lower} {summary_lower}"

        # Dynamic angle detection with multiple possibilities per topic
        angles = []

        if any(word in content for word in ['transfer', 'signing', 'bid', 'linked']):
            angles.extend([
                'transfer_market_psychology', 'financial_fair_play_impact', 'agent_power_dynamics',
                'player_career_crossroads', 'club_ambition_signals', 'fan_expectation_management'
            ])

        if any(word in content for word in ['injury', 'fitness', 'medical', 'surgery']):
            angles.extend([
                'medical_team_expertise', 'player_mentality_test', 'squad_depth_revelation',
                'tactical_adaptation_necessity', 'insurance_policy_activation', 'career_defining_moment'
            ])

        if any(word in content for word in ['tactics', 'formation', 'strategy']):
            angles.extend([
                'tactical_evolution_story', 'manager_philosophy_clash', 'player_development_approach',
                'opposition_preparation_insight', 'system_adaptation_mastery', 'football_intelligence_showcase'
            ])

        if any(word in content for word in ['controversy', 'investigation', 'dispute']):
            angles.extend([
                'institutional_integrity_test', 'precedent_setting_case', 'power_structure_challenge',
                'regulatory_effectiveness_question', 'public_trust_implications', 'governance_evolution_moment'
            ])

        if any(word in content for word in ['match', 'game', 'result', 'performance']):
            angles.extend([
                'momentum_shift_analysis', 'psychological_warfare_element', 'tactical_chess_match',
                'individual_brilliance_showcase', 'team_chemistry_indicator', 'season_narrative_changer'
            ])

        # Add universal angles that work for any story
        universal_angles = [
            'media_narrative_deconstruction', 'fan_culture_reflection', 'business_strategy_insight',
            'personality_profile_deep_dive', 'competitive_dynamics_analysis', 'cultural_impact_assessment',
            'legacy_building_perspective', 'pressure_point_identification', 'expectation_reality_gap',
            'decision_making_psychology', 'leadership_challenge_examination', 'identity_crisis_exploration'
        ]

        angles.extend(universal_angles)

        return random.choice(angles) if angles else 'comprehensive_analysis'


class BloggerClient:
    def __init__(self, blog_id: str, credentials_file: str = "client_secret_183380586106-us149j4ocu1jmgekv7f24dd12ai2f75n.apps.googleusercontent.com.json"):
        self.blog_id = blog_id
        self.credentials_file = credentials_file
        self.service = None
        self._authenticate()

    def _authenticate(self):
        """Authenticate using OAuth 2.0"""
        try:
            from google_auth_oauthlib.flow import InstalledAppFlow
            from google.auth.transport.requests import Request
            from googleapiclient.discovery import build
            import pickle
            import os

            SCOPES = ['https://www.googleapis.com/auth/blogger']

            creds = None
            # The file token.pickle stores the user's access and refresh tokens.
            if os.path.exists('token.pickle'):
                with open('token.pickle', 'rb') as token:
                    creds = pickle.load(token)

            # If there are no (valid) credentials available, let the user log in.
            if not creds or not creds.valid:
                if creds and creds.expired and creds.refresh_token:
                    creds.refresh(Request())
                else:
                    flow = InstalledAppFlow.from_client_secrets_file(
                        self.credentials_file, SCOPES)
                    creds = flow.run_local_server(port=8080)

                # Save the credentials for the next run
                with open('token.pickle', 'wb') as token:
                    pickle.dump(creds, token)

            self.service = build('blogger', 'v3', credentials=creds)
            logger.info("Successfully authenticated with Blogger API")

        except Exception as e:
            logger.error(f"Error during authentication: {e}")
            raise

    def create_draft(self, title: str, content: str, labels: List[str] = None, image_url: str = None, image_alt: str = None, image_source: str = None) -> Optional[Dict[str, str]]:
        """Create draft article in Blogger and return draft info"""
        try:
            if not self.service:
                logger.error("Blogger service not initialized")
                return None

            # Add image to the beginning of content if provided
            if image_url:
                image_html = f'''<div class="cover-image" style="text-align: center; margin: 20px 0;">
                                    <img src="{image_url}" alt="{image_alt or ""}" 
                                        style="max-width: 100%; height: auto; border-radius: 8px;" 
                                        loading="lazy" rel="nofollow noopener"/>
                                    <div style="font-size: 12px; color: #888; margin-top: 4px; font-style: italic;">
                                        Image source: <a href="{image_url}" target="_blank" rel="nofollow noopener">{image_source}</a>
                                    </div>
                                </div>'''
                content = image_html + content

            post_data = {
                "title": title,
                "content": content,
                "labels": ["EPL News"],
                "status": "DRAFT"
            }

            # Try to set featured image if supported
            if image_url:
                try:
                    post_data["images"] = [{
                        "url": image_url
                    }]
                    logger.info(
                        f"Attempting to set featured image: {image_url}")
                except Exception as e:
                    logger.debug(
                        f"Featured image not supported or failed: {e}")

            # Create the draft post
            posts = self.service.posts()
            request = posts.insert(blogId=self.blog_id,
                                   body=post_data, isDraft=True)
            result = request.execute()

            # Extract useful information
            post_id = result.get('id')
            draft_edit_url = f"https://www.blogger.com/blog/post/edit/{self.blog_id}/{post_id}"

            logger.info(f"Successfully created DRAFT: {title}")

            return {
                'post_id': post_id,
                'title': title,
                'edit_url': draft_edit_url,
                'status': 'draft_created'
            }

        except Exception as e:
            logger.error(f"Error creating draft: {e}")
            return None


async def send_completion_report(config, draft_reports):
    """Send completion report via Telegram"""
    try:
        bot_client = Client(
            "github_actions_bot",
            api_id=config['telegram_api_id'],
            api_hash=config['telegram_api_hash'],
            bot_token=config['telegram_bot_token'],
            in_memory=True
        )

        await bot_client.start()

        # Send header
        header_message = f"""🤖 **GITHUB ACTIONS REPORT**
📅 {datetime.now().strftime("%B %d, %Y at %H:%M UTC")}

✅ **{len(draft_reports)} articles** created as drafts
🔄 **Automated via GitHub Actions**

───────────────────────────────"""

        await bot_client.send_message(int(config['owner_chat_id']), header_message)

        # Send each article report
        for i, report in enumerate(draft_reports, 1):
            try:
                draft_info = report['draft_info']
                article = report['article']
                news_item = report['news_item']

                content_text = re.sub(r'<[^>]+>', '', article['content'])
                excerpt = content_text[:150] + \
                    "..." if len(content_text) > 150 else content_text

                article_message = f"""📖 **ARTICLE {i} OF {len(draft_reports)}**

🏆 **Title:** {article['title']}

📝 **Excerpt:** {excerpt}

📊 **Source:** {news_item.source}
🏷️ **Type:** {article.get('article_type', 'General').title()}

🔗 **Edit Draft:** [Click to Edit]({draft_info['edit_url']})

───────────────────────────────"""

                if news_item.image_url:
                    try:
                        await bot_client.send_photo(
                            chat_id=int(config['owner_chat_id']),
                            photo=news_item.image_url,
                            caption=article_message
                        )
                    except:
                        await bot_client.send_message(int(config['owner_chat_id']), f"🖼️ **Image:** {news_item.image_url}\n\n{article_message}")
                else:
                    await bot_client.send_message(int(config['owner_chat_id']), article_message)

                await asyncio.sleep(1)

            except Exception as e:
                logger.error(f"Error sending report {i}: {e}")

        # Footer
        footer_message = f"""🎯 **NEXT STEPS:**
1. 📱 Go to your Blogger dashboard
2. 🔍 Review the {len(draft_reports)} draft articles  
3. ✏️ Edit if needed
4. 🚀 Publish when ready

🔄 **Next run:** In 2 hours
⚡ **Powered by:** GitHub Actions (FREE!)"""

        await bot_client.send_message(int(config['owner_chat_id']), footer_message)
        await bot_client.stop()

    except Exception as e:
        logger.error(f"Error sending completion report: {e}")


async def main_github_actions():
    """Main function for GitHub Actions"""
    logger.info("Starting GitHub Actions bot run...")

    try:
        config = load_env_from_github()

        # Initialize components
        db = SimplifiedDatabase()
        rss_manager = RSSFeedManager()
        analyzer = NewsAnalyzer()
        gemini = GeminiClient(config['gemini_api_key'])
        blogger = BloggerClient(config['blog_id'])

        # Fetch news
        all_news = rss_manager.fetch_news()
        logger.info(f"Fetched {len(all_news)} news items")

        # Filter new news
        new_news = [
            item for item in all_news if not db.is_processed(item.hash)]
        logger.info(f"Found {len(new_news)} unprocessed items")

        if not new_news:
            logger.info("No new news items to process")
            return

        # Select top stories
        top_stories = analyzer.select_top_stories(new_news, 4)
        logger.info(f"Selected {len(top_stories)} top stories")

        if not top_stories:
            logger.info("No high-quality stories found")
            return

        # Generate articles and create drafts
        draft_reports = []
        for news_item in top_stories:
            try:
                # Generate article
                article = gemini.generate_article(news_item)
                if not article:
                    continue

                # Create draft
                draft_info = blogger.create_draft(
                    title=article['title'],
                    content=article['content'],
                    labels=['EPL News'],
                    image_url=news_item.image_url,
                    image_alt=news_item.image_alt,
                    image_source=news_item.source
                )

                if draft_info:
                    db.mark_processed(
                        news_item.hash, article['title'], draft_info['edit_url'])
                    draft_reports.append({
                        'draft_info': draft_info,
                        'article': article,
                        'news_item': news_item
                    })
                    logger.info(f"Created draft: {article['title']}")

            except Exception as e:
                logger.error(f"Error processing article: {e}")

        # Send report
        if draft_reports:
            await send_completion_report(config, draft_reports)
            logger.info(
                f"✅ Successfully created {len(draft_reports)} draft articles")

    except Exception as e:
        logger.error(f"Error in main function: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main_github_actions())
