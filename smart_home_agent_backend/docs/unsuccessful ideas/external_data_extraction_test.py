from docling.document_converter import DocumentConverter


DOCLING_URLS = [
    "https://www.zigbee2mqtt.io/",
    "https://docling-project.github.io/docling/",
    "https://docling-project.github.io/docling/getting_started/installation/",
    "https://docling-project.github.io/docling/getting_started/quickstart/",
    "https://docling-project.github.io/docling/usage/supported_formats/",
    "https://docling-project.github.io/docling/concepts/docling_document/",
    "https://docling-project.github.io/docling/examples/rag_langchain/",
]


def convert_urls_to_markdown(urls: list[str]) -> list[str]:
    converter = DocumentConverter()
    markdown_docs = []

    for result in converter.convert_all(urls):
        if result.document:
            markdown_docs.append(result.document.export_to_markdown())

    return markdown_docs


if __name__ == "__main__":
    docs = convert_urls_to_markdown(DOCLING_URLS)

    print(f"Converted documents: {len(docs)}")

    for i, markdown in enumerate(docs, start=1):
        print("\n" + "=" * 80)
        print(f"DOCUMENT {i}")
        print("=" * 80)
        print(markdown[:1500])