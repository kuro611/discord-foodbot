from keep_alive import keep_alive
from bot import run_bot  # bot.py で run_bot() を定義している前提

if __name__ == "__main__":
    keep_alive()
    run_bot()