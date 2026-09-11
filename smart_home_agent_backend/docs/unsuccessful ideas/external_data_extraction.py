from docling.document_converter import DocumentConverter
from langchain.docstore.document import Document
from smart_home_agent.utils.vector_store import vs as vector_store
from typing import List
import os

def extract_and_store_documents(urls: List[str]):
    """
    Extract documents from URLs using Docling and store them in the vector store.
    """
    converter = DocumentConverter()
    documents = []

    for result in converter.convert_all(urls):
        if result.document:
            markdown = result.document.export_to_markdown()
            doc = Document(
                page_content=markdown,
                metadata={"source": result.input.url, "type": "external"}
            )
            documents.append(doc)

    if documents:
        vector_store.add_documents(documents)
        print(f"Added {len(documents)} documents to vector store")

def scrape_and_store_firecrawl(url: str):
    """
    Scrape a URL using Firecrawl and store in vector store.
    """
    from firecrawl import FirecrawlApp
    app = FirecrawlApp(api_key=os.getenv("FIRECRAWL_API_KEY"))

    try:
        scrape_result = app.scrape_url(url, params={'formats': ['markdown']})
        if 'markdown' in scrape_result:
            doc = Document(
                page_content=scrape_result['markdown'],
                metadata={"source": url, "type": "firecrawl"}
            )
            vector_store.add_documents([doc])
            print(f"Added document from {url} to vector store")
    except Exception as e:
        print(f"Error scraping {url}: {e}")

# Example usage
if __name__ == "__main__":
    # Example URLs for smart home documentation
    urls = [
        "https://www.zigbee2mqtt.io/",
        "https://docling-project.github.io/docling/",
    ]
    extract_and_store_documents(urls)

    # Example Firecrawl usage
    scrape_and_store_firecrawl("https://community.home-assistant.io/t/can-i-make-a-home-assistant-native-intercom/868524")