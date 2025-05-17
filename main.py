from keep_alive import keep_alive
from bot import run_bot  

if __name__ == "__main__":
    keep_alive()
    print("keep_alive呼び出し")
    run_bot()