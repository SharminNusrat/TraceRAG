from abc import ABC, abstractmethod

class ChatProvider(ABC):

    @abstractmethod
    def chat(self, prompt: str, system_message: str = None) -> str:
        pass

    @abstractmethod
    def model_name(self) -> str:
        pass