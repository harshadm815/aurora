import logging
import sys

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
    for name, state in status.get("tools", {}).items():
        print(f"  tool     {name:<12} {state}")
    print()


def print_route(decision) -> None:
    print(f"\n  intent      {decision.intent.value}")
    print(f"  confidence  {decision.confidence:.2f}")
    print(f"  target      {decision.target or '-'}")
    for key, value in decision.arguments.items():
        print(f"  {key:<11} {value}")
    print(f"  reason      {decision.reason}")
    if decision.alternatives:
        others = ", ".join(
            f"{alt.intent.value} {alt.confidence:.2f}" for alt in decision.alternatives
        )
        print(f"  also        {others}")
    print()


def main():
    # Make sure characters like ° and – display correctly on Windows,
    # including when output is piped or redirected.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass

    setup_logging()

    engine = AuroraEngine()

    print("\n==============================")
    print(" AURORA KERNEL INITIALIZING ")
    print("==============================\n")

    engine.start()

    print(f"{settings.APP_NAME} v{settings.VERSION} online.")
    print("Type 'exit' to shut down.")
    print("Type 'clear' to clear the conversation.")
    print("Type 'status' to see system status.")
    print("Type 'route <message>' to see how a message would be routed.\n")

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

            if user_input.lower().startswith("route "):
                print_route(engine.route(user_input[6:]))
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
