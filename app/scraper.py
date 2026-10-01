import os
import sys
import json
import time
import threading
import urllib.parse
from datetime import datetime
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright
from app.config import BROWSER_MODE, BASE_DIR
from app.logger import logger

SESSION_FILE = BASE_DIR / "session.json"
UPWORK_GRAPHQL_URL = "https://www.upwork.com/api/graphql/v1"

def parse_publish_time(pub_time) -> datetime:
    """Robust parser for Upwork publishTime field formats."""
    if not pub_time:
        return datetime.utcnow()
    
    if isinstance(pub_time, (int, float)):
        try:
            if pub_time > 1e11:  # Milliseconds epoch
                return datetime.utcfromtimestamp(pub_time / 1000.0)
            return datetime.utcfromtimestamp(pub_time)
        except Exception:
            return datetime.utcnow()
            
    if isinstance(pub_time, str):
        if pub_time.isdigit():
            try:
                val = float(pub_time)
                if val > 1e11:
                    return datetime.utcfromtimestamp(val / 1000.0)
                return datetime.utcfromtimestamp(val)
            except Exception:
                pass
        
        try:
            # Replace trailing Z with UTC offset
            clean_str = pub_time.replace("Z", "+00:00")
            return datetime.fromisoformat(clean_str)
        except Exception:
            pass
            
    return datetime.utcnow()


SEARCH_JOBS_QUERY = """
query VisitorJobSearch($requestVariables: VisitorJobSearchV1Request!) {
  search {
    universalSearchNuxt {
      visitorJobSearchV1(request: $requestVariables) {
        paging {
          total
          offset
          count
        }
        facets {
          jobType {
            key
            value
          }
          workload {
            key
            value
          }
          clientHires {
            key
            value
          }
          durationV3 {
            key
            value
          }
          amount {
            key
            value
          }
          contractorTier {
            key
            value
          }
          contractToHire {
            key
            value
          }
        }
        results {
          id
          title
          description
          relevanceEncoded
          ontologySkills {
            uid
            parentSkillUid
            prefLabel
            prettyName: prefLabel
            freeText
            highlighted
          }
          jobTile {
            job {
              id
              ciphertext: cipherText
              jobType
              weeklyRetainerBudget
              hourlyBudgetMax
              hourlyBudgetMin
              hourlyEngagementType
              contractorTier
              sourcingTimestamp
              createTime
              publishTime
              hourlyEngagementDuration {
                rid
                label
                weeks
                mtime
                ctime
              }
              fixedPriceAmount {
                isoCurrencyCode
                amount
              }
              fixedPriceEngagementDuration {
                id
                rid
                label
                weeks
                ctime
                mtime
              }
            }
          }
        }
      }
    }
  }
}
"""

# Minimal details query — only requests fields that are confirmed to work
# with visitor (guest) tokens. Client details beyond country are not
# accessible without authentication, so we default them gracefully.
JOB_DETAILS_BASIC_QUERY = """
query JobDetailsBasic($id: ID!) {
  marketplaceJobPosting(id: $id) {
    id
    content {
      title
      description
    }
    clientCompanyPublic {
      location {
        country
      }
      evaluation {
        individualFeedbackRating
      }
    }
  }
}"""

