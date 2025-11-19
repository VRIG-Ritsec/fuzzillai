import requests
from bs4 import BeautifulSoup
import json
import time
import re
from typing import Dict, List, Optional
from urllib.parse import urljoin, urlparse, parse_qs
import base64

# --- Selenium Imports ---
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from webdriver_manager.chrome import ChromeDriverManager
import os
# ------------------------

class ChromiumIssueScraper:
    def __init__(self):
        self.base_url = "https://issues.chromium.org"
        self.tracker_url = "https://tracker.ret2happy.com"
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/142.0.7420.0 Safari/537.36'
        })

    def _get_webdriver_instance(self) -> webdriver.Chrome:
        """Initializes and returns a configured headless Chrome WebDriver."""
        chrome_options = Options()
        chrome_options.add_argument("--headless=new")
        chrome_options.add_argument("--no-sandbox")
        chrome_options.add_argument("--disable-dev-shm-usage")
        chrome_options.add_argument(f"user-agent={self.session.headers['User-Agent']}")
        
        CHROME_DRIVER_LOG_PATH = "chromedriver_scrape.log"

        try:
            driver_path = ChromeDriverManager().install()
            service = Service(executable_path=driver_path,
                              log_path=CHROME_DRIVER_LOG_PATH,
                              verbose=False)

            if os.path.exists(CHROME_DRIVER_LOG_PATH):
                 os.remove(CHROME_DRIVER_LOG_PATH)

            driver = webdriver.Chrome(service=service, options=chrome_options)
            return driver
        except Exception as e:
            print(f"Error initializing ChromeDriver: {e}")
            return None

    def _extract_filename_from_element(self, driver, link_element) -> tuple:
        """
        Extract the filename and URL from the attachment link element.
        Returns (filename, url) tuple where url is modified to use download=false.
    
        The link is typically at: .../b-conditional-link/a
        The filename is in a sibling at: .../b-attachment-viewer/div[2]/div[1]
        """
        filename = 'attachment'
        url = link_element.get_attribute('href')
    
        try:
            # CRITICAL: Convert download=true to download=false first
            if url and 'download=true' in url:
                url = url.replace('download=true', 'download=false')
                print(f"    [Selenium] Converted to view URL (download=false)")

            # Navigate up from the link to find the filename element
            # Link is at: .../b-conditional-link/a
            # Filename is at: .../b-attachment-viewer/div[2]/div[1]
            # So we go up 3 levels to b-attachment-viewer, then down to div[2]/div[1]

            WAIT_TIME_FILENAME = 5
            extracted_filename = None

            # XPath to go from link element up to b-attachment-viewer, then to filename div
            filename_xpath = "./ancestor::b-attachment-viewer/div[2]/div[1]"

            # Alternative XPaths to try for filename extraction
            alternative_xpaths = [
                "./ancestor::b-attachment-viewer/div[2]/div[1]",  # Primary path
                "./ancestor::b-attachment-viewer//span[contains(@class, 'bv2-issue-attachment-filename')]",  # Class-based
                "./ancestor::b-attachment-viewer//div[@class='bv2-issue-attachment-details']",  # Details div
                "./ancestor::b-conditional-link/preceding-sibling::*[1]",  # Sibling approach
            ]

            # Custom wait function to wait for the filename element to have non-empty text
            def wait_for_filename_text(d):
                try:
                    # Try each XPath in order until one works
                    for xpath in alternative_xpaths:
                        try:
                            filename_element = link_element.find_element(By.XPATH, xpath)
                            text = filename_element.text.strip()
                            # Return the text if non-empty, otherwise continue to next xpath
                            if text:
                                return text
                        except Exception:
                            continue
                    return False
                except Exception:
                    return False

            # Wait for filename element text to load
            try:
                print(f"    [Selenium] Waiting up to {WAIT_TIME_FILENAME}s for filename text...")

                # Perform the wait. If successful, it returns the non-empty text (the filename).
                extracted_filename = WebDriverWait(driver, WAIT_TIME_FILENAME, 0.2).until(wait_for_filename_text)

                if extracted_filename:
                    filename = extracted_filename
                    print(f"    [Selenium] Extracted filename from XPath: {filename} (via WebDriverWait)")

            except Exception as e:
                # Fallback: Try alternative methods if WebDriverWait times out
                print(f"    [Selenium] Filename extraction failed/timed out: {e}. Trying fallback methods...")

                # Fallback 1: Try each alternative XPath directly without wait
                for xpath in alternative_xpaths:
                    try:
                        filename_element = link_element.find_element(By.XPATH, xpath)
                        extracted_filename = filename_element.text.strip()
                        if extracted_filename:
                            filename = extracted_filename
                            print(f"    [Selenium] Extracted filename (direct fallback with {xpath}): {filename}")
                            break
                    except Exception:
                        continue

                # Fallback 2: Try to extract from URL itself
                if filename == 'attachment' and url:
                    # Look for filename in URL query parameters
                    parsed = urlparse(url)
                    query_params = parse_qs(parsed.query)

                    # Check for 'name' or 'filename' parameters
                    if 'name' in query_params and query_params['name']:
                        filename = query_params['name'][0]
                        print(f"    [Selenium] Extracted filename from URL params: {filename}")
                    elif 'filename' in query_params and query_params['filename']:
                        filename = query_params['filename'][0]
                        print(f"    [Selenium] Extracted filename from URL params: {filename}")
                    # Check if URL ends with a filename-like pattern
                    elif '/' in parsed.path:
                        path_parts = parsed.path.split('/')
                        last_part = path_parts[-1]
                        # If last part looks like a filename (has extension), use it
                        if '.' in last_part and len(last_part) < 100:
                            filename = last_part
                            print(f"    [Selenium] Extracted filename from URL path: {filename}")

                # Fallback 3: Use the link's text content
                if filename == 'attachment':
                    link_text = link_element.text.strip()
                    # Exclude generic text like 'Download' if it's not the actual filename
                    if link_text and link_text.lower() not in ['download', 'attachment', 'view', '']:
                        filename = link_text
                        print(f"    [Selenium] Extracted filename from link text (final fallback): {filename}")

        except Exception as e:
            print(f"    [Selenium] Error processing attachment: {e}")

        return filename, url

    def _try_incremented_attachments(self, base_url: str, filename_map: Dict[int, str] = None) -> List[Dict]:
        """
        Try incrementing the attachment ID in the URL to find additional attachments.
        Always uses download=false to view content.
    
        Args:
            base_url: The base attachment URL
            filename_map: Dictionary mapping attachment IDs to their filenames
        """
        attachments = []
    
        match = re.search(r'/attachments/(\d+)', base_url)
        if not match:
            return []
    
        base_id = int(match.group(1))
        base_url_template = base_url.replace(str(base_id), '{id}')
    
        # Convert download=true to download=false
        if 'download=true' in base_url_template:
            base_url_template = base_url_template.replace('download=true', 'download=false')
            print(f"  [Selenium] Converted base URL template to use download=false")
    
        print(f"  [Selenium] Checking for additional attachments starting from ID {base_id + 1}...")
    
        for offset in range(1, 11):
            new_id = base_id + offset
            new_url = base_url_template.format(id=new_id)

            try:
                response = self.session.head(new_url, timeout=10, allow_redirects=True)

                if response.status_code == 200:
                    print(f"  [Selenium] Found additional attachment at ID {new_id}")

                    # Use the filename from the map if available
                    filename = filename_map.get(new_id, f'attachment_{new_id}')

                    # If still generic, try to get from Content-Disposition header
                    if filename == f'attachment_{new_id}' and 'Content-Disposition' in response.headers:
                        cd = response.headers['Content-Disposition']
                        fname_match = re.search(r'filename="?([^"]+)"?', cd)
                        if fname_match:
                            filename = fname_match.group(1)
                            print(f"  [Selenium] Got filename from Content-Disposition: {filename}")

                    attachments.append({
                        'url': new_url,
                        'name': filename,
                        'location': 'incremented'
                    })
                else:
                    print(f"  [Selenium] No more attachments found (stopped at ID {new_id})")
                    break

            except Exception:
                print(f"  [Selenium] Stopping incremental search at ID {new_id}")
                break
    
        return attachments

    def _get_attachment_details_selenium(self, url: str) -> List[Dict]:
        """
        Uses Selenium to find dynamically loaded attachment URLs and their names.
        Searches description, comments, and tries incremented IDs.
        """
        # XPATH is highly site-specific and brittle, but kept as provided
        DESCRIPTION_XPATH = "/html/body/div[1]/app-root/div/app-root-body/div/div/issue-details-wrapper/mat-sidenav-container/mat-sidenav-content/div/div/article/ng-component/div/b-issue-description/div/div/div[2]/div/b-attachment-viewer/div[2]/div[2]/span[1]/b-conditional-link/a"
        COMMENT_XPATH_PATTERN = "//b-comment//b-attachment-viewer//b-conditional-link/a"

        driver = self._get_webdriver_instance()
        attachments = []
        filename_map = {}  # Map attachment IDs to filenames

        if not driver:
            return []

        try:
            print("  [Selenium] Starting headless browser to find dynamic attachments...")
            driver.get(url)
            time.sleep(3)  # Give Angular time to render

            # First pass: collect ALL attachments and build filename map
            all_attachment_links = driver.find_elements(By.XPATH, "//a[contains(@href, '/attachments/')]")

            for link_elem in all_attachment_links:
                try:
                    filename, view_url = self._extract_filename_from_element(driver, link_elem)

                    # Extract attachment ID from URL
                    match = re.search(r'/attachments/(\d+)', view_url)
                    if match:
                        att_id = int(match.group(1))
                        filename_map[att_id] = filename
                        print(f"  [Selenium] Mapped ID {att_id} -> {filename}")
                except Exception:
                    continue

            # 1. Main description attachment
            try:
                # Use a less brittle wait condition first
                WebDriverWait(driver, 10).until(
                    EC.presence_of_element_located((By.XPATH, "//b-issue-description//a[contains(@href, '/attachments/')]"))
                )

                link_element = driver.find_element(By.XPATH, DESCRIPTION_XPATH)
                filename, view_url = self._extract_filename_from_element(driver, link_element)

                print(f"  [Selenium] Found description attachment: {filename}")
                attachments.append({
                    'url': view_url,
                    'name': filename,
                    'location': 'description'
                })

                # Try incremented attachment IDs with filename map
                base_attachments = self._try_incremented_attachments(view_url, filename_map)
                attachments.extend(base_attachments)

            except Exception:
                print(f"  [Selenium] No attachment in main description")

            # 2. Comment attachments
            try:
                comment_links = driver.find_elements(By.XPATH, COMMENT_XPATH_PATTERN)

                for idx, link_elem in enumerate(comment_links):
                    try:
                        filename, view_url = self._extract_filename_from_element(driver, link_elem)

                        if not any(att['url'] == view_url for att in attachments):
                            print(f"  [Selenium] Found comment attachment: {filename}")
                            attachments.append({
                                'url': view_url,
                                'name': filename,
                                'location': f'comment_{idx}'
                            })

                            comment_attachments = self._try_incremented_attachments(view_url, filename_map)
                            attachments.extend(comment_attachments)

                    except Exception:
                        continue

            except Exception:
                print(f"  [Selenium] No attachments found in comments")

            # 3. Any remaining attachment links not already captured
            try:
                for link_elem in all_attachment_links:
                    try:
                        filename, view_url = self._extract_filename_from_element(driver, link_elem)

                        if not any(att['url'] == view_url for att in attachments):
                            print(f"  [Selenium] Found additional attachment: {filename}")
                            attachments.append({
                                'url': view_url,
                                'name': filename,
                                'location': 'other'
                            })

                    except Exception:
                        continue

            except Exception:
                pass

        except Exception as e:
            print(f"  [Selenium] Error during attachment search: {e}")
        finally:
            if driver:
                driver.quit()

        return attachments

    def scrape_tracker_bugs(self, min_reward: int = 1, page_size: int = 100, max_pages: int = None) -> List[str]:
        """Scrape bug IDs from tracker.ret2happy.com with reward > 0."""
        issue_ids = []
        page = 1
        
        while True:
            if max_pages and page > max_pages:
                break
                
            url = f"{self.tracker_url}/bugs?disclosedTimeRange=&rewardRange={min_reward}%2C&sortBy=newest&page={page}&pageSize={page_size}"
            
            print(f"Scraping tracker page {page}: {url}")
            
            try:
                response = self.session.get(url, timeout=30)
                response.raise_for_status()
                
                soup = BeautifulSoup(response.text, 'html.parser')
                page_issues = self._extract_issue_ids_from_tracker(soup)
                
                if not page_issues:
                    print(f"No more issues found on page {page}")
                    break
                    
                issue_ids.extend(page_issues)
                print(f"Found {len(page_issues)} issues on page {page}")

                break 
                #if not self._has_next_page(soup):
                #    break
                #    
                #page += 1
                #time.sleep(1)
                
            except requests.exceptions.RequestException as e:
                print(f"Error fetching tracker page {page}: {e}")
                break
        
        return list(set(issue_ids))
    
    def _extract_issue_ids_from_tracker(self, soup: BeautifulSoup) -> List[str]:
        """Extract Chromium issue IDs from tracker page."""
        issue_ids = []
        
        chromium_links = soup.find_all('a', href=re.compile(r'issues\.chromium\.org/issues/(\d+)', re.I))
        for link in chromium_links:
            match = re.search(r'issues/(\d+)', link.get('href', ''))
            if match:
                issue_ids.append(match.group(1))
        
        text_content = soup.get_text()
        patterns = [
            r'[Ii]ssue\s+(\d{6,})',
            r'#(\d{6,})',
            r'[Bb]ug\s+(\d{6,})',
            r'crbug\.com/(\d{6,})',
            r'(?:^|\s)(\d{9})(?:\s|$)'
        ]
        
        for pattern in patterns:
            matches = re.finditer(pattern, text_content)
            for match in matches:
                issue_ids.append(match.group(1))
        
        rows = soup.find_all(['tr', 'li', 'div'], class_=re.compile(r'bug|issue|row|item', re.I))
        for row in rows:
            for attr in ['data-issue-id', 'data-bug-id', 'data-id']:
                if row.has_attr(attr):
                    issue_ids.append(row[attr])
            
            links = row.find_all('a', href=True)
            for link in links:
                href = link.get('href', '')
                match = re.search(r'(?:issues?|bugs?)[/:](\d{6,})', href)
                if match:
                    issue_ids.append(match.group(1))
        
        scripts = soup.find_all('script', type='application/json')
        for script in scripts:
            try:
                data = json.loads(script.string)
                self._extract_ids_from_json(data, issue_ids)
            except:
                pass
        
        return issue_ids
    
    def _extract_ids_from_json(self, data, issue_ids: List[str]):
        """Recursively extract issue IDs from JSON data."""
        if isinstance(data, dict):
            for key, value in data.items():
                if key in ['issueId', 'bugId', 'issue_id', 'bug_id', 'id'] and isinstance(value, (str, int)):
                    issue_ids.append(str(value))
                elif isinstance(value, (dict, list)):
                    self._extract_ids_from_json(value, issue_ids)
        elif isinstance(data, list):
            for item in data:
                self._extract_ids_from_json(item, issue_ids)
    
    def _has_next_page(self, soup: BeautifulSoup) -> bool:
        """Check if there's a next page available."""
        next_button = soup.find('a', string=re.compile(r'Next|›|»', re.I))
        if next_button and not next_button.has_attr('disabled'):
            return True
        
        pagination = soup.find_all(['a', 'button'], class_=re.compile(r'next|pagination', re.I))
        for elem in pagination:
            if 'disabled' not in elem.get('class', []):
                return True
        
        return False
    
    def _extract_title(self, soup: BeautifulSoup, url: str = None) -> Optional[str]:
        """
        Extract issue title using multiple methods.
        First tries static HTML, then falls back to Selenium for dynamic content.
        """
        # Method 1: Try static HTML parsing first
        title_elem = soup.find('h3')
        if title_elem:
            title = title_elem.get_text(strip=True)
            if title and title != "Chromium" and len(title) > 5:
                print(f"  [Static] Found title: {title}")
                return title
    
        ## Method 2: Look for title in scripts/JSON
        #scripts = soup.find_all('script')
        #for script in scripts:
        #    if script.string and 'defrostedResourcesJspb' in script.string:
        #        match = re.search(r'"([^"]{20,200}(?:RCE|bypass|vulnerability|exploit|crash)[^"]{0,100})"', script.string, re.I)
        #        if match:
        #            title = match.group(1)
        #            print(f"  [Script] Found title: {title}")
        #            return title
    
        # Method 3: Use Selenium if URL is provided and static methods failed
        if url:
            print("  [Selenium] Attempting to extract title from dynamic content...")
            driver = self._get_webdriver_instance()

            if not driver:
                return None

            try:
                driver.get(url)
                time.sleep(3)  # Let Angular render

                # XPath options to try
                title_xpaths = [
                    "//issue-title//h3",  # More robust relative path
                    "//*[@id='skiplink-navigation-target']/issue-details-wrapper/mat-sidenav-container/mat-sidenav-content/div/header/issue-header/div/div[2]/div[2]/issue-title/div/h3",
                    "/html/body/div[1]/app-root/div/app-root-body/div/div/issue-details-wrapper/mat-sidenav-container/mat-sidenav-content/div/header/issue-header/div/div[2]/div[2]/issue-title/div/h3",
                    "//issue-header//h3",  # Even more general fallback
                ]

                for xpath in title_xpaths:
                    try:
                        # Wait for element to be present
                        title_element = WebDriverWait(driver, 5).until(
                            EC.presence_of_element_located((By.XPATH, xpath))
                        )

                        title = title_element.text.strip()
                        if title and title != "Chromium" and len(title) > 5:
                            print(f"  [Selenium] Found title with xpath: {title}")
                            return title
                    except Exception:
                        continue

                print("  [Selenium] Could not find title with any xpath")

            except Exception as e:
                print(f"  [Selenium] Error extracting title: {e}")
            finally:
                if driver:
                    driver.quit()
    
        return None

    def _extract_description(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract issue description."""
        scripts = soup.find_all('script')
        for script in scripts:
            if script.string and 'VULNERABILITY DETAILS' in script.string:
                match = re.search(r'###\s*VULNERABILITY DETAILS.*?(?=###\s*VERSION|###\s*CREDIT|$)', script.string, re.DOTALL)
                if match:
                    desc = match.group(0)
                    desc = re.sub(r'\\u[0-9a-fA-F]{4}', '', desc)
                    desc = re.sub(r'\\[nrt]', ' ', desc)
                    return desc[:5000]
        
        return None
    
    def _extract_attachments(self, soup: BeautifulSoup, issue_url: str) -> List[Dict]:
        """
        Extract attachment links using Selenium for dynamic content.
        Searches entire thread and tries incremented attachment IDs.
        
        Filename matching is improved in _extract_filename_from_element.
        """
        attachments = []

        # Use Selenium to get all dynamic attachments
        selenium_attachments = self._get_attachment_details_selenium(issue_url)
        
        for att in selenium_attachments:
            name = att['name']
            url = att['url']
            location = att.get('location', 'unknown')
            
            attachments.append({
                'name': name,
                'url': url,
                'download': False
            })

        # Check static HTML for attachment mentions in comments
        comments = soup.find_all(['div', 'article'], class_=re.compile('comment|message', re.I))
        
        for comment_idx, comment in enumerate(comments):
            comment_text = comment.get_text()
            file_mentions = re.finditer(
                r'[Aa]ttached\s+(?:as\s+)?[`"]?([a-zA-Z0-9_.-]+\.(html?|js|py|txt|pdf|zip|cpp?|json|xml))[`"]?',
                comment_text,
                re.I
            )
            
            for match in file_mentions:
                filename = match.group(1)
                
                # Only add if not already found via Selenium/URL (which should have a URL)
                if not any(att['name'] == filename for att in attachments):
                    download_flag = True
                    if filename.lower().endswith(EXCLUDED_EXTENSIONS):
                        download_flag = False
                    
                    attachments.append({
                        'name': filename,
                        'url': None,
                        'source': f'text_mention_comment_{comment_idx}',
                        'download': download_flag
                    })

        # Fallback: static attachment links
        attachment_links = soup.find_all('a', href=re.compile(r'attachment|download|testcase|clusterfuzz', re.I))

        for link in attachment_links:
            href = link.get('href', '')
            url = urljoin(self.base_url, href)
            name = link.get_text(strip=True) or 'attachment'

            if any(att.get('url') == url for att in attachments):
                continue

            if href:
                # Try to extract a better name from the URL or text if 'attachment' is used
                if name == 'attachment':
                    parsed = urlparse(url)
                    path_parts = parsed.path.split('/')
                    if path_parts:
                        last_part = path_parts[-1]
                        if '.' in last_part and len(last_part) < 100:
                            name = last_part

                download_flag = False
                if name.lower().endswith(EXCLUDED_EXTENSIONS) or url.lower().endswith(EXCLUDED_EXTENSIONS):
                    download_flag = False

                attachments.append({
                    'name': name,
                    'url': url,
                    'source': 'requests_static',
                    'download': download_flag
                })

        # Remove duplicates
        unique_attachments = []
        urls_seen = set()
        
        for att in attachments:
            url = att.get('url')
            name = att.get('name')
            # Use URL as unique key if available, otherwise use name
            unique_key = url if url else name
            
            if unique_key not in urls_seen:
                urls_seen.add(unique_key)
                unique_attachments.append(att)

        print(f"  Found {len(unique_attachments)} total unique attachments")
        return unique_attachments

    def _download_attachments(self, attachments: List[Dict]) -> List[Dict]:
        """Fetch attachment content, always using download=false."""
        downloaded = []
        
        # Content types to skip
        EXCLUDED_CONTENT_TYPES = ['application/pdf', 'video/mp4', 'image/png', 'image/jpeg', 'image/jpg', 'image/gif', 'image/webp', 'video/webm', 'video/avi', 'application/zip', 'application/octet-stream', 'video/quicktime']
        
        for attachment in attachments:
            result = {
                'name': attachment['name'],
                'url': attachment.get('url'),
                'content': None,
                'error': None,
                'source': attachment.get('source', 'unknown')
            }

            if attachment.get('url'):
                url = attachment['url']
                
                # Convert download=true to download=false
                if 'download=true' in url:
                    url = url.replace('download=true', 'download=false')
                    print(f"  Converted to view URL (download=false)")
                
                try:
                    print(f"  Fetching: {attachment['name']} (Source: {result['source']})")
                    
                    # First do a HEAD request to check content type without downloading
                    head_response = self.session.head(url, timeout=10, allow_redirects=True)
                    content_type = head_response.headers.get('content-type', '').lower()
                    
                    # Check if content type should be excluded
                    if any(excluded in content_type for excluded in EXCLUDED_CONTENT_TYPES):
                        print(f"  ✗ Skipping excluded content type: {content_type}")
                        result['error'] = f'Excluded content type: {content_type}'
                        downloaded.append(result)
                        continue
                    
                    # Now fetch the actual content
                    response = self.session.get(url, timeout=30)
                    response.raise_for_status()
                    
                    content_type = response.headers.get('content-type', '').lower()
                    
                    # Double-check content type from actual response
                    if any(excluded in content_type for excluded in EXCLUDED_CONTENT_TYPES):
                        print(f"  ✗ Skipping excluded content type: {content_type}")
                        result['error'] = f'Excluded content type: {content_type}'
                        downloaded.append(result)
                        continue
                    
                    if 'text' in content_type or 'javascript' in content_type or 'html' in content_type or \
                       url.endswith('.js') or url.endswith('.html') or url.endswith('.htm'):
                        result['content'] = response.text
                        result['content_type'] = content_type
                        print(f"  ✓ Fetched {len(response.text)} characters")
                    else:
                        try:
                            # Attempt to decode as text, but fall back to error if not
                            result['content'] = response.text
                            result['content_type'] = content_type
                            print(f"  ✓ Fetched {len(response.text)} characters (potential binary)")
                        except:
                            print(f"  ✗ Skipping binary content")
                            result['error'] = 'Binary content not stored'
                    
                except Exception as e:
                    print(f"  ✗ Error: {e}")
                    result['error'] = str(e)
            else:
                result['error'] = 'No URL available'
            
            downloaded.append(result)
        
        return downloaded
    def _extract_related_commits(self, soup: BeautifulSoup, html: str) -> List[Dict]:
        """
        Extract commit hashes with their descriptions from the Chromium issue page.
        Returns a list of dicts with 'hash' and 'description' keys.
        Only returns valid git SHA-1 hashes (7-40 hex characters), no change numbers or URLs.
        """
        commits = {}  # Use dict to store hash -> description mapping
    
        # ============================================================
        # PATTERN 1: Full 40-character SHA-1 commit hashes
        # ============================================================
    
        # 1.1: Any 40-char hash followed by context on next line
        # This catches "Hash:", "Commit:", or just standalone hashes with descriptions
        hash_with_context = re.finditer(
            r'\b([a-f0-9]{40})\b[^\n]*\n([^\n]{10,200})',
            html,
            re.IGNORECASE | re.MULTILINE
        )
        for match in hash_with_context:
            hash_val = match.group(1).strip()
            context = match.group(2).strip()

            # Skip if context looks like another hash or URL
            if re.match(r'^[a-f0-9]{7,}$', context) or context.startswith('http'):
                if hash_val not in commits:
                    commits[hash_val] = ""
                continue

            # Clean up the context - remove common prefixes
            context = re.sub(r'^(Commit message:|Description:|Subject:|Message:)\s*', '', context, flags=re.IGNORECASE)

            if context and len(context) > 10:
                commits[hash_val] = context[:200]  # Limit description length
            elif hash_val not in commits:
                commits[hash_val] = ""
    
        # 1.2: Hashes in googlesource.com URLs with surrounding context
        for match in re.finditer(
            r'https?://(?:chromium|v8|skia|angle|swiftshader|pdfium|webrtc)\.googlesource\.com/[^/]+/[^/]+/\+/([a-f0-9]{40})',
            html,
            re.IGNORECASE
        ):
            hash_val = match.group(1).strip()

            # Try to find description near this URL
            start_pos = max(0, match.start() - 300)
            end_pos = min(len(html), match.end() + 300)
            context = html[start_pos:end_pos]

            # Look for commit message patterns
            desc_patterns = [
                r'(?:Commit message|Subject|Description):\s*([^\n]{10,200})',
                r'>\s*([A-Z][^\n<]{10,200})\s*<',  # Text between tags
            ]

            description = ""
            for pattern in desc_patterns:
                desc_match = re.search(pattern, context, re.IGNORECASE)
                if desc_match:
                    description = desc_match.group(1).strip()
                    break

            if hash_val not in commits:
                commits[hash_val] = description
    
        # 1.3: Hashes in crrev.com URLs
        for match in re.finditer(r'https?://crrev\.com/([a-f0-9]{40})\b', html, re.IGNORECASE):
            hash_val = match.group(1).strip()
            if hash_val not in commits:
                commits[hash_val] = ""
    
        # 1.4: Hashes following fix-related keywords with context
        fix_keywords = [
            'Fixed By', 'Fix commit', 'Fixing commit', 'Fixed in', 'Fix:', 
            'Landed-in', 'Merged-in', 'Landed in', 'Merged in',
            'Fixed by commit', 'Patched in', 'Resolved in', 'Marked as Fixed',
            'Marked as fixed', 'Fixed', 'Resolved'
        ]
        for keyword in fix_keywords:
            pattern = rf'\b{re.escape(keyword)}\b[^:\n]*:\s*([a-f0-9]{{40}})\b([^\n]{{0,200}})'
            for match in re.finditer(pattern, html, re.IGNORECASE):
                hash_val = match.group(1).strip()
                description = match.group(2).strip()
                if description and not description.startswith('http'):
                    commits[hash_val] = description[:200]
                elif hash_val not in commits:
                    commits[hash_val] = "Fix commit"
    
        # 1.5: Standalone 40-char hashes with surrounding context (broader search)
        for match in re.finditer(r'\b([a-f0-9]{40})\b', html):
            hash_val = match.group(1).strip()

            if hash_val not in commits:
                # Get surrounding context (more context for better descriptions)
                start_pos = max(0, match.start() - 200)
                end_pos = min(len(html), match.end() + 200)

                # Look for descriptive text before the hash
                before_text = html[start_pos:match.start()].strip()
                # Look for descriptive text after the hash
                after_text = html[match.end():end_pos].strip()

                description = ""

                # Try to extract a meaningful line before the hash
                if before_text:
                    before_lines = before_text.split('\n')
                    for line in reversed(before_lines):
                        line = line.strip()
                        # Skip lines that are just labels or too short
                        if len(line) > 15 and len(line) < 200 and not line.endswith(':'):
                            # Make sure it's not just another hash
                            if not re.match(r'^[a-f0-9\s]+$', line):
                                description = line
                                break

                # If no good description before, try after
                if not description and after_text:
                    after_lines = after_text.split('\n')
                    for line in after_lines:
                        line = line.strip()
                        if len(line) > 15 and len(line) < 200:
                            # Make sure it's not just another hash or URL
                            if not re.match(r'^[a-f0-9\s]+$', line) and not line.startswith('http'):
                                description = line
                                break

                commits[hash_val] = description
    
        # ============================================================
        # PATTERN 2: Shorter commit hashes (7-39 characters)
        # ============================================================
    
        # 2.1: Bisect/Regression keywords with commit hashes and context
        bisect_keywords = [
            'bisect', 'bisected', 'bisecting',
            'regress', 'regressed', 'regression',
            'introduced in', 'introduced by',
            'culprit', 'bad commit', 'breaking commit',
            'first bad', 'last good'
        ]
        for keyword in bisect_keywords:
            pattern = rf'\b{re.escape(keyword)}\b[^:\n]*[:\s]+([a-f0-9]{{7,39}})\b([^\n]{{0,200}})'
            for match in re.finditer(pattern, html, re.IGNORECASE):
                hash_val = match.group(1).strip()
                # Skip pure decimal numbers (change numbers)
                if re.match(r'^\d+$', hash_val):
                    continue
                description = match.group(2).strip() or "Bisect/regression commit"
                if hash_val not in commits:
                    commits[hash_val] = description[:200]
    
        # 2.2: Shortened hashes in googlesource URLs
        for match in re.finditer(
            r'https?://(?:chromium|v8|skia|angle|swiftshader|pdfium|webrtc)\.googlesource\.com/[^/]+/[^/]+/\+/([a-f0-9]{7,39})',
            html,
            re.IGNORECASE
        ):
            hash_val = match.group(1).strip()
            # Skip pure decimal numbers
            if re.match(r'^\d+$', hash_val):
                continue
            if hash_val not in commits:
                commits[hash_val] = ""
    
        # 2.3: Commit hashes after "commit" keywords (excluding CL numbers)
        commit_keywords = ['commit', 'revision', 'sha', 'sha1']
        for keyword in commit_keywords:
            pattern = rf'\b{re.escape(keyword)}\b[:\s]+([a-f0-9]{{7,39}})\b([^\n]{{0,200}})'
            for match in re.finditer(pattern, html, re.IGNORECASE):
                hash_val = match.group(1).strip()
                # Skip pure decimal numbers
                if re.match(r'^\d+$', hash_val):
                    continue
                description = match.group(2).strip()
                if hash_val not in commits:
                    commits[hash_val] = description[:200] if description else ""
    
        # ============================================================
        # PATTERN 3: Extract from structured bot comments
        # ============================================================
    
        # Look for bot comment patterns that typically include commit info
        bot_comment_pattern = re.compile(
            r'(?:The following revision|Commit:)\s+refers\s+to\s+this\s+bug[^\n]*\n\s*([a-f0-9]{7,40})\s*\n\s*([^\n]{10,200})',
            re.IGNORECASE
        )
        for match in bot_comment_pattern.finditer(html):
            hash_val = match.group(1).strip()
            # Skip if it's all digits (change number, not git hash)
            if re.match(r'^\d+$', hash_val):
                continue
            description = match.group(2).strip()
            if hash_val not in commits:
                commits[hash_val] = description[:200]
    
        # ============================================================
        # Clean and format results
        # ============================================================
    
        result = []
        for hash_val, description in commits.items():
            # Validate it's a proper hex hash (7-40 chars, only hex)
            if not re.match(r'^[a-f0-9]{7,40}$', hash_val):
                continue

            # Skip if it looks like a pure decimal number (change numbers)
            # Git hashes should have at least one letter (a-f)
            if re.match(r'^\d+$', hash_val):
                continue

            # Clean description
            clean_desc = description.strip()
            # Remove URLs from description
            clean_desc = re.sub(r'https?://\S+', '', clean_desc).strip()
            # Remove extra whitespace
            clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()
            # Remove HTML tags if any
            clean_desc = re.sub(r'<[^>]+>', '', clean_desc).strip()

            result.append({
                'hash': hash_val,
                'description': clean_desc if clean_desc else None
            })
    
        # Sort by hash length (longer/full hashes first) then alphabetically
        result.sort(key=lambda x: (-len(x['hash']), x['hash']))
    
        return result


    def _extract_related_commits_selenium(self, url: str) -> List[Dict]:
        """
        Use Selenium to extract commit hashes and descriptions from dynamically loaded content.
        This is more reliable for getting commit descriptions from the rendered page.
        Only returns valid git SHA-1 hashes, no change numbers.
        """
        driver = self._get_webdriver_instance()
        commits = {}

        if not driver:
            return []

        try:
            print("  [Selenium] Loading page to extract commit information...")
            driver.get(url)
            time.sleep(3)  # Let Angular render

            # Find all links containing commit hashes
            commit_links = driver.find_elements(
                By.XPATH,
                "//a[contains(@href, 'googlesource.com') and contains(@href, '/+/')]"
            )

            for link in commit_links:
                try:
                    href = link.get_attribute('href')
                    link_text = link.text.strip()

                    # Extract hash from URL
                    hash_match = re.search(r'/\+/([a-f0-9]{7,40})', href)
                    if not hash_match:
                        continue

                    hash_val = hash_match.group(1)

                    # Skip pure decimal numbers (change numbers)
                    if re.match(r'^\d+$', hash_val):
                        continue

                    # Try to get description from nearby text
                    parent = link.find_element(By.XPATH, './ancestor::*[self::div or self::p or self::span][1]')
                    parent_text = parent.text.strip()

                    # The description might be in the link text itself or nearby
                    description = link_text if link_text and len(link_text) > 10 else ""

                    if not description:
                        # Look for description in parent text
                        lines = parent_text.split('\n')
                        for line in lines:
                            if len(line) > 10 and len(line) < 200 and hash_val not in line:
                                description = line
                                break

                    if hash_val not in commits:
                        commits[hash_val] = description

                except Exception as e:
                    continue

            # Look for bot comments with structured commit info
            try:
                bot_comments = driver.find_elements(
                    By.XPATH,
                    "//b-comment[contains(., 'Hash:') or contains(., 'Commit:')]"
                )

                for comment in bot_comments:
                    comment_text = comment.text

                    # Extract hash
                    hash_match = re.search(r'Hash:\s*([a-f0-9]{40})', comment_text, re.IGNORECASE)
                    if hash_match:
                        hash_val = hash_match.group(1)

                        # Try to find description
                        desc_match = re.search(
                            r'(?:Commit message|Subject|Description):\s*([^\n]{10,200})',
                            comment_text,
                            re.IGNORECASE
                        )

                        description = desc_match.group(1) if desc_match else ""

                        if hash_val not in commits:
                            commits[hash_val] = description

            except Exception:
                pass

        except Exception as e:
            print(f"  [Selenium] Error extracting commits: {e}")
        finally:
            if driver:
                driver.quit()

        # Format results
        result = []
        for hash_val, description in commits.items():
            # Skip pure decimal numbers
            if re.match(r'^\d+$', hash_val):
                continue

            clean_desc = description.strip()
            clean_desc = re.sub(r'\s+', ' ', clean_desc).strip()

            result.append({
                'hash': hash_val,
                'description': clean_desc if clean_desc else None
            })

        result.sort(key=lambda x: (-len(x['hash']), x['hash']))
        return result

    def scrape_issue(self, issue_id: str) -> Dict:
        """
        Scrape a single Chromium issue and extract relevant information.
        """
        url = f"{self.base_url}/issues/{issue_id}"
    
        try:
            response = self.session.get(url, timeout=30)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, 'html.parser')
            attachments = self._extract_attachments(soup, url)

            # Extract title (now with URL for Selenium fallback)
            title = self._extract_title(soup, url)

            # Extract commits using both methods
            print("  Extracting commits from HTML...")
            html_commits = self._extract_related_commits(soup, response.text)

            print("  Extracting commits using Selenium...")
            selenium_commits = self._extract_related_commits_selenium(url)

            # Merge commits, preferring selenium descriptions if available
            all_commits = {}
            for commit in html_commits:
                all_commits[commit['hash']] = commit['description']

            for commit in selenium_commits:
                if commit['hash'] not in all_commits:
                    all_commits[commit['hash']] = commit['description']
                elif commit['description'] and not all_commits[commit['hash']]:
                    # Update with better description from selenium
                    all_commits[commit['hash']] = commit['description']

            # Convert back to list format
            final_commits = [
                {'hash': h, 'description': d}
                for h, d in all_commits.items()
            ]
            final_commits.sort(key=lambda x: (-len(x['hash']), x['hash']))

            issue_data = {
                'issue_id': issue_id,
                'url': url,
                'html': response.text,
                'title': title,
                'description': self._extract_description(soup),
                'attachments': self._download_attachments(attachments),
                'related_commits': final_commits,
            }

            print(f"  Found {len(final_commits)} unique commit hashes")

            return issue_data

        except requests.exceptions.RequestException as e:
            print(f"Error fetching issue {issue_id}: {e}")
            return {'issue_id': issue_id, 'error': str(e)}

    def scrape_multiple_issues(self, issue_ids: List[str], delay: float = 1.0) -> None: 
        """Scrape multiple issues with rate limiting."""
        #results = []
        
        for idx, issue_id in enumerate(issue_ids):
            print(f"Scraping issue {idx + 1}/{len(issue_ids)}: {issue_id}")
            issue_data = self.scrape_issue(issue_id)
            #results.append(issue_data)

            filename = f"{OUTPUT_DIR}/chromium_issue_{issue_id}.json"
            self.save_to_json(issue_data, filename)    
            #print(f"Saved issue {issue_id} to {filename}")

            if idx < len(issue_ids) - 1:
                time.sleep(delay)
        
        #return results
    
    def save_to_json(self, data: List[Dict], filename: str):
        """Save scraped data to JSON file."""
        with open(filename, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"Data saved to {filename}")


OUTPUT_DIR = "out" 

# Example usage
if __name__ == "__main__":
    scraper = ChromiumIssueScraper()

    try:
        os.makedirs(OUTPUT_DIR)
    except FileExistsError:
        pass

    print("=" * 60)
    print("Scraping bug IDs from tracker.ret2happy.com...")
    print("=" * 60)
    
    issue_ids = scraper.scrape_tracker_bugs(min_reward=1, page_size=1671, max_pages=1)
    print(f"\nFound {len(issue_ids)} total issues with reward > 0")
    
    if issue_ids:
        scraper.scrape_multiple_issues(issue_ids, delay=2.0)
        #scraper.save_to_json(all_issues, "chromium_issues_from_tracker.json")
        print(f"\nScraped {len(all_issues)} issues successfully")
    
    # Option 2: Single issue scraping
    #print("\n" + "=" * 60)
    #print("Example: Scraping single issue 430344952 (Uses new minimal structure)")
    #print("=" * 60)
    
    #issue_id = "40067948"
    #issue_data = scraper.scrape_issue(issue_id)
    #scraper.save_to_json([issue_data], f"chromium_issue_{issue_id}.json")