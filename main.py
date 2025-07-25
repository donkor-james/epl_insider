import uuid  # Add this import at the top
import feedparser
import requests
import json
import schedule
import time
import logging
from datetime import datetime, timedelta
import os
from typing import List, Dict, Optional
import re
from dataclasses import dataclass
import sqlite3
import hashlib
import asyncio
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO
import urllib.parse
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import tweepy  # X (Twitter) API

# Load environment variables from .env file


def load_env_file():
    """Load environment variables from .env file"""
    env_path = '.env'
    if os.path.exists(env_path):
        with open(env_path, 'r') as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith('#') and '=' in line:
                    key, value = line.split('=', 1)
                    os.environ[key.strip()] = value.strip()


def replace_caption_text(text: str) -> str:
    """Replace 'Follow @premierleagueinsider' with 'Follow Us' in the given text."""
    if not text:
        return ""
    return text.replace("Follow @premierleagueinsider", "Follow Us")


# Load environment variables
load_env_file()


# Configure logging
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


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
    full_text: Optional[str] = None  # <-- Add this field


class ArticleReviewSystem:
    def __init__(self, review_timeout_minutes: int = 30):
        self.pending_articles_file = "pending_articles.json"
        self.review_timeout = review_timeout_minutes * 60  # Convert to seconds
        self.pending_articles = {}
        self.load_pending_articles()

    def load_pending_articles(self):
        """Load pending articles from file"""
        try:
            if os.path.exists(self.pending_articles_file):
                with open(self.pending_articles_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.pending_articles = data
        except Exception as e:
            logger.warning(f"Could not load pending articles: {e}")
            self.pending_articles = {}

    def save_pending_articles(self):
        """Save pending articles to file"""
        try:
            with open(self.pending_articles_file, 'w', encoding='utf-8') as f:
                json.dump(self.pending_articles, f,
                          indent=2, ensure_ascii=False)
        except Exception as e:
            logger.error(f"Could not save pending articles: {e}")

    def add_pending_article(self, news_item, generated_article):
        """Add article to pending review list"""
        article_id = str(uuid.uuid4())[:8]  # Short ID for easy typing

        pending_item = {
            'id': article_id,
            'timestamp': datetime.now().isoformat(),
            'news_item': {
                'title': news_item.title,
                'summary': news_item.summary,
                'link': news_item.link,
                'published': news_item.published,
                'source': news_item.source,
                'hash': news_item.hash,
                'image_url': news_item.image_url,
                'image_alt': news_item.image_alt
            },
            'generated_article': generated_article,
            'status': 'pending'
        }

        self.pending_articles[article_id] = pending_item
        self.save_pending_articles()
        return article_id

    def get_pending_articles(self):
        """Get all pending articles"""
        return {k: v for k, v in self.pending_articles.items() if v['status'] == 'pending'}

    def approve_articles(self, article_ids: list):
        """Mark articles as approved"""
        approved = []
        for article_id in article_ids:
            if article_id in self.pending_articles and self.pending_articles[article_id]['status'] == 'pending':
                self.pending_articles[article_id]['status'] = 'approved'
                approved.append(article_id)

        self.save_pending_articles()
        return approved

    def get_expired_articles(self):
        """Get articles that have exceeded timeout"""
        expired = []
        current_time = datetime.now()

        for article_id, article in self.pending_articles.items():
            if article['status'] == 'pending':
                article_time = datetime.fromisoformat(article['timestamp'])
                if (current_time - article_time).total_seconds() > self.review_timeout:
                    expired.append(article_id)

        return expired

    def auto_approve_expired(self):
        """Auto-approve expired articles"""
        expired_ids = self.get_expired_articles()
        if expired_ids:
            logger.info(
                f"Auto-approving {len(expired_ids)} expired articles: {expired_ids}")
            return self.approve_articles(expired_ids)
        return []

    def remove_article(self, article_id: str):
        """Remove article from pending list"""
        if article_id in self.pending_articles:
            del self.pending_articles[article_id]
            self.save_pending_articles()

    def clear_old_articles(self, days_old: int = 1):
        """Clear articles older than specified days"""
        cutoff_time = datetime.now() - timedelta(days=days_old)
        to_remove = []

        for article_id, article in self.pending_articles.items():
            article_time = datetime.fromisoformat(article['timestamp'])
            if article_time < cutoff_time:
                to_remove.append(article_id)

        for article_id in to_remove:
            self.remove_article(article_id)

        if to_remove:
            logger.info(f"Cleared {len(to_remove)} old articles")

    def skip_all_pending(self):
        """Mark all pending articles as skipped"""
        skipped = []
        for article_id, article in self.pending_articles.items():
            if article['status'] == 'pending':
                article['status'] = 'skipped'
                skipped.append(article_id)

        self.save_pending_articles()
        return skipped


class NewsDatabase:
    def __init__(self, db_path: str = "news.db"):
        self.db_path = db_path
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
        cursor.execute(
            "SELECT COUNT(*) FROM processed_news WHERE hash = ?", (news_hash,))
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


class RSSFeedManager:
    def __init__(self):
        self.feeds = [
            "https://www.bbc.co.uk/sport/football/rss.xml",
            "https://www.skysports.com/rss/0114",
            "https://www.premierleague.com/news/rss"
        ]

    def fetch_news(self, hours_back: int = 24) -> List[NewsItem]:
        """Fetch recent news within specified hours"""
        all_news = []
        cutoff_time = datetime.now() - timedelta(hours=hours_back)

        feed_names = {
            "https://www.bbc.co.uk/sport/football/rss.xml": "BBC Football",
            "https://www.skysports.com/rss/0114": "Sky Sports Football",
            "https://www.premierleague.com/news/rss": "Premier League Official"
        }

        for feed_url in self.feeds:
            try:
                feed_name = feed_names.get(feed_url, feed_url)
                feed = feedparser.parse(feed_url)
                epl_entries = []

                for entry in feed.entries[:20]:  # Limit to avoid overload
                    if not self._is_recent_entry(entry, cutoff_time):
                        continue
                    if self._is_epl_content(entry):
                        image_url, image_alt = self._extract_image_from_entry(
                            entry)
                        if not image_url:
                            logger.debug(
                                f"Skipping entry without image: {entry.title}")
                            continue
                        news_hash = hashlib.md5(
                            entry.link.encode()).hexdigest()

                        full_text = self._scrape_full_article_text(entry.link)
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
                            image_alt=image_alt,
                            full_text=full_text
                        )
                        epl_entries.append(news_item)
                        all_news.append(news_item)

                # Log summary for this feed
                logger.info(
                    f"[{feed_name}] EPL-relevant stories: {len(epl_entries)}")
                for item in epl_entries:
                    logger.info(f"[{feed_name}] - {item.title}")
                if not epl_entries:
                    logger.info(
                        f"[{feed_name}] No EPL-relevant stories found.")

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
            'burnley', 'luton town', "spurs", "fa cup"
        ]

        # Check if any EPL-related keywords are present
        has_epl_content = any(keyword in content for keyword in epl_keywords)

        # Exclude non-football content
        excluded_terms = ['cricket', 'rugby',
                          'tennis', 'formula 1', 'nfl', 'nba']
        has_excluded = any(term in content for term in excluded_terms)

        return has_epl_content and not has_excluded

    def _scrape_main_image_from_url(self, url):
        """Try to extract the main image from the article's HTML page."""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; PremierLeagueBot/1.0)"
            }
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")

            # Try Open Graph image first
            og_image = soup.find("meta", property="og:image")
            if og_image and og_image.get("content"):
                return og_image["content"]

            # Try Twitter Card image
            twitter_image = soup.find("meta", property="twitter:image")
            if twitter_image and twitter_image.get("content"):
                return twitter_image["content"]

            # Try first large <img> in the article
            for img in soup.find_all("img"):
                src = img.get("src")
                if src and (src.startswith("http") or src.startswith("//")):
                    # Optionally, filter out very small images
                    try:
                        if img.get("width") and int(img.get("width")) < 100:
                            continue
                    except Exception:
                        pass
                    return src

        except Exception as e:
            logger.warning(f"Could not scrape image from {url}: {e}")
        return None

    def _extract_image_from_entry(self, entry):
        """Extract image URL and alt text from RSS entry, or scrape from article page if missing."""
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

            if not image_url and hasattr(entry, 'link'):
                scraped_image = self._scrape_main_image_from_url(entry.link)
                if scraped_image:
                    image_url = scraped_image

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

    def _scrape_full_article_text(self, url):
        """Scrape the main article text from the article's HTML page."""
        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (compatible; PremierLeagueBot/1.0)"
            }
            resp = requests.get(url, headers=headers, timeout=10)
            if resp.status_code != 200:
                return None

            soup = BeautifulSoup(resp.text, "html.parser")

            # Try to find main content by common tags/classes
            article_tags = soup.find_all(['article'])
            if article_tags:
                text = ' '.join(tag.get_text(separator=' ', strip=True)
                                for tag in article_tags)
                if len(text) > 300:
                    return text

            # Fallback: get all <p> tags inside main content
            paragraphs = soup.find_all('p')
            text = ' '.join(p.get_text(separator=' ', strip=True)
                            for p in paragraphs)
            return text if len(text) > 300 else None
        except Exception as e:
            logger.warning(
                f"Could not scrape full article text from {url}: {e}")
        return None

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
            'transfer', 'signing', 'sign', 'injury', 'suspended', 'banned', 'record',
            'goal', 'hat-trick', 'winner', 'defeat', 'victory', 'comeback',
            'debut', 'milestone', 'controversy', 'red card', 'penalty', 'VAR', 'epl'
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
                     'arsenal', 'chelsea', 'manchester city', 'tottenham', 'newcastle']
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

        # Legitimate analysis approaches - no fake personas
        self.analysis_styles = [
            {
                'approach': 'tactical_breakdown',
                'focus': 'Formation analysis, playing style, and strategic fit',
                'tone': 'Educational and detailed, using football terminology',
                'signature': 'Breaks down complex tactics with clear explanations',
                'data_sources': 'Formation data, playing statistics, tactical trends'
            },
            {
                'approach': 'market_analysis',
                'focus': 'Transfer values, financial implications, and market trends',
                'tone': 'Business-focused and data-driven',
                'signature': 'Analyzes deals within broader market context',
                'data_sources': 'Transfer fees, market values, financial reports'
            },
            {
                'approach': 'performance_analysis',
                'focus': 'Statistical review and performance metrics',
                'tone': 'Analytical and evidence-based',
                'signature': 'Uses statistics to predict impact and success',
                'data_sources': 'Performance stats, historical data, comparative analysis'
            },
            {
                'approach': 'historical_context',
                'focus': 'Comparing to past situations and precedents',
                'tone': 'Contextual and comparative',
                'signature': 'Draws parallels with similar past events',
                'data_sources': 'Historical records, past transfers, archived data'
            },
            {
                'approach': 'strategic_impact',
                'focus': 'Long-term implications for club strategy',
                'tone': 'Forward-thinking and strategic',
                'signature': 'Analyzes broader implications for club direction',
                'data_sources': 'Club strategies, competitive analysis, trend data'
            }
        ]

        # Legitimate analytical angles only
        self.content_angles = {
            'transfer_analysis': [
                'tactical_system_fit',
                'market_value_assessment',
                'squad_depth_impact',
                'age_profile_implications',
                'positional_competition_analysis',
                'contract_structure_implications'
            ],
            'performance_review': [
                'statistical_trend_analysis',
                'comparative_performance_study',
                'consistency_evaluation',
                'key_metrics_breakdown',
                'seasonal_progression_tracking'
            ],
            'tactical_analysis': [
                'formation_compatibility_study',
                'playing_style_integration',
                'tactical_flexibility_assessment',
                'system_evolution_implications',
                'positional_role_analysis'
            ],
            'injury_analysis': [
                'squad_rotation_implications',
                'tactical_adjustment_requirements',
                'depth_chart_reshuffling',
                'opportunity_cost_analysis',
                'recovery_timeline_impact'
            ],
            'general_analysis': [
                'competitive_landscape_assessment',
                'season_trajectory_implications',
                'momentum_shift_evaluation',
                'pressure_point_identification',
                'expectation_reality_analysis'
            ]
        }

        # Varied writing structures for natural variety
        self.narrative_structures = [
            'data_first_approach',  # Lead with statistics
            'tactical_breakdown_focus',  # Formation/system analysis
            'comparative_analysis',  # Compare to similar situations
            'chronological_development',  # Track progression over time
            'impact_assessment',  # Focus on implications
            'problem_solution_format',  # Identify issue and analyze solution
            'trend_analysis_approach',  # Broader pattern recognition
            'contextual_deep_dive'  # Historical and market context
        ]

        # Professional conclusion styles
        self.conclusion_approaches = [
            'prediction_based_on_data',
            'key_factors_to_monitor',
            'comparative_outlook',
            'timeline_expectations',
            'success_metrics_identification',
            'broader_implications_summary',
            'analytical_verdict',
            'trend_continuation_assessment'
        ]

    def generate_article(self, news_item: NewsItem) -> Optional[Dict[str, str]]:
        """Generate legitimate analytical articles with varied approaches"""
        import random

        # Select analysis approach
        style = random.choice(self.analysis_styles)
        structure = random.choice(self.narrative_structures)
        conclusion = random.choice(self.conclusion_approaches)

        # Determine content angle based on news type
        content_angle = self._get_analytical_angle(news_item)
        article_text = news_item.full_text if news_item.full_text else news_item.summary

        # Create analytical prompt
        analytical_prompt = f"""
        You are a professional football analyst writing an expert analysis piece.
        
        ANALYSIS APPROACH: {style['approach']}
        Writing Focus: {style['focus']}
        Tone: {style['tone']}
        Signature Style: {style['signature']}
        Data Sources to Reference: {style['data_sources']}
        
        NEWS TO ANALYZE:
        Title: {news_item.title}
        Article: {article_text}
        Source: {news_item.source}
        
        ANALYTICAL ANGLE: {content_angle}
        NARRATIVE STRUCTURE: {structure}
        CONCLUSION STYLE: {conclusion}
        
        CRITICAL REQUIREMENTS:
        
        🔴 NEVER FABRICATE:
        - Do NOT claim insider sources or "sources say"
        - Do NOT invent quotes or conversations
        - Do NOT make up behind-the-scenes drama
        - Do NOT create fictional details
        
        ✅ LEGITIMATE ANALYSIS ONLY:
        - Base all analysis on publicly available information
        - Use phrases like "The data suggests...", "Analysis indicates...", "This pattern shows..."
        - Reference observable statistics, historical records, public statements
        - Compare to similar documented cases
        - Draw conclusions from verifiable trends
        
        CONTENT REQUIREMENTS:
        
        1. **Statistical Foundation**: Include relevant performance metrics, transfer values, or tactical data
        
        2. **Historical Context**: Reference similar past situations or precedents where applicable
        
        3. **Tactical Analysis**: Explain formations, playing styles, or strategic implications using football knowledge
        
        4. **Market Context**: Position within broader transfer market or competitive landscape
        
        5. **Evidence-Based Predictions**: Make forecasts based on observable patterns, not speculation
        
        WRITING STYLE GUIDELINES:
        
        ✅ Professional but engaging tone
        ✅ Use football terminology appropriately  
        ✅ Vary sentence length naturally
        ✅ Include rhetorical questions for engagement
        ✅ Use analytical transitions ("However, the data shows...", "In contrast to...", "This suggests...")
        ✅ Reference specific examples from football history
        ✅ Explain complex concepts clearly
        ✅ Show expertise through detailed knowledge
        ✅ Use contractions for natural flow
        ✅ Include opinion based on analysis, not fabrication
        
        STRUCTURE FOR THIS ARTICLE:
        - Use the "{structure}" narrative approach
        - Focus analytical lens on: {content_angle}
        - Conclude with: {conclusion} style ending
        - Target length: {random.randint(450, 650)} words
        
        SPECIFIC ANALYTICAL DEPTH:
        Focus your analysis on: {random.choice([
            'Statistical performance patterns and trend analysis',
            'Tactical system integration and formation implications', 
            'Market positioning and financial strategy assessment',
            'Historical precedent comparison and outcome prediction',
            'Competitive advantage analysis and strategic impact',
            'Performance metrics evaluation and success probability',
            'Timeline analysis and development trajectory mapping',
            'Risk assessment and potential challenge identification'
        ])}
        
        TEASER REQUIREMENTS:
        Create a professional, analytical teaser that:
        - Highlights the key analytical insight (2-3 sentences, under 280 characters)
        - Uses analytical language ("Analysis reveals...", "Data shows...", "Key insight:")
        - Focuses on the most interesting finding or implication
        - Maintains professional tone while being engaging
        - Ends with intrigue about the full analysis
        - NO drama or gossip language
        - NO fake insider claims
        
        Example analytical teasers:
        • "Analysis reveals this transfer could reshape the entire tactical approach. The numbers tell a fascinating story about long-term strategy."
        • "Key insight: Performance data suggests a major shift in playing style is imminent. What the metrics really show might surprise you."
        • "Market analysis indicates this deal represents more than just a signing. The strategic implications run deeper than expected."
        
        Response format (JSON):
        {{
            "title": "Professional analytical headline",
            "content": "Full analytical article in HTML format with evidence-based insights",
            "telegram_teaser": "Professional analytical teaser focusing on key insight",
            "article_type": "{content_angle}",
            "analysis_approach": "{style['approach']}",
            "structure_used": "{structure}"
        }}
        """

        try:
            headers = {"Content-Type": "application/json"}
            data = {"contents": [{"parts": [{"text": analytical_prompt}]}]}

            response = requests.post(self.base_url, headers=headers, json=data)

            if response.status_code == 200:
                result = response.json()
                content = result['candidates'][0]['content']['parts'][0]['text']

                logger.debug(f"Gemini analytical response: {content}")

                # Extract JSON from response
                json_start = content.find('{')
                json_end = content.rfind('}') + 1
                if json_start != -1 and json_end != -1:
                    article_json = json.loads(content[json_start:json_end])

                    # Add metadata for tracking analytical variety
                    article_json['analysis_style'] = style['approach']
                    article_json['narrative_structure'] = structure
                    article_json['conclusion_approach'] = conclusion

                    return article_json

        except Exception as e:
            logger.error(f"Error generating analytical article: {e}")

        return None

    def _get_analytical_angle(self, news_item: NewsItem) -> str:
        """Determine legitimate analytical angle based on news content"""
        import random

        title_lower = news_item.title.lower()
        summary_lower = news_item.summary.lower()
        content = f"{title_lower} {summary_lower}"

        # Determine news category and select appropriate analytical angles
        if any(word in content for word in ['transfer', 'signing', 'bid', 'move', 'deal']):
            return random.choice(self.content_angles['transfer_analysis'])

        elif any(word in content for word in ['injury', 'fitness', 'medical', 'surgery', 'recovery']):
            return random.choice(self.content_angles['injury_analysis'])

        elif any(word in content for word in ['tactics', 'formation', 'strategy', 'system', 'style']):
            return random.choice(self.content_angles['tactical_analysis'])

        elif any(word in content for word in ['performance', 'stats', 'goals', 'assists', 'form']):
            return random.choice(self.content_angles['performance_review'])

        else:
            return random.choice(self.content_angles['general_analysis'])

    def _ensure_analytical_quality(self, article_content: str) -> bool:
        """Validate that article maintains analytical standards"""

        # Red flag phrases that indicate fabrication
        red_flags = [
            'sources tell me', 'insider reveals', 'behind closed doors',
            'dressing room sources', 'club insider', 'my sources',
            'confidential information', 'off the record'
        ]

        content_lower = article_content.lower()

        # Check for fabrication red flags
        for flag in red_flags:
            if flag in content_lower:
                logger.warning(f"Article contains red flag phrase: {flag}")
                return False

        # Ensure analytical language is present
        analytical_indicators = [
            'analysis', 'data', 'statistics', 'indicates', 'suggests',
            'trend', 'pattern', 'metrics', 'comparison', 'historical'
        ]

        analytical_count = sum(
            1 for indicator in analytical_indicators if indicator in content_lower)

        if analytical_count < 3:
            logger.warning("Article lacks sufficient analytical language")
            return False

        return True


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


