import os
import asyncio
import discord
from discord.ext import commands, tasks
from datetime import datetime
from app.config import DISCORD_BOT_TOKEN, DISCORD_CHANNEL_MAP, UPWORK_SEARCH_URLS, MONITOR_INTERVAL
from app.logger import logger
from app.database import DBManager, init_db
from app.scraper import JobScraper

intents = discord.Intents.default()
intents.message_content = os.getenv("ENABLE_MESSAGE_CONTENT", "false").lower() == "true"
bot = commands.Bot(command_prefix="!", intents=intents)
db = DBManager

is_scraping = False

class ThreadManager:
    @staticmethod
    def format_embed(job: dict) -> discord.Embed:
        embed = discord.Embed(
            title=job["title"],
            url=job["url"],
            color=discord.Color.from_rgb(20, 168, 0),
            description=job["description"][:1000] + ("..." if len(job["description"]) > 1000 else "")
        )
        embed.add_field(name="💼 Job Type", value=job.get("job_type", "N/A"), inline=True)
        embed.add_field(name="💰 Budget", value=job.get("budget") or "N/A", inline=True)
        embed.add_field(name="📈 Experience Level", value=job.get("experience_level", "N/A"), inline=True)

        country = job.get("client_country") or "Unknown"
        rating = job.get("client_rating")
        rating_str = f"⭐ {rating:.1f}" if rating else "No rating"
        embed.add_field(name="🌍 Client Location", value=country, inline=True)
        embed.add_field(name="⭐️ Client Rating", value=rating_str, inline=True)

        skills = job.get("skills", [])
        skills_str = ", ".join(skills[:8]) if skills else "None specified"
        if len(skills) > 8:
            skills_str += f" (+{len(skills) - 8} more)"
        embed.add_field(name="🛠️ Skills", value=skills_str, inline=False)

        pub_date = job.get("published_date")
        if isinstance(pub_date, str):
            try:
                pub_date = datetime.fromisoformat(pub_date)
            except ValueError:
                pass
        pub_str = pub_date.strftime("%Y-%m-%d %H:%M:%S UTC") if isinstance(pub_date, datetime) else "Recently"
        embed.set_footer(text=f"Published: {pub_str} | Upwork Monitor")
        return embed

    @staticmethod
    async def create_job_thread(channel: discord.TextChannel, job: dict) -> bool:
        try:
            embed = ThreadManager.format_embed(job)
            main_message = await channel.send(embed=embed)
            
            thread_name = f"💬 {job['title'][:90]}"
            thread = await main_message.create_thread(
                name=thread_name,
                auto_archive_duration=1440
            )
            
            desc = job.get("description", "No description available.")
            if len(desc) > 1000:
                desc_chunks = [desc[i:i+1900] for i in range(0, len(desc), 1900)]
                await thread.send("**📝 Complete Job Description:**")
                for chunk in desc_chunks:
                    await thread.send(chunk)
            
            skills_all = ", ".join(job.get("skills", [])) or "None specified"
            meta_info = (
                f"**📋 Job Details & Metadata:**\n"
                f"• **Direct Application Link:** {job['url']}\n"
                f"• **Job Type:** {job.get('job_type')}\n"
                f"• **Budget:** {job.get('budget') or 'N/A'}\n"
                f"• **Experience Level:** {job.get('experience_level')}\n"
                f"• **Client Location:** {job.get('client_country') or 'Unknown'}\n"
                f"• **Client Rating:** {f'{job.get('client_rating'):.1f} / 5' if job.get('client_rating') else 'N/A'}\n"
                f"• **Required Skills:** {skills_all}\n"
            )
            await thread.send(meta_info)
            return True
        except Exception as e:
            logger.error(f"Failed to post job/create thread for {job['id']}: {e}")
            return False


_monitor_started = False

@bot.event
async def on_ready():
    global _monitor_started
    logger.info(f"Discord Bot loaded as {bot.user.name} (ID: {bot.user.id})")
    init_db()
    if not _monitor_started:
        _monitor_started = True
        monitor_loop.start()
        logger.info("Background job monitoring loop started.")

@tasks.loop(seconds=MONITOR_INTERVAL)
async def monitor_loop():
    global is_scraping
    if is_scraping:
        logger.warning("Scrape loop is already running. Skipping this cycle.")
        return
    is_scraping = True
    logger.info("Executing job monitor scraping cycle...")
    try:
        await run_scrape_cycle()
        await post_new_jobs()
    except Exception as e:
        logger.error(f"Error during scrape cycle execution: {e}")
    finally:
        is_scraping = False
        logger.info("Scraping cycle completed.")

