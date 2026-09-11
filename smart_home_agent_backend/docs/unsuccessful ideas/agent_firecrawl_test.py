from dotenv import load_dotenv
from smart_home_agent.utils.discussion_scraper_firecrawl import scrape_url

if __name__ == "__main__":
    load_dotenv()

    url = "https://community.home-assistant.io/t/can-i-make-a-home-assistant-native-intercom/868524"

    data = scrape_url(url)

    if "data" not in data:
        print("Firecrawl error:")
        print(data)
    else:
        markdown = data["data"].get("markdown", "")
        print(markdown[:20000])