class TelegramClient:
    def __init__(self, bot_token: str, channel_id: str, owner_chat_id: str = None, api_id: int = None, api_hash: str = None):
        self.channel_id = channel_id
        self.owner_chat_id = owner_chat_id

        from pyrogram import Client

        # Create sessions directory if it doesn't exist
        sessions_dir = "./sessions"
        if not os.path.exists(sessions_dir):
            try:
                os.makedirs(sessions_dir)
                logger.info(f"Created sessions directory: {sessions_dir}")
            except Exception as e:
                logger.warning(
                    f"Could not create sessions directory: {e}, using in-memory session")
                # Fall back to in-memory session
                self.app = Client(
                    "premier_league_bot",
                    api_id=api_id,
                    api_hash=api_hash,
                    bot_token=bot_token,
                    in_memory=True
                )
                return

        # Use file-based session if directory creation was successful
        self.app = Client(
            "premier_league_bot",
            api_id=api_id,
            api_hash=api_hash,
            bot_token=bot_token,
            workdir=sessions_dir
        )

    async def test_owner_message(self):
        """Test method to verify owner chat ID works"""
        if not self.owner_chat_id:
            logger.error("No owner chat ID set!")
            return False

        try:
            from pyrogram import enums

            await self.app.send_message(
                chat_id=int(self.owner_chat_id),
                text="🤖 <b>Bot Test Message</b>\n\nIf you receive this, your owner chat ID is working correctly!",
                parse_mode=enums.ParseMode.HTML
            )
            logger.info("Test message sent successfully!")
            return True
        except Exception as e:
            logger.error(f"Failed to send test message: {e}")
            return False

    async def send_article_for_review(self, article_id: str, generated_article: dict, news_item) -> bool:
        """Send article to owner for review"""
        if not self.owner_chat_id:
            logger.warning("Owner chat ID not set, cannot send for review")
            return False

        logger.info(
            f"Sending article {article_id} for review to owner {self.owner_chat_id}")

        try:
            from pyrogram import enums

            # Create simple text message
            review_message = f"""
🔍 ARTICLE REVIEW #{article_id}

📰 Title: {generated_article['title']}

📱 Teaser: {generated_article.get('telegram_teaser', 'No teaser')}

📝 Content: {generated_article['content'][:200]}...

📊 Source: {news_item.source}
Type: {generated_article.get('article_type', 'unknown')}

⏰ Instructions:
Reply with: /post {article_id} to approve
Or wait 30 minutes for auto-approval

Use /choice to see all pending articles
"""

            # Send simple text message only
            await self.app.send_message(
                chat_id=int(self.owner_chat_id),
                text=review_message
            )

            logger.info(
                f"Successfully sent review message for article {article_id}")
            return True

        except Exception as e:
            logger.error(f"Error sending review message: {e}")
            return False

    async def send_choice_summary(self, pending_articles: dict) -> bool:
        """Send summary of all pending articles"""
        if not self.owner_chat_id or not pending_articles:
            return False

        try:
            summary_message = f"📋 PENDING ARTICLES ({len(pending_articles)})\n\n"

            for article_id, article_data in pending_articles.items():
                generated = article_data['generated_article']
                timestamp = datetime.fromisoformat(
                    article_data['timestamp']).strftime("%H:%M")

                summary_message += f"🆔 {article_id} | {timestamp}\n"
                summary_message += f"📰 {generated['title'][:50]}...\n"
                summary_message += f"🏷️ {generated.get('article_type', 'unknown')}\n\n"

            summary_message += f"💡 Commands:\n"
            summary_message += f"/post {' '.join(pending_articles.keys())} - Approve all\n"
            summary_message += f"/post article_id1 article_id2 - Approve specific\n"
            summary_message += f"/skip - Skip this cycle\n"

            await self.app.send_message(
                chat_id=int(self.owner_chat_id),
                text=summary_message
            )

            logger.info("Successfully sent choice summary")
            return True

        except Exception as e:
            logger.error(f"Error sending choice summary: {e}")
            return False

    async def send_message(self, message: str, image_url: str = None) -> bool:
        """Send message to Telegram channel"""
        try:
            from pyrogram import enums

            await self.app.send_message(
                chat_id=self.channel_id,
                text=message,
                parse_mode=enums.ParseMode.HTML,
                disable_web_page_preview=False
            )
            return True

        except Exception as e:
            logger.error(f"Error sending to Telegram channel: {e}")
            return False


