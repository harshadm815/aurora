from aurora.brain.brain import Brain
from aurora.kernel.lifecycle import ServiceState
from aurora.kernel.service import BaseService


class BrainService(BaseService):
    """
    Owns AURORA's single Brain instance.

    Other components get the Brain through this service rather than
    creating their own.
    """

    def __init__(self, brain: Brain | None = None):
        super().__init__("Brain")

        self.brain = brain or Brain()

    def start(self):
        self.state = ServiceState.RUNNING

    def stop(self):
        self.state = ServiceState.STOPPED
