import os
import sys
import json
import time
import urllib.parse
from datetime import datetime
from pathlib import Path
import requests
from playwright.sync_api import sync_playwright
from app.config import BROWSER_MODE, BASE_DIR
from app.logger import logger

SESSION_FILE = BASE_DIR / "session.json"
UPWORK_GRAPHQL_URL = "https://www.upwork.com/api/graphql/v1"

SEARCH_JOBS_QUERY = """
query VisitorJobSearch($requestVariables: VisitorJobSearchV1Request!) {
  search {
    universalSearchNuxt {
      visitorJobSearchV1(request: $requestVariables) {
        results {
          id
          title
          description
          ontologySkills {
            prefLabel
          }
          jobTile {
            job {
              id
              ciphertext: cipherText
              jobType
              hourlyBudgetMax
              hourlyBudgetMin
              contractorTier
              publishTime
            }
          }
        }
      }
    }
  }
}
"""

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
}
"""

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
    def refresh_token() -> dict:
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
                page.goto("https://www.upwork.com/", wait_until="domcontentloaded", timeout=25000)
                page.wait_for_timeout(5000)
            except Exception as e:
                logger.warning(f"Stealth auth navigation warning: {e}")
                
            cookies = context.cookies()
            browser.close()
            
            cookie_dict = {c["name"]: c["value"] for c in cookies}
            token = cookie_dict.get("visitor_gql_token") or cookie_dict.get("oauth2v3_user_gql_token")
            
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

def execute_graphql(query: str, variables: dict = None, retries: int = 1, alias: str = None) -> dict:
    session_data = AuthManager.get_token_data()
    token = session_data["token"]
    cookies_dict = session_data["cookies"]
    
    headers = {
        "Accept": "application/json, text/plain, */*",
        "Accept-Language": "en-US,en;q=0.9",
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Origin": "https://www.upwork.com",
        "Referer": "https://www.upwork.com/nx/search/jobs/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Sec-Fetch-Mode": "cors",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-Dest": "empty",
        "Connection": "keep-alive"
    }
    
    url = UPWORK_GRAPHQL_URL
    if alias:
        url = f"{UPWORK_GRAPHQL_URL}?alias={alias}"
        
    try:
        resp = requests.post(
            url,
            json={"query": query, "variables": variables or {}},
            headers=headers,
            cookies=cookies_dict,
            timeout=30
        )
        
        if resp.status_code in (401, 403):
            if retries > 0:
                logger.warning(f"HTTP {resp.status_code} received. Refreshing credentials and retrying...")
                if SESSION_FILE.exists():
                    try:
                        SESSION_FILE.unlink()
                    except Exception:
                        pass
                return execute_graphql(query, variables, retries - 1, alias)
            else:
                logger.error("Cloudflare challenge block active on this network.")
                logger.error("CRITICAL GUIDE REMINDER (Page 31): Please switch your network to a VPN or Mobile Hotspot to bypass shared IP blocks!")
                raise Exception(f"HTTP {resp.status_code} Forbidden: Cloudflare challenge page returned.")
                
        if resp.status_code != 200:
            raise Exception(f"HTTP {resp.status_code}: {resp.text[:200]}")
            
        return resp.json()
    except Exception as e:
        raise e

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
                
        variables = {
            "requestVariables": {
                "userQuery": search_query,
                "sort": "recency",
                "paging": {"offset": 0, "count": 20}
            }
        }
        
        try:
            res = execute_graphql(SEARCH_JOBS_QUERY, variables, alias="visitorJobSearch")
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
                    job_type_str = "FIXED" if job_info.get("jobType") == 1 else "HOURLY"
                    
                    budget = None
                    if job_type_str == "FIXED":
                        budget = "Fixed-Price"
                    else:
                        min_b = job_info.get("hourlyBudgetMin")
                        max_b = job_info.get("hourlyBudgetMax")
                        if min_b or max_b:
                            budget = f"${min_b or 0}-${max_b or 0}/hr"

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
                        "published_date": datetime.utcnow()
                    })
                except Exception:
                    continue
            return jobs
        except Exception as e:
            logger.error(f"Search failed: {e}")
            return []

    @staticmethod
    def fetch_job_details(job_id: str) -> dict:
        try:
            res = execute_graphql(JOB_DETAILS_BASIC_QUERY, {"id": job_id}, alias="JobDetailsBasic")
            posting = res.get("data", {}).get("marketplaceJobPosting", {}) or {}
            content = posting.get("content", {})
            
            client_pub = posting.get("clientCompanyPublic", {}) or {}
            location = client_pub.get("location", {}) or {}
            country = location.get("country")
            evaluation = client_pub.get("evaluation", {}) or {}
            rating = evaluation.get("individualFeedbackRating")

            return {
                "description": content.get("description"),
                "client_country": country,
                "client_rating": rating
            }
        except Exception as e:
            logger.error(f"Failed to fetch job details: {e}")
            return {}