class PremierLeagueNewsBot:
    def __init__(self, config: Dict[str, str]):
        self.db = NewsDatabase()
        self.rss_manager = RSSFeedManager()
        self.analyzer = NewsAnalyzer()
        self.gemini = GeminiClient(config['gemini_api_key'])
        self.blogger = BloggerClient(config['blog_id'])
        self.telegram = TelegramClient(
            config['telegram_bot_token'],
            config['telegram_channel_id'],
            config.get('owner_chat_id'),
            config.get('telegram_api_id'),
            config.get('telegram_api_hash')
        )
        self.review_system = ArticleReviewSystem(
            config.get('review_timeout_minutes', 30))
        self.max_daily_posts = config.get('max_daily_posts', 12)
        self.posts_per_job = config.get('posts_per_job', 4)
        self.daily_post_count = 0
        self.last_reset_date = datetime.now().date()

    def _check_daily_limit(self) -> int:
        """Check how many posts we can still make today"""
        current_date = datetime.now().date()

        # Reset counter if it's a new day
        if current_date != self.last_reset_date:
            self.daily_post_count = 0
            self.last_reset_date = current_date
            logger.info("Daily post counter reset for new day")

        remaining = max(0, self.max_daily_posts - self.daily_post_count)
        logger.info(
            f"Daily posts: {self.daily_post_count}/{self.max_daily_posts}, remaining: {remaining}")
        return remaining

    def _get_posts_limit_for_job(self) -> int:
        """Get the maximum posts this job can create"""
        remaining_daily = self._check_daily_limit()
        return min(self.posts_per_job, remaining_daily)

    def handle_skip_command(self) -> str:
        """Handle skip command for pending articles"""
        skipped = self.review_system.skip_all_pending()
        if skipped:
            return f"⏭️ Skipped {len(skipped)} pending articles: {', '.join(skipped)}"
        else:
            return "📭 No pending articles to skip"

    async def run_daily_cycle_async(self):
        """Run the daily news cycle asynchronously"""
        logger.info("Starting daily news cycle")

        # Check for expired articles first and auto-approve them
        auto_approved = self.review_system.auto_approve_expired()
        if auto_approved:
            logger.info(f"Auto-approved {len(auto_approved)} expired articles")
            await self.process_approved_articles_async(auto_approved)

        # Check limits
        job_post_limit = self._get_posts_limit_for_job()

        if job_post_limit == 0:
            logger.info("Post limit reached. Skipping this cycle.")
            return

        # Fetch and process news
        all_news = self.rss_manager.fetch_news()
        logger.info(f"Fetched {len(all_news)} news items")

        # Filter out already processed news
        new_news = [
            item for item in all_news if not self.db.is_processed(item.hash)]
        logger.info(f"Found {len(new_news)} new items")

        if not new_news:
            logger.info("No new news items to process")
            return

        # Select top stories
        top_stories = self.analyzer.select_top_stories(
            new_news, job_post_limit)
        logger.info(f"Selected {len(top_stories)} top stories")

        # Generate and send articles for review
        articles_generated = 0
        for news_item in top_stories:
            if articles_generated >= job_post_limit:
                break

            # Generate article
            article = self.gemini.generate_article(news_item)
            if not article:
                logger.warning(
                    f"Failed to generate article for: {news_item.title}")
                continue

            # Add to review system
            article_id = self.review_system.add_pending_article(
                news_item, article)

            # Send for review
            if await self.telegram.send_article_for_review(article_id, article, news_item):
                articles_generated += 1
                logger.info(
                    f"Sent article #{article_id} for review: {article['title']}")

        if articles_generated > 0:
            # Send summary of all pending articles
            pending = self.review_system.get_pending_articles()
            await self.telegram.send_choice_summary(pending)
            logger.info(
                f"Generated {articles_generated} articles and sent for review")