class AuthManager:
    @staticmethod
    def get_token_data() -> dict:
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                if time.time() - data.get("timestamp", 0) < 12 * 60 * 60:
                    return data
            except Exception:
                pass
        return AuthManager.refresh_token()

    @staticmethod
    def _refresh_token_worker() -> dict:
        logger.info("Extracting fresh token using Playwright browser auth...")
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=BROWSER_MODE == "headless",
                args=["--disable-blink-features=AutomationControlled"]
            )
            context = browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            )
            page = context.new_page()
            page.add_init_script("delete navigator.webdriver")
            
            try:
                page.goto("https://www.upwork.com/nx/search/jobs/?q=testing", wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(6000)
            except Exception as e:
                logger.warning(f"Stealth auth navigation warning: {e}")
                
            cookies = context.cookies()
            browser.close()
            
            cookie_dict = {c["name"]: c["value"] for c in cookies}
            token = (cookie_dict.get("visitor_topnav_gql_token")
                     or cookie_dict.get("visitor_gql_token")
                     or cookie_dict.get("oauth2v3_user_gql_token"))
            
            if not token:
                for name, value in cookie_dict.items():
                    if "gql_token" in name:
                        token = value
                        break
                        
            if not token:
                raise Exception("GraphQL authentication token not found in cookies.")
                
            data = {
                "token": token,
                "timestamp": time.time(),
                "cookies": cookie_dict
            }
            
            with open(SESSION_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
                
            logger.info("Successfully refreshed and cached visitor credentials.")
            return data

    @staticmethod
    def refresh_token() -> dict:
        res = []
        err = []
        def _target():
            try:
                res.append(AuthManager._refresh_token_worker())
            except Exception as e:
                err.append(e)
        t = threading.Thread(target=_target, daemon=True, name="AuthRefreshThread")
        t.start()
        t.join(timeout=60)
        if err:
            raise err[0]
        if not res:
            raise Exception("AuthManager.refresh_token timed out.")
        return res[0]

    @staticmethod
    def _proactive_refresh_loop(interval_hours: int = 11):
        """Background loop that refreshes the token before it expires.
        On first run it calculates how long the current token still has left
        and sleeps exactly that long, so the refresh always happens right at
        the interval boundary regardless of when the bot started.
        """
        while True:
            try:
                wait_seconds = interval_hours * 3600  # default: full interval

                # If a cached token already exists, sleep only for the remaining lifetime
                if SESSION_FILE.exists():
                    try:
                        with open(SESSION_FILE, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        age = time.time() - data.get("timestamp", 0)
                        remaining = (interval_hours * 3600) - age
                        # If more than 60 seconds remain, wait that long; otherwise refresh now
                        wait_seconds = max(remaining, 0)
                    except Exception:
                        pass  # fall back to full interval

                if wait_seconds > 60:
                    hours_left = wait_seconds / 3600
                    logger.info(f"[Auth] Proactive token refresh scheduled in {hours_left:.2f} hour(s).")
                    time.sleep(wait_seconds)

                logger.info("[Auth] Proactive token refresh triggered — fetching fresh credentials...")
                AuthManager.refresh_token()

            except Exception as e:
                logger.warning(f"[Auth] Proactive token refresh failed: {e}. Retrying in 30 minutes.")
                time.sleep(30 * 60)  # back-off before retrying

    @staticmethod
    def start_proactive_refresh(interval_hours: int = 11):
        """Spin up the proactive refresh daemon thread. Call once at startup."""
        thread = threading.Thread(
            target=AuthManager._proactive_refresh_loop,
            args=(interval_hours,),
            daemon=True,
            name="ProactiveTokenRefresh",
        )
        thread.start()
        logger.info(f"[Auth] Proactive token refresh thread started (interval: {interval_hours}h).")

def _browser_graphql_worker(url: str, query: str, variables: dict, token: str) -> dict:
    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=BROWSER_MODE == "headless",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-sandbox",
                "--disable-dev-shm-usage"
            ]
        )
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )
        )
        page = context.new_page()
        page.add_init_script("delete navigator.webdriver")

        # Inject cached cookies if available
        if SESSION_FILE.exists():
            try:
                with open(SESSION_FILE, "r", encoding="utf-8") as f:
                    session_data = json.load(f)
                cookie_list = []
                for cname, cval in session_data.get("cookies", {}).items():
                    cookie_list.append({
                        "name": cname,
                        "value": str(cval),
                        "domain": ".upwork.com",
                        "path": "/"
                    })
                if cookie_list:
                    context.add_cookies(cookie_list)
            except Exception as ce:
                logger.warning(f"Error loading cached cookies: {ce}")

        try:
            page.goto("https://www.upwork.com/", wait_until="domcontentloaded", timeout=25000)
            page.wait_for_timeout(3000)
        except Exception as e:
            logger.warning(f"Browser navigation warning: {e}")

        # Extract latest token from live browser cookies
        all_cookies = {c["name"]: c["value"] for c in context.cookies()}
        live_token = (all_cookies.get("visitor_topnav_gql_token")
                      or all_cookies.get("visitor_gql_token")
                      or all_cookies.get("oauth2v3_user_gql_token")
                      or token)

        result = page.evaluate("""
            async ([targetUrl, gqlQuery, gqlVariables, authHeader]) => {
                const headers = {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json, text/plain, */*'
                };
                if (authHeader) {
                    headers['Authorization'] = 'Bearer ' + authHeader;
                }
                try {
                    const resp = await fetch(targetUrl, {
                        method: 'POST',
                        headers: headers,
                        body: JSON.stringify({ query: gqlQuery, variables: gqlVariables })
                    });
                    if (!resp.ok) {
                        return { error_status: resp.status, text: await resp.text() };
                    }
                    return await resp.json();
                } catch (err) {
                    return { error_status: 500, text: err.toString() };
                }
            }
        """, [url, query, variables or {}, live_token or ""])

        browser.close()
        return result


def execute_graphql(query: str, variables: dict = None, retries: int = 1, alias: str = None) -> dict:
    """Execute GraphQL query in an isolated thread to prevent any asyncio event loop conflicts."""
    url = f"{UPWORK_GRAPHQL_URL}?alias={alias}" if alias else UPWORK_GRAPHQL_URL
    session_data = AuthManager.get_token_data()
    cached_token = session_data.get("token", "")

    result_box = []
    error_box = []

    def thread_target():
        try:
            res = _browser_graphql_worker(url, query, variables, cached_token)
            result_box.append(res)
        except Exception as exc:
            error_box.append(exc)

    t = threading.Thread(target=thread_target, daemon=True, name="PlaywrightGraphQLWorker")
    t.start()
    t.join(timeout=65)

    if error_box:
        logger.error(f"In-browser GraphQL error: {error_box[0]}")
        raise error_box[0]

    if not result_box:
        raise Exception("In-browser GraphQL execution timed out after 65s.")

    result = result_box[0]
    if isinstance(result, dict) and "error_status" in result:
        status = result["error_status"]
        if status in (401, 403) and retries > 0:
            logger.warning(f"In-browser GraphQL returned {status}. Refreshing credentials...")
            if SESSION_FILE.exists():
                try:
                    SESSION_FILE.unlink()
                except Exception:
                    pass
            AuthManager.refresh_token()
            return execute_graphql(query, variables, retries - 1, alias)
        raise Exception(f"In-browser GraphQL HTTP {status}: {result.get('text', '')[:200]}")

    return result

