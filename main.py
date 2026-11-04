from aurora.kernel.engine import AuroraEngine


def main():
    engine = AuroraEngine()
    engine.start()

    print("\nAURORA v0.4.0")
    print("Type 'exit' to shut down.")
    print("Type 'clear' to clear the conversation.\n")

    while True:
        user_input = input("You: ").strip()

        if not user_input:
            continue

        if user_input.lower() in ("exit", "quit"):
            engine.shutdown()
            break

        if user_input.lower() == "clear":
            engine.clear_conversation()
            print("AURORA: Conversation cleared.\n")
            continue

        try:
            response = engine.process_message(user_input)
            print(f"\nAURORA: {response}\n")

        except Exception as error:
            print(f"\nAURORA encountered an error: {error}\n")


if __name__ == "__main__":
    main()