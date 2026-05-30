import fitz  # PyMuPDF
from core.schemas import Artifact, ArtifactType
from core.ingestion.base import ArtifactProvider

class PDFProvider(ArtifactProvider):
    """Class for extracting artifacts from PDF documents."""
    
    def __init__(self, file_path: str):
        self.file_path = file_path
    
    def load(self) -> list[Artifact]:
        doc = fitz.open(self.file_path)
        full_text = ""
        for page in doc:
            full_text += page.get_text()
        doc.close()
        
        return [Artifact(
            identifier=self.file_path,
            type=ArtifactType.REQUIREMENT,
            content=full_text   
        )]