class JobScraper:
    @staticmethod
    def search_jobs(search_query: str) -> list:
        if "upwork.com" in search_query:
            try:
                parsed = urllib.parse.urlparse(search_query)
                query_params = urllib.parse.parse_qs(parsed.query)
                search_query = query_params.get("q", [""])[0]
            except Exception:
                pass
                
        try:
            res = execute_graphql(
                SEARCH_JOBS_QUERY,
                {
                    "requestVariables": {
                        "userQuery": search_query,
                        "sort": "recency",
                        "paging": {"offset": 0, "count": 10}
                    }
                },
                alias="visitorJobSearch"
            )
            search_data = res.get("data", {}).get("search", {}).get("universalSearchNuxt", {})
            results = search_data.get("visitorJobSearchV1", {}).get("results", [])
            
            jobs = []
            for item in results:
                try:
                    job_tile = item.get("jobTile", {})
                    job_info = job_tile.get("job", {})
                    ciphertext = job_info.get("ciphertext")
                    if not ciphertext:
                        continue
                        
                    skills = [s.get("prefLabel") for s in item.get("ontologySkills", []) if s.get("prefLabel")]
                    
                    # Robust job type detection
                    job_type_val = job_info.get("jobType")
                    fixed_amt = job_info.get("fixedPriceAmount")
                    
                    is_fixed = False
                    if job_type_val == 1:
                        is_fixed = True
                    elif isinstance(job_type_val, str) and job_type_val.upper() in ("FIXED", "FIXED_PRICE", "FIXEDPRICE"):
                        is_fixed = True
                    elif fixed_amt is not None:
                        is_fixed = True
                        
                    budget = None
                    if is_fixed:
                        job_type_str = "FIXED"
                        if fixed_amt and fixed_amt.get("amount") is not None:
                            budget = f"${fixed_amt['amount']}"
                        else:
                            budget = "Fixed-Price"
                    else:
                        job_type_str = "HOURLY"
                        min_b = job_info.get("hourlyBudgetMin")
                        max_b = job_info.get("hourlyBudgetMax")
                        if min_b or max_b:
                            budget = f"${min_b or 0}-${max_b or 0}/hr"
                        elif fixed_amt is not None:
                            # Fallback: if classified as hourly but has fixed price details
                            job_type_str = "FIXED"
                            if fixed_amt.get("amount") is not None:
                                budget = f"${fixed_amt['amount']}"
                            else:
                                budget = "Fixed-Price"

                    experience_map = {1: "Entry", 2: "Intermediate", 3: "Expert"}
                    exp_level = experience_map.get(job_info.get("contractorTier"), "Intermediate")

                    jobs.append({
                        "id": ciphertext,
                        "numeric_id": str(job_info.get("id")),
                        "title": item.get("title"),
                        "description": item.get("description", ""),
                        "url": f"https://www.upwork.com/jobs/{ciphertext}",
                        "job_type": job_type_str,
                        "budget": budget,
                        "experience_level": exp_level,
                        "skills": skills,
                        "published_date": parse_publish_time(job_info.get("publishTime")),
                        "proposal_count": job_info.get("totalApplicants"),
                        "project_duration": (
                            (job_info.get("hourlyEngagementDuration") or {}).get("label")
                            or (job_info.get("fixedPriceEngagementDuration") or {}).get("label")
                        ),
                    })
                except Exception:
                    continue
            return jobs
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    @staticmethod
    def fetch_job_details(job_id: str) -> dict:
        """Fetch client info (country, rating) for a specific job posting."""
        try:
            res = execute_graphql(
                JOB_DETAILS_BASIC_QUERY,
                {"id": job_id},
                alias="jobDetailsBasic"
            )
            posting = res.get("data", {}).get("marketplaceJobPosting") or {}
            client_info = posting.get("clientCompanyPublic") or {}
            location = client_info.get("location") or {}
            evaluation = client_info.get("evaluation") or {}

            details = {}
            country = location.get("country")
            if country:
                details["client_country"] = country

            rating = evaluation.get("individualFeedbackRating")
            if rating is not None:
                try:
                    details["client_rating"] = float(rating)
                except (ValueError, TypeError):
                    pass

            content = posting.get("content") or {}
            if content.get("description"):
                details["description"] = content["description"]

            return details
        except Exception as e:
            logger.warning(f"Could not fetch job details for {job_id}: {e}")
            return {}