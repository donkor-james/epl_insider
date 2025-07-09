import requests
import json
from datetime import datetime


def test_scorebat_api():
    """Test the ScoreBat API and explore the data structure"""

    # ScoreBat API endpoint
    api_url = "https://www.scorebat.com/video-api/v3/feed/token=MjIwMDkzXzE3NTE4NTUyMzRfM2UzMTY0YTA1ZTY0MDgxZWRhYzZiMzFjZDU4NDdlMDk1YzgzNDg0ZA"

    try:
        print("🔄 Fetching data from ScoreBat API...")

        # Make the API request
        response = requests.get(api_url, timeout=30)

        print(f"📊 Response Status: {response.status_code}")
        print(f"📏 Response Size: {len(response.content)} bytes")

        if response.status_code == 200:
            # Parse JSON response
            data = response.json()

            print(f"✅ Successfully fetched data!")
            print(f"📋 Data type: {type(data)}")

            if isinstance(data, list):
                print(f"📊 Number of items: {len(data)}")

                # Show first few items structure
                if len(data) > 0:
                    print("\n🔍 First item structure:")
                    first_item = data[0]
                    print(json.dumps(first_item, indent=2))

                    # Analyze the data structure
                    print(f"\n📋 Available fields in first item:")
                    if isinstance(first_item, dict):
                        for key, value in first_item.items():
                            value_type = type(value).__name__
                            print(f"  • {key}: {value_type}")

                            if isinstance(value, str):
                                if len(value) > 100:
                                    print(f"    Preview: {value[:100]}...")
                                else:
                                    print(f"    Value: {value}")
                            elif isinstance(value, list):
                                print(f"    Length: {len(value)} items")
                                if len(value) > 0:
                                    print(
                                        f"    First item type: {type(value[0]).__name__}")
                            elif isinstance(value, dict):
                                print(f"    Keys: {list(value.keys())}")
                            else:
                                print(f"    Value: {value}")

                    # Show multiple items to understand the pattern
                    print(f"\n📋 Showing first 3 items overview:")
                    for i in range(min(3, len(data))):
                        item = data[i]
                        print(f"\n  Item {i+1}:")
                        if isinstance(item, dict):
                            print(f"    Title: {item.get('title', 'N/A')}")
                            print(
                                f"    Competition: {item.get('competition', 'N/A')}")
                            print(f"    Date: {item.get('date', 'N/A')}")

                            # Check for common fields
                            if 'videos' in item:
                                print(
                                    f"    Videos: {len(item['videos'])} available")
                            if 'thumbnail' in item:
                                print(
                                    f"    Thumbnail: {item.get('thumbnail', 'N/A')[:50]}...")
                            if 'matchviewUrl' in item:
                                print(f"    Match URL: Available")
                            if 'side1' in item:
                                print(
                                    f"    Side1: {item.get('side1', {}).get('name', 'N/A')}")
                            if 'side2' in item:
                                print(
                                    f"    Side2: {item.get('side2', {}).get('name', 'N/A')}")

            elif isinstance(data, dict):
                print(f"📊 Dictionary with keys: {list(data.keys())}")
                print(f"📋 Data structure:")
                print(json.dumps(data, indent=2))

            # Save sample data to file for analysis
            with open('scorebat_sample_data.json', 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)
            print(f"\n💾 Sample data saved to 'scorebat_sample_data.json'")

            return data

        else:
            print(f"❌ API Error: {response.status_code}")
            print(f"Response: {response.text[:500]}...")
            return None

    except requests.exceptions.Timeout:
        print("⏰ Request timeout - API took too long to respond")
        return None
    except requests.exceptions.RequestException as e:
        print(f"🌐 Network error: {e}")
        return None
    except json.JSONDecodeError as e:
        print(f"📄 JSON parsing error: {e}")
        print(f"Raw response: {response.text[:500]}...")
        return None
    except Exception as e:
        print(f"❌ Unexpected error: {e}")
        return None


def analyze_data_structure(data):
    """Analyze the overall data structure without filtering"""
    if not data:
        return

    print("\n" + "="*50)
    print("📊 COMPLETE DATA STRUCTURE ANALYSIS")
    print("="*50)

    if isinstance(data, list) and len(data) > 0:
        print(f"📋 Total items: {len(data)}")

        # Analyze all unique fields across all items
        all_fields = set()
        competitions = set()

        for item in data:
            if isinstance(item, dict):
                all_fields.update(item.keys())
                if 'competition' in item:
                    competitions.add(item.get('competition', 'Unknown'))

        print(f"\n🔍 All available fields across all items:")
        for field in sorted(all_fields):
            print(f"  • {field}")

        print(f"\n🏆 All competitions/leagues found:")
        for comp in sorted(competitions):
            print(f"  • {comp}")

        # Show sample of different competitions
        print(f"\n📺 Sample items from different competitions:")
        shown_competitions = set()
        count = 0

        for item in data:
            if isinstance(item, dict) and count < 5:
                competition = item.get('competition', 'Unknown')
                if competition not in shown_competitions:
                    shown_competitions.add(competition)
                    count += 1

                    print(f"\n  Competition: {competition}")
                    print(f"    Title: {item.get('title', 'N/A')}")
                    print(f"    Date: {item.get('date', 'N/A')}")
                    if 'side1' in item and 'side2' in item:
                        side1 = item.get('side1', {}).get('name', 'N/A')
                        side2 = item.get('side2', {}).get('name', 'N/A')
                        print(f"    Teams: {side1} vs {side2}")


if __name__ == "__main__":
    print("🏆 ScoreBat API Test - Complete Data Analysis")
    print("="*60)

    # Test the API
    data = test_scorebat_api()

    # Analyze complete data structure
    if data:
        analyze_data_structure(data)

    print(
        f"\n✅ Test completed at {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    print("\n💡 Check the 'scorebat_sample_data.json' file for the complete raw data!")