class XPoster:
    def __init__(self, consumer_key, consumer_secret, access_token, access_token_secret, posted_file="posted_to_x.json"):
        self.posted_file = posted_file
        self.api = None
        self.posted_ids = set()
        self._load_posted()
        self._authenticate(consumer_key, consumer_secret,
                           access_token, access_token_secret)

    def _authenticate(self, consumer_key, consumer_secret, access_token, access_token_secret):
        try:
            auth = tweepy.OAuth1UserHandler(
                consumer_key, consumer_secret, access_token, access_token_secret)
            self.api = tweepy.API(auth)
        except Exception as e:
            print(f"Error authenticating with X API: {e}")

    def _load_posted(self):
        try:
            if os.path.exists(self.posted_file):
                with open(self.posted_file, "r", encoding="utf-8") as f:
                    self.posted_ids = set(json.load(f))
        except Exception as e:
            print(f"Error loading posted X IDs: {e}")

    def _save_posted(self):
        try:
            with open(self.posted_file, "w", encoding="utf-8") as f:
                json.dump(list(self.posted_ids), f)
        except Exception as e:
            print(f"Error saving posted X IDs: {e}")

    def has_posted(self, telegram_message_id):
        return str(telegram_message_id) in self.posted_ids

    def mark_posted(self, telegram_message_id):
        self.posted_ids.add(str(telegram_message_id))
        self._save_posted()

    def post_to_x(self, text, image_url=None):
        try:
            if image_url:
                filename = "temp_x_image.jpg"
                r = requests.get(image_url)
                with open(filename, "wb") as f:
                    f.write(r.content)
                media = self.api.media_upload(filename)
                tweet = self.api.update_status(
                    status=text, media_ids=[media.media_id])
                os.remove(filename)
            else:
                tweet = self.api.update_status(status=text)
            return tweet.id
        except Exception as e:
            print(f"Error posting to X: {e}")
            return None


