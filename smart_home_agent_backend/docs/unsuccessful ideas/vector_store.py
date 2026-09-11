from langchain_community.vectorstores import FAISS
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_core.documents import Document
from typing import List, Dict, Any
import os

class VectorStore:
    def __init__(self, persist_directory: str = "./vector_store"):
        self.persist_directory = persist_directory
        self.embeddings = None
        self.vectorstore = None
        self._init_embeddings()
        self.load_or_create()

    def _init_embeddings(self):
        if self.embeddings is None:
            self.embeddings = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")

    def load_or_create(self):
        self._init_embeddings()
        if os.path.exists(self.persist_directory):
            try:
                self.vectorstore = FAISS.load_local(self.persist_directory, self.embeddings, allow_dangerous_deserialization=True)
            except:
                self.vectorstore = None
        if self.vectorstore is None:
            # Create empty store
            self.vectorstore = FAISS.from_texts(["dummy"], self.embeddings)
            self.save()

    def add_documents(self, documents: List[Document]):
        if self.vectorstore:
            self.vectorstore.add_documents(documents)
            self.save()

    def add_texts(self, texts: List[str], metadatas: List[Dict[str, Any]] = None):
        if self.vectorstore:
            self.vectorstore.add_texts(texts, metadatas=metadatas)
            self.save()

    def search(self, query: str, k: int = 5) -> List[Document]:
        if self.vectorstore:
            return self.vectorstore.similarity_search(query, k=k)
        return []

    def save(self):
        if self.vectorstore:
            self.vectorstore.save_local(self.persist_directory)

# Global instance - lazy loaded
_vs_instance = None

def get_vector_store():
    global _vs_instance
    if _vs_instance is None:
        _vs_instance = VectorStore()
    return _vs_instance

vs = get_vector_store()