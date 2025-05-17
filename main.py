from keep_alive import keep_alive
from bot_minimal import bot
import os

if __name__ == "__main__":
    keep_alive()
    bot.run(os.environ.get("DISCORD_TOKEN"))