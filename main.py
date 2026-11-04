import logging

from aurora.config import settings
from aurora.kernel.engine import AuroraEngine
from aurora.utils.log import setup_logging

logger = logging.getLogger("aurora.main")


def main():
    setup_logging()

    engine = AuroraEngine()

    print("\n==============================")
    print(" AURORA KERNEL INITIALIZING ")
    print("==============================\n")

    engine.start()

    print(f"{settings.APP_NAME} v{settings.VERSION} online.")
    print("Type 'exit' to shut down.")
    print("Type 'clear' to clear the conversation.\n")

    try:
        while True:
            user_input = input("You: ").strip()

            if not user_input:
                continue

            if user_input.lower() in ("exit", "quit"):
                break

            if user_input.lower() == "clear":
                engine.clear_conversation()
                print("AURORA: Conversation cleared.\n")
                continue

            try:
                response = engine.process_message(user_input)
                print(f"\nAURORA: {response}\n")

            except Exception as error:
                logger.exception("Error while processing a message")
                print(f"\nAURORA encountered an error: {error}\n")

    except (KeyboardInterrupt, EOFError):
        print()

    finally:
        print("\nShutting down AURORA...")
        engine.shutdown()
        print("AURORA offline.")


if __name__ == "__main__":
    main()
