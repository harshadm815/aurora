from abc import ABC, abstractmethod


class BaseTool(ABC):
    """
    Base class for every AURORA tool.
    """

    def __init__(self, name: str):
        self.name = name

    @abstractmethod
    def execute(self, *args, **kwargs):
        """
        Executes the tool.
        """
        pass