# --- New: Event-driven Telegram-to-X sync using message handler ---
def setup_telegram_to_x_sync(app, xposter, channel_id):
    from pyrogram import filters
    from pyrogram.types import Message

    def extract_hashtags(text, max_tags=3):
        import re
        # List of EPL and football keywords for hashtags
        keywords = [
            'Premier League', 'EPL', 'Football', 'Soccer', 'Transfer', 'Goal', 'Injury', 'VAR',
            'Arsenal', 'Chelsea', 'Liverpool', 'Manchester United', 'Man United', 'Man City', 'Manchester City',
            'Tottenham', 'Spurs', 'West Ham', 'Everton', 'Aston Villa', 'Newcastle', 'Brighton',
            'Crystal Palace', 'Fulham', 'Brentford', 'Wolves', 'Wolverhampton', 'Nottingham Forest',
            'Bournemouth', 'Sheffield United', 'Burnley', 'Luton Town', 'FA Cup', 'Champions League'
        ]
        text_lower = text.lower()
        found = []
        for kw in keywords:
            # Match whole words, ignore case
            if re.search(r'\\b' + re.escape(kw.lower()) + r'\\b', text_lower):
                tag = '#' + kw.replace(' ', '')
                if tag not in found:
                    found.append(tag)
            if len(found) >= max_tags:
                break
        # If not enough, add top 1-2 unique words from text (longer than 5 chars, not common stopwords)
        if len(found) < max_tags:
            stopwords = set(['the', 'and', 'for', 'with', 'that', 'from', 'this', 'have', 'will', 'your', 'about', 'after', 'could', 'their', 'which', 'more', 'than', 'when', 'where', 'what', 'who', 'been', 'into', 'over', 'just', 'like',
                            'they', 'but', 'are', 'has', 'was', 'you', 'all', 'out', 'now', 'his', 'her', 'him', 'she', 'our', 'its', 'not', 'can', 'get', 'had', 'new', 'one', 'two', 'three', 'four', 'five', 'six', 'seven', 'eight', 'nine', 'ten'])
            words = re.findall(r'\\b[a-zA-Z]{5,}\\b', text)
            for w in words:
                wl = w.lower()
                tag = '#' + w.capitalize()
                if wl not in stopwords and tag not in found:
                    found.append(tag)
                if len(found) >= max_tags:
                    break
        return found

    @app.on_message(filters.channel & filters.chat(channel_id))
    async def handle_new_channel_post(client, message: Message):
        # Only process text or caption messages
        if not message.text and not message.caption:
            return
        telegram_id = message.message_id
        if xposter.has_posted(telegram_id):
            return
        text = message.caption if message.caption else message.text
        # You must define replace_caption_text somewhere in your code
        text = replace_caption_text(text)
        hashtags = extract_hashtags(text)
        if hashtags:
            # Add hashtags at the end, avoid duplicates
            text = text.strip() + '\n' + ' '.join(hashtags)
        image_url = None
        if message.photo:
            image_url = await client.download_media(message.photo, file_name="temp_sync_photo.jpg")
        tweet_id = xposter.post_to_x(text, image_url=image_url)
        if tweet_id:
            xposter.mark_posted(telegram_id)
            print(
                f"Posted Telegram message {telegram_id} to X as tweet {tweet_id}")
        if image_url and os.path.exists(image_url):
            os.remove(image_url)


