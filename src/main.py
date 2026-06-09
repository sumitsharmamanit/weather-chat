import asyncio
import os
import signal
import sys

from dotenv import load_dotenv
load_dotenv()

from llm import run_turn


async def main() -> None:
    loop = asyncio.get_running_loop()
    loop.add_signal_handler(
        signal.SIGINT,
        lambda: [t.cancel() for t in asyncio.all_tasks(loop) if not t.done()],
    )

    history: list = []
    print("Elyos Chat — type 'quit' to exit, Ctrl+C to cancel a running request\n")

    while True:
        try:
            user_input = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nGoodbye!")
            break

        if not user_input:
            continue
        if user_input.lower() in {"quit", "exit", "q"}:
            print("Goodbye!")
            break

        print("Assistant: ", end="", flush=True)

        task = asyncio.create_task(run_turn(user_input, history))
        try:
            await task
        except asyncio.CancelledError:
            print()
        except Exception as e:
            print(f"\n[Error: {e}]", file=sys.stderr)


def run() -> None:
    asyncio.run(main())


if __name__ == "__main__":
    run()
