import chromadb

client = chromadb.PersistentClient(path="chroma_data/projects/analysis-1/chroma")
for collection in client.list_collections():
    print(collection.name, "-", collection.count(), "items")
    item = collection.peek(3)
    for i in range(3):
        print("  id:      ", item["ids"][i])
        print("  metadata:", item["metadatas"][i])
        print("  text:    ", item["documents"][i][:80].replace("\n", " "))
        print("  vector:  ", [round(x, 3) for x in item["embeddings"][i][:5]], "... 768 numbers")
