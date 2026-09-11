import os
from firecrawl import FirecrawlApp
from langchain.docstore.document import Document
from smart_home_agent.utils.vector_store import vs as vector_store

def scrape_url(url: str) -> dict:
    """
    Scrape a URL using Firecrawl API.
    """
    api_key = os.getenv("FIRECRAWL_API_KEY")

    if not api_key:
        raise ValueError("Missing FIRECRAWL_API_KEY in environment variables.")

    app = FirecrawlApp(api_key=api_key)
    return app.scrape_url(url, params={'formats': ['markdown']})

def scrape_and_store(url: str):
    """
    Scrape a URL and store the content in the vector store.
    """
    try:
        result = scrape_url(url)
        if 'markdown' in result:
            doc = Document(
                page_content=result['markdown'],
                metadata={"source": url, "type": "firecrawl"}
            )
            vector_store.add_documents([doc])
            print(f"Added document from {url} to vector store")
        return result
    except Exception as e:
        print(f"Error scraping {url}: {e}")
        return None