async def process_approved_articles_as_drafts(client, config, bot, article_ids):
    """Process articles as drafts and send comprehensive report"""
    draft_reports = []

    for article_id in article_ids:
        if article_id not in bot.review_system.pending_articles:
            continue

        article_data = bot.review_system.pending_articles[article_id]
        if article_data['status'] != 'approved':
            continue

        # Reconstruct news item and article
        news_data = article_data['news_item']
        news_item = NewsItem(
            title=news_data['title'],
            summary=news_data['summary'],
            link=news_data['link'],
            published=news_data['published'],
            source=news_data['source'],
            hash=news_data['hash'],
            image_url=news_data['image_url'],
            image_alt=news_data['image_alt']
        )

        generated_article = article_data['generated_article']

        # Create draft in Blogger
        draft_info = bot.blogger.create_draft(
            title=news_item.title,
            content=generated_article['content'],
            labels=['EPL News'],
            image_url=news_item.image_url,
            image_alt=news_item.image_alt,
            image_source=news_item.source
        )

        if draft_info:
            # Mark as processed (even though it's a draft)
            bot.db.mark_processed(
                news_item.hash, generated_article['title'], draft_info['edit_url'])

            # Store draft report info
            draft_report = {
                'draft_info': draft_info,
                'article': generated_article,
                'news_item': news_item,
                'article_id': article_id
            }
            draft_reports.append(draft_report)

            # Remove from pending articles
            bot.review_system.remove_article(article_id)

            logger.info(
                f"Successfully created draft for article #{article_id}: {generated_article['title']}")
        else:
            logger.warning(f"Failed to create draft for article #{article_id}")

    # Send comprehensive report to your DM
    if draft_reports:
        await send_draft_report_to_dm(client, draft_reports)


