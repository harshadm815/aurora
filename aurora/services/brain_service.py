from aurora.brain.brain import Brain
from aurora.kernel.lifecycle import ServiceState
from aurora.kernel.service import BaseService


class BrainService(BaseService):

    def __init__(self):
        super().__init__("Brain")

        self.brain = Brain()

    def start(self):
        self.state = ServiceState.RUNNING

    def stop(self):
        self.state = ServiceState.STOPPED