import logging

from aurora.config import settings
from aurora.kernel.engine import AuroraEngine
from aurora.utils.log import setup_logging

logger = logging.getLogger("aurora.main")


def print_status(status: dict) -> None:
    services = status["services"]
    plugins = status["plugins"]
    healthy = all(state == "Running" for state in services.values())

    print("\nAURORA: " + ("All systems operational." if healthy else "Some systems need attention."))
    for name, state in services.items():
        print(f"  service  {name:<12} {state}")
    for name, state in plugins.items():
        print(f"  plugin   {name:<12} {state}")
    if not plugins:
        print("  plugins  none registered")
    print()


def main():
    setup_logging()

    engine = AuroraEngine()

    print("\n==============================")
    print(" AURORA KERNEL INITIALIZING ")
    print("==============================\n")

    engine.start()

    print(f"{settings.APP_NAME} v{settings.VERSION} online.")
    print("Type 'exit' to shut down.")
    print("Type 'clear' to clear the conversation.")
    print("Type 'status' to see system status.\n")

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

            if user_input.lower() == "status":
                print_status(engine.status())
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
