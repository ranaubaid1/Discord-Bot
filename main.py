import signal
import sys
import asyncio
import threading
from app.logger import logger
from app.config import DISCORD_BOT_TOKEN
from app.discord import bot, db
from app.dashboard_server import DashboardHTTPHandler
from app.scraper import AuthManager
from http.server import HTTPServer

# Global reference so signal_handler can shut the server down cleanly
_dashboard_server: HTTPServer | None = None


def start_dashboard_server(port: int = 8080):
    """Find a free port and run the dashboard HTTP server in a daemon thread."""
    global _dashboard_server
    for p in [port, 8081, 8000, 5000, 5001]:
        try:
            server = HTTPServer(("0.0.0.0", p), DashboardHTTPHandler)
            _dashboard_server = server
            logger.info(f"Dashboard server started → http://localhost:{p}/")
            print("==================================================")
            print(f"🟢 [Dashboard] Live at http://localhost:{p}/")
            print("==================================================")
            # serve_forever blocks, so run it in a daemon thread
            thread = threading.Thread(target=server.serve_forever, daemon=True, name="DashboardThread")
            thread.start()
            return
        except OSError:
            logger.warning(f"Port {p} in use, trying next...")
            continue
    logger.error("Could not bind dashboard server on any port. Dashboard will be unavailable.")


def save_current_state():
    """Persist any in-memory state before shutdown (DB writes are immediate)."""
    logger.info("Saving current state before shutdown...")
    try:
        db.cleanup_old_jobs()
        logger.info("State saved successfully.")
    except Exception as e:
        logger.warning(f"Could not save state on shutdown: {e}")


def signal_handler(sig, frame):
    """Handle SIGINT / SIGTERM for graceful shutdown."""
    logger.info("Shutdown signal received — initiating graceful shutdown...")

    # Save state (flush DB, run cleanup)
    save_current_state()

    # Stop dashboard HTTP server
    if _dashboard_server:
        try:
            _dashboard_server.shutdown()
            logger.info("Dashboard server stopped.")
        except Exception as e:
            logger.warning(f"Error stopping dashboard server: {e}")

    # Close database connection
    try:
        db.close()
        logger.info("Database connection closed.")
    except Exception as e:
        logger.warning(f"Error closing database: {e}")

    # Stop Discord bot (schedule coroutine close from sync context)
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            loop.create_task(bot.close())
        else:
            loop.run_until_complete(bot.close())
        logger.info("Discord bot stopped.")
    except Exception as e:
        logger.warning(f"Error stopping Discord bot: {e}")

    logger.info("Shutdown complete. Exiting.")
    sys.exit(0)


# Register signal handlers
signal.signal(signal.SIGINT, signal_handler)
if hasattr(signal, "SIGTERM"):   # SIGTERM not available on all Windows versions
    signal.signal(signal.SIGTERM, signal_handler)


def main():
    logger.info("Initializing Upwork Monitor Bot via main.py...")
    if not DISCORD_BOT_TOKEN:
        logger.critical("DISCORD_BOT_TOKEN is missing in .env file!")
        sys.exit(1)

    # Start proactive token refresh (refreshes every 11 hours in background)
    AuthManager.start_proactive_refresh(interval_hours=11)

    # Start dashboard in background thread before the bot blocks the main thread
    start_dashboard_server()

    logger.info("Starting Discord bot client...")
    try:
        bot.run(DISCORD_BOT_TOKEN)
    except Exception as e:
        logger.critical(f"Critical error running Discord bot: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
