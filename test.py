import chromadb
client = chromadb.PersistentClient(path="./chroma_db")
collection = client.get_collection(name="documents")
print(f"Nombre d'éléments dans la base : {collection.count()}")