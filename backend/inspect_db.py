import chromadb

client = chromadb.PersistentClient(path="./chroma_data")
collection = client.get_collection("source_elements")
results = collection.get()
for i, doc_id in enumerate(results["ids"]):
    print(f"ID: {doc_id}")
    print(f"Content: {results['documents'][i][:100]}")
    print(f"Metadata: {results['metadatas'][i]}")
    print("---")