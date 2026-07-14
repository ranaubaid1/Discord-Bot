import sys
from app.logger import logger
from app.database import init_db
from app.config import DISCORD_BOT_TOKEN
from app.discord import bot

def main():
    logger.info("Initializing Upwork Monitor Bot via main.py...")
    try:
        init_db()
    except Exception as e:
        logger.critical(f"Failed to initialize database: {e}")
        sys.exit(1)
        
    if not DISCORD_BOT_TOKEN:
        logger.critical("DISCORD_BOT_TOKEN is missing in .env file!")
        sys.exit(1)
        
    logger.info("Starting Discord bot client...")
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.critical(f"Critical error running Discord bot: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
