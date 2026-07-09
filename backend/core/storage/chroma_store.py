import chromadb
from core.schemas import Element
from core.storage.base import VectorStore

SOURCE_COLLECTION = "source_elements"
TARGET_COLLECTION = "target_elements"
DEFAULT_CHROMA_PATH = "./chroma_data"

class ChromaStore(VectorStore):

    def __init__(self, collection_name: str, path: str = DEFAULT_CHROMA_PATH):
        self.client = chromadb.PersistentClient(path=path)
        self.collection = self.client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine"}
        )

    def add_elements(self, elements: list[Element], embeddings: list[list[float]]) -> None:
        if not elements:
            return

        self.collection.upsert(
            ids=[e.identifier for e in elements],
            embeddings=embeddings,
            documents=[e.content for e in elements],
            metadatas=[{
                "type": e.type,
                "granularity": e.granularity,
                "parent_id": e.parent_id or "",
                "compare": e.compare
            } for e in elements]
        )

    def replace_elements(self, elements: list[Element], embeddings: list[list[float]]) -> None:
        current_ids = {e.identifier for e in elements}
        existing = self.collection.get()
        stale_ids = [doc_id for doc_id in existing["ids"] if doc_id not in current_ids]
        if stale_ids:
            self.collection.delete(ids=stale_ids)

        self.add_elements(elements, embeddings)

    def find_similar_elements(self, embedding: list[float], n_results: int, only_compare: bool = True) -> list[tuple[Element, float]]:
        where = {"compare": True} if only_compare else None
        results = self.collection.query(
            query_embeddings=[embedding],
            n_results=n_results,
            where=where,
            include=["documents", "metadatas", "distances"]
        )
        elements = []
        for i, doc_id in enumerate(results["ids"][0]):
            metadata = results["metadatas"][0][i]
            distance = results["distances"][0][i]
            similarity = 1 - distance
            element = Element(
                identifier=doc_id,
                type=metadata["type"],
                content=results["documents"][0][i],
                granularity=metadata["granularity"],
                parent_id=metadata["parent_id"] or None,
                compare=metadata["compare"]
            )
            elements.append((element, similarity))
        return elements

    def get_by_id(self, identifier: str) -> Element | None:
        result = self.collection.get(ids=[identifier])
        if not result["ids"]:
            return None
        metadata = result["metadatas"][0]
        return Element(
            identifier=result["ids"][0],
            type=metadata["type"],
            content=result["documents"][0],
            granularity=metadata["granularity"],
            parent_id=metadata["parent_id"] or None,
            compare=metadata["compare"]
        )

    def clear(self) -> None:
        self.client.delete_collection(self.collection.name)
        self.collection = self.client.get_or_create_collection(
            name=self.collection.name,
            metadata={"hnsw:space": "cosine"}
        )