async def run_scrape_cycle():
    for url in UPWORK_SEARCH_URLS:
        logger.info(f"Scraping URL: {url}")
        jobs = await asyncio.to_thread(JobScraper.search_jobs, url)
        for job in jobs:
            if DBManager.is_duplicate(job["id"]):
                continue
            await asyncio.sleep(2)
            details = await asyncio.to_thread(JobScraper.fetch_job_details, job["numeric_id"])
            if details:
                if details.get("description"):
                    job["description"] = details["description"]
                job["client_country"] = details.get("client_country")
                job["client_rating"] = details.get("client_rating")
            DBManager.add_job(job)
        await asyncio.sleep(5)

async def post_new_jobs():
    unposted_jobs = DBManager.get_unposted_jobs()
    if not unposted_jobs:
        logger.info("No new unposted jobs found in database.")
        return
    logger.info(f"Found {len(unposted_jobs)} new unposted jobs to publish.")
    for job_obj in unposted_jobs:
        job = job_obj.to_dict()
        channel_id = get_target_channel_id(job)
        if not channel_id:
            continue
        channel = bot.get_channel(channel_id)
        if not channel:
            try:
                channel = await bot.fetch_channel(channel_id)
            except Exception as e:
                logger.error(f"Could not retrieve channel ID {channel_id}: {e}")
                continue
        if channel:
            success = await ThreadManager.create_job_thread(channel, job)
            if success:
                DBManager.mark_as_posted(job["id"])
                await asyncio.sleep(3)

def get_target_channel_id(job: dict) -> int | None:
    title_lower = job["title"].lower() if job.get("title") else ""
    desc_lower = job["description"].lower() if job.get("description") else ""
    skills_lower = [s.lower() for s in job.get("skills", [])]
    for keyword, ch_id in DISCORD_CHANNEL_MAP.items():
        if keyword == "default":
            continue
        if keyword in title_lower or keyword in desc_lower or any(keyword in s for s in skills_lower):
            logger.info(f"Job {job['id']} matched keyword '{keyword}'. Routing to channel {ch_id}")
            return ch_id
    default_ch_id = DISCORD_CHANNEL_MAP.get("default")
    if default_ch_id:
        logger.info(f"No keyword matched. Routing job {job['id']} to default channel {default_ch_id}")
    return default_ch_id

@bot.command(name="status")
@commands.has_permissions(administrator=True)
async def status_cmd(ctx):
    from sqlalchemy import func
    from app.database import SessionLocal, Job
    session = SessionLocal()
    try:
        total_jobs = session.query(func.count(Job.id)).scalar()
        unposted_jobs = session.query(func.count(Job.id)).filter(Job.posted_to_discord == False).scalar()
    finally:
        session.close()
    embed = discord.Embed(title="🤖 Upwork Job Monitor Status", color=discord.Color.blue())
    embed.add_field(name="📡 Monitored URLs", value=str(len(UPWORK_SEARCH_URLS)), inline=True)
    embed.add_field(name="🔁 Loop Interval", value=f"{MONITOR_INTERVAL}s", inline=True)
    embed.add_field(name="📊 Total DB Jobs", value=str(total_jobs), inline=True)
    embed.add_field(name="📬 Unposted Jobs", value=str(unposted_jobs), inline=True)
    embed.add_field(name="🎛️ Scrape active", value="Yes" if is_scraping else "No", inline=True)
    channels_desc = "\n".join([f"• `{k}` -> <#{v}>" for k, v in DISCORD_CHANNEL_MAP.items()])
    embed.add_field(name="📂 Mapped Channels", value=channels_desc or "None configured", inline=False)
    await ctx.send(embed=embed)

@bot.command(name="force_check")
@commands.has_permissions(administrator=True)
async def force_check_cmd(ctx):
    global is_scraping
    if is_scraping:
        await ctx.send("⚠️ A scraping cycle is already running. Please wait.")
        return
    msg = await ctx.send("🔄 Triggering manual scraping and posting cycle. Please check console/logs...")
    try:
        is_scraping = True
        await run_scrape_cycle()
        await post_new_jobs()
        await msg.edit(content="✅ Manual scraping cycle successfully executed. Check channels for new jobs!")
    except Exception as e:
        logger.error(f"Manual scrape trigger failed: {e}")
        await msg.edit(content=f"❌ Manual scraping cycle failed: {e}")
    finally:
        is_scraping = False

@bot.command(name="list_urls")
@commands.has_permissions(administrator=True)
async def list_urls_cmd(ctx):
    if not UPWORK_SEARCH_URLS:
        await ctx.send("No Upwork search URLs are currently monitored.")
        return
    urls_list = "\n".join([f"{idx+1}. <{url}>" for idx, url in enumerate(UPWORK_SEARCH_URLS)])
    await ctx.send(f"📋 **Monitored Upwork Search URLs:**\n{urls_list}")