async def send_draft_report_to_dm(client, draft_reports):
    """Send beautiful draft report with images, titles, excerpts to owner's DM using Bot Client"""
    try:
        # Get owner chat ID from environment
        owner_chat_id = int(os.getenv('OWNER_CHAT_ID'))
        logger.info(f"Sending draft report to owner: {owner_chat_id}")

        # Send header message
        header_message = f"""📰 **DRAFT ARTICLES CREATED**
📅 {datetime.now().strftime("%B %d, %Y at %H:%M")}

✅ **{len(draft_reports)} articles** successfully created as drafts
🔗 **Ready for review** in your Blogger dashboard

───────────────────────────────"""

        await client.send_message(owner_chat_id, header_message)
        logger.info("Header message sent successfully")

        # Send each article report with image
        for i, report in enumerate(draft_reports, 1):
            try:
                draft_info = report['draft_info']
                article = report['article']
                news_item = report['news_item']

                title = news_item.title
                summary = news_item.summary if hasattr(
                    news_item, 'summary') else title
                excerpt = f"{summary}"
                # Article report message (FIXED formatting)
                article_message = f"""📖 **ARTICLE {i} OF {len(draft_reports)}**

🔗 **Edit Draft:** [Click to Edit]({draft_info['edit_url']})

📝 **Excerpt:** {excerpt}

Image Source: [{news_item.source if news_item.source else "Unknown"}]
Follow @premierleagueinsider for more premier league news!
"""
                # Send image with caption if available
                if news_item.image_url:
                    try:
                        await client.send_photo(
                            chat_id=owner_chat_id,
                            photo=news_item.image_url,
                            caption=article_message
                        )
                        logger.info(f"Sent draft report {i} with image")
                    except Exception as e:
                        logger.warning(
                            f"Failed to send image for article {i}: {e}")
                        # Fallback to text message
                        fallback_message = f"🖼️ **Image:** {news_item.image_url}\n\n{article_message}"
                        await client.send_message(owner_chat_id, fallback_message)
                        logger.info(f"Sent draft report {i} as text fallback")
                else:
                    # Send text only if no image
                    await client.send_message(owner_chat_id, article_message)
                    logger.info(f"Sent draft report {i} as text only")

                # Small delay between messages
                await asyncio.sleep(2)

            except Exception as e:
                logger.error(f"Error sending individual report {i}: {e}")
                # Send basic fallback
                try:
                    basic_message = f"📖 **ARTICLE {i}:** {news_item.title}\nExcerpt: {excerpt} 🔗 Edit: {draft_info['edit_url']}"
                    await client.send_message(owner_chat_id, basic_message)
                except:
                    pass

        # Send footer message
        footer_message = f"""🎯 **NEXT STEPS:**

1. 📱 Go to your Blogger dashboard
2. 🔍 Review the {len(draft_reports)} draft articles
3. ✏️ Edit if needed
4. 🚀 Publish when ready
5. 📢 Share on your Telegram channel manually

💡 All drafts are ready for your review!"""

        await client.send_message(owner_chat_id, footer_message)
        logger.info("Footer message sent successfully")

        logger.info(
            f"Successfully sent comprehensive draft report for {len(draft_reports)} articles")

    except Exception as e:
        logger.error(f"Error sending draft report: {e}")
        import traceback
        traceback.print_exc()

        # Emergency fallback message
        try:
            owner_chat_id = int(os.getenv('OWNER_CHAT_ID'))
            emergency_message = f"🚨 {len(draft_reports)} draft articles created successfully!\n\nCheck your Blogger dashboard to review them.\n\nSorry, detailed report failed to send."
            await client.send_message(owner_chat_id, emergency_message)
            logger.info("Emergency fallback message sent")
        except Exception as emergency_error:
            logger.error(f"Even emergency message failed: {emergency_error}")


