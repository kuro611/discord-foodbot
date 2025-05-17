from keep_alive import keep_alive
from bot import run_bot  # bot.py で run_bot() を定義している前提

if __name__ == "__main__":
    keep_alive()
    print("keep_alive呼び出し")
    run_bot()
    print("run_bot呼び出し")