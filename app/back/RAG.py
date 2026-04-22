import os
from qdrant_client import QdrantClient
from sentence_transformers import SentenceTransformer
from dotenv import load_dotenv
from pathlib import Path
from groq import Groq

class RAG:
    name:str
    def __init__(self, name:str "RAG-1"):
        self.name = name