def main():
    from pyrogram import Client, filters
    import asyncio

    config = {
        'gemini_api_key': os.getenv('GEMINI_API_KEY'),
        'blog_id': os.getenv('BLOG_ID'),
        'telegram_bot_token': os.getenv('TELEGRAM_BOT_TOKEN'),
        'telegram_channel_id': os.getenv('TELEGRAM_CHANNEL_ID'),
        'telegram_api_id': int(os.getenv('TELEGRAM_API_ID')),
        'telegram_api_hash': os.getenv('TELEGRAM_API_HASH'),
        'owner_chat_id': os.getenv('OWNER_CHAT_ID'),
        'review_timeout_minutes': int(os.getenv('REVIEW_TIMEOUT_MINUTES', 30)),
        'max_daily_posts': 12,
        'posts_per_job': 4
    }

    def get_x_config():
        return {
            "consumer_key": os.getenv("X_CONSUMER_KEY"),
            "consumer_secret": os.getenv("X_CONSUMER_SECRET"),
            "access_token": os.getenv("X_ACCESS_TOKEN"),
            "access_token_secret": os.getenv("X_ACCESS_TOKEN_SECRET")
        }

    # Initialize the bot
    bot = PremierLeagueNewsBot(config)
    bot.review_system.clear_old_articles()

    # Use Bot Client instead of User Client
    bot_client = Client(
        "premier_league_bot",
        api_id=config['telegram_api_id'],
        api_hash=config['telegram_api_hash'],
        bot_token=config['telegram_bot_token']
    )

    x_config = get_x_config()
    xposter = XPoster(
        x_config["consumer_key"],
        x_config["consumer_secret"],
        x_config["access_token"],
        x_config["access_token_secret"]
    )

    async def run_daily_cycle_and_create_drafts():
        """Run daily cycle and create drafts automatically"""
        logger.info("Starting automated daily cycle")

        # Check limits
        job_post_limit = min(4, bot._get_posts_limit_for_job())

        if job_post_limit == 0:
            logger.info("Post limit reached. Skipping this cycle.")
            return

        try:
            # Fetch and process news
            all_news = bot.rss_manager.fetch_news()
            logger.info(f"Fetched {len(all_news)} news items")

            # Filter out already processed news
            new_news = [
                item for item in all_news if not bot.db.is_processed(item.hash)]
            logger.info(f"Found {len(new_news)} new items")

            if not new_news:
                logger.info("No new news items to process")
                return

            # Select top 4 stories
            top_stories = bot.analyzer.select_top_stories(
                new_news, job_post_limit)
            logger.info(f"Selected {len(top_stories)} top stories")

            if not top_stories:
                logger.info("No high-quality stories found")
                return

            # Generate articles and create drafts automatically
            draft_articles = []
            for news_item in top_stories:
                # Generate article
                article = bot.gemini.generate_article(news_item)
                if not article:
                    logger.warning(
                        f"Failed to generate article for: {news_item.title}")
                    continue

                # Add to review system and auto-approve
                article_id = bot.review_system.add_pending_article(
                    news_item, article)
                bot.review_system.approve_articles(
                    [article_id])  # Auto-approve
                draft_articles.append(article_id)

                logger.info(
                    f"Generated and approved article #{article_id}: {article['title']}")

            # Process all approved articles as drafts
            if draft_articles:
                await process_approved_articles_as_drafts(bot_client, config, bot, draft_articles)
                logger.info(
                    f"Created {len(draft_articles)} draft articles and sent report")
            else:
                logger.info("No articles generated for this cycle")

        except Exception as e:
            logger.error(f"Error in daily cycle: {e}")
            import traceback
            traceback.print_exc()

    async def run_bot():
        logger.info(
            "Starting automated Premier League draft creator with BOT CLIENT...")

        # Start bot client
        await bot_client.start()
        logger.info("✅ Bot client started!")

        # Setup Telegram-to-X sync
        setup_telegram_to_x_sync(
            bot_client, xposter, config['telegram_channel_id'])
        logger.info("✅ Telegram-to-X sync setup complete!")

        async def periodic_news_cycle():
            while True:
                logger.info("Running scheduled news cycle...")
                await run_daily_cycle_and_create_drafts()
                await asyncio.sleep(14400)  # 4 hours

        # async def periodic_telegram_to_x():
        #     while True:
        #         logger.info("Running scheduled Telegram-to-X sync...")
        #         await sync_telegram_to_x(bot_client, xposter, config['telegram_channel_id'])
        #         await asyncio.sleep(7200)  # 2 hours
        #     # Start periodic tasks

        await asyncio.gather(
            periodic_news_cycle(),
        )

        try:
            # Get bot info
            bot_info = await bot_client.get_me()
            logger.info(f"✅ Bot logged in as: @{bot_info.username}")

            # Send startup message to owner
            startup_msg = f"""
🤖 **Premier League Draft Creator Started!**

✅ Automated draft creation is now active
📰 Will generate up to 4 articles and create drafts
📱 You'll receive detailed reports with images and excerpts
🔄 Running every 4 hours

🎯 **What happens:**
1. Bot finds Premier League news
2. Generates 4 quality articles
3. Creates drafts in your Blogger
4. Sends you a beautiful report
5. You review and publish manually

📅 Started: {datetime.now().strftime("%B %d, %Y at %H:%M")}

💡 Bot Username: @{bot_info.username}
"""
            await bot_client.send_message(int(config['owner_chat_id']), startup_msg)
            logger.info("Startup message sent to owner")

            # Run initial cycle
            logger.info("Running initial news cycle...")
            await run_daily_cycle_and_create_drafts()
            logger.info("✅ Initial cycle completed!")

            logger.info("🤖 Bot running in automated draft mode!")

            # # Keep running - check every 4 hours
            # while True:
            #     await asyncio.sleep(7200)  # 4 hours
            #     logger.info("Running scheduled news cycle...")
            #     await run_daily_cycle_and_create_drafts()

        except Exception as e:
            logger.error(f"Error in run_bot: {e}")
            import traceback
            traceback.print_exc()
        finally:
            if bot_client.is_connected:
                await bot_client.stop()
    try:
        asyncio.run(run_bot())
    except KeyboardInterrupt:
        logger.info("Bot stopped by user")
    except Exception as e:
        logger.error(f"Main loop error: {e}")


if __name__ == "__main__":
    main()
