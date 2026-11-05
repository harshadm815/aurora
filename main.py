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


def print_plan(plan) -> None:
    if not plan.is_multi_step:
        print(f"\n  single step: {plan.steps[0].describe()}\n")
        return
    print()
    for number, step in enumerate(plan.steps, start=1):
        print(f"  {number}. {step.describe()}")
    for note in plan.notes:
        print(f"  note: {note}")
    if plan.truncated:
        print("  (truncated to the first steps)")
    print()


def watch(engine, camera_tool) -> None:
    from aurora.vision.camera import VisionUnavailable
    from aurora.vision.watch import Watcher

    analyzer = camera_tool.analyzer if camera_tool is not None else None
    try:
        Watcher(engine, analyzer=analyzer).run()
    except VisionUnavailable as error:
        print(f"AURORA: {error}\n")


class VoiceControls:
    """Creates the voice parts on first use, so AURORA starts fast without them."""

    def __init__(self, engine):
        self.engine = engine
        self.session = None
        self.speak_replies = False

    def get(self):
        if self.session is None:
            from aurora.voice.listener import Listener
            from aurora.voice.session import VoiceSession
            from aurora.voice.speaker import Pyttsx3Speaker

            print("Loading the speech model...")
            listener = Listener()
            try:
                speaker = Pyttsx3Speaker()
            except Exception as error:
                print(f"(Text-to-speech isn't available: {error}. Replies will be text only.)")
                speaker = None
            self.session = VoiceSession(self.engine, listener, speaker)
        return self.session

    def run(self, action: str) -> None:
        from aurora.voice.listener import VoiceUnavailable

        try:
            session = self.get()
            if action == "listen":
                session.listen_once()
            else:
                session.run_hands_free()
        except VoiceUnavailable as error:
            print(f"AURORA: {error}\n")

    def speak(self, text: str) -> None:
        if self.speak_replies:
            try:
                self.get().say(text)
            except Exception as error:
                print(f"(Couldn't speak: {error})")


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
    print("Type 'route <message>' to see how a message would be routed.")
    print("Type 'plan <message>' to see how a message would be split into steps.")
    print("Type 'listen' to ask by voice, 'voice on' for hands-free, 'speak on/off' to hear replies.")

    print("Type 'watch' to have AURORA watch the room for motion (Ctrl+C stops it).\n")

    voice = VoiceControls(engine)

    # Show which research agent is working during a research session.
    research_tool = engine.tool_manager.get("Research")
    if research_tool is not None:
        research_tool.progress = print

    # Reminders fire in the background: show them, then restore the prompt.
    reminders_tool = engine.tool_manager.get("Reminders")
    if reminders_tool is not None:
        reminders_tool.scheduler.notify = lambda text: print(f"\n\a{text}\nYou: ", end="", flush=True)

    # Show when the camera turns on and off for "what do you see?" / "read this".
    camera_tool = engine.tool_manager.get("Camera")
    if camera_tool is not None:
        camera_tool.notify = print

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

            if user_input.lower().startswith("plan "):
                print_plan(engine.plan(user_input[5:]))
                continue

            if user_input.lower() in ("listen", "voice on"):
                voice.run(user_input.lower())
                continue

            if user_input.lower() == "watch":
                watch(engine, camera_tool)
                continue

            if user_input.lower() in ("speak on", "speak off"):
                voice.speak_replies = user_input.lower() == "speak on"
                print(f"AURORA: Spoken replies {'on' if voice.speak_replies else 'off'}.\n")
                continue

            try:
                response = engine.process_message(user_input)
                print(f"\nAURORA: {response}\n")
                voice.speak(response)

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
