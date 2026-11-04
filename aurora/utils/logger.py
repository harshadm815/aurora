from datetime import datetime


class Logger:
    """Simple console logger."""

    @staticmethod
    def info(message: str):
        time = datetime.now().strftime("%H:%M:%S")
        print(f"[INFO {time}] {message}")

    @staticmethod
    def warning(message: str):
        time = datetime.now().strftime("%H:%M:%S")
        print(f"[WARNING {time}] {message}")

    @staticmethod
    def error(message: str):
        time = datetime.now().strftime("%H:%M:%S")
        print(f"[ERROR {time}] {message}")