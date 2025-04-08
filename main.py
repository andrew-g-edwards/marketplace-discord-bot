import discord
import re
import os
import logging
from discord.ext import commands
import asyncio
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, StaleElementReferenceException
from dotenv import load_dotenv
import datetime
import random

# Set up logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler("bot.log"),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger("marketplace-bot")

# Clear any cached environment variables
if 'DISCORD_TOKEN' in os.environ:
    del os.environ['DISCORD_TOKEN']

# Load environment variables
script_dir = os.path.dirname(os.path.abspath(__file__))
env_path = os.path.join(script_dir, '.env')
logger.info(f"Loading environment from: {env_path}")
load_dotenv(dotenv_path=env_path, override=True)

TOKEN = os.getenv('DISCORD_TOKEN')
CHANNEL_ID = int(os.getenv('CHANNEL_ID'))

# Set up intents
intents = discord.Intents.default()
intents.message_content = True

# Initialize the bot
bot = commands.Bot(command_prefix='!', intents=intents)

# Regular expressions for different Facebook URL formats
FB_PATTERNS = [
    r'https?://(?:www\.)?facebook\.com/marketplace/item/\d+',
    r'https?://(?:www\.)?facebook\.com/marketplace/item\.php\?id=\d+',
    r'https?://(?:www\.)?fb\.com/marketplace/item/\d+',
    r'https?://(?:www\.)?facebook\.com/share/[a-zA-Z0-9]+',
    r'https?://(?:www\.)?facebook\.com/share/[a-zA-Z0-9]+/?(?:\?.*)?',
    r'https?://(?:www\.)?facebook\.com/[^/]+/posts/[a-zA-Z0-9]+',
    r'https?://(?:www\.)?fb\.watch/[a-zA-Z0-9_-]+/?',
    r'https?://(?:www\.)?fb\.me/[a-zA-Z0-9_-]+',
    r'https?://m\.facebook\.com/[^\s]+'
]

async def setup_webdriver():
    """Set up and return a configured Chrome webdriver"""
    chrome_options = Options()
    chrome_options.add_argument("--headless")
    chrome_options.add_argument("--no-sandbox")
    chrome_options.add_argument("--disable-dev-shm-usage")
    chrome_options.add_argument("--disable-gpu")
    chrome_options.add_argument("--window-size=1920,1080")
    
    # Rotate user agents to avoid detection
    user_agents = [
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36",
        "Mozilla/5.0 (iPhone; CPU iPhone OS 16_5 like Mac OS X) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/16.0 Mobile/15E148 Safari/604.1",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36"
    ]
    chrome_options.add_argument(f"--user-agent={random.choice(user_agents)}")
    
    try:
        driver = webdriver.Chrome(options=chrome_options)
        return driver
    except Exception as e:
        logger.error(f"Failed to initialize webdriver: {e}")
        return None

async def find_main_listing_container(driver):
    """
    Identify the main listing container to avoid similar listings sections
    Returns the main container element if found, None otherwise
    """
    # Try to find main marketplace listing container
    container_selectors = [
        # Primary marketplace container selectors
        "[data-testid='marketplace_pdp_container']",
        "[data-pagelet='MarketplacePermalinkRoot']",
        "div[role='main']",
        # More specific container patterns
        "div.x1qjc9v5.x78zum5.x1q0g3np", # Common marketplace container class
        ".xrvj5dj.x1gslohp",  # Another common container
        # Mobile selectors
        ".x78zum5.xdt5ytf"
    ]
    
    for selector in container_selectors:
        try:
            containers = driver.find_elements(By.CSS_SELECTOR, selector)
            if containers:
                # Find the container most likely to be the main listing
                for container in containers:
                    # Check if this container has both a title and price
                    has_title = len(container.find_elements(By.CSS_SELECTOR, "h1, [role='heading'], span.x193iq5w")) > 0
                    has_price = len(container.find_elements(By.XPATH, ".//*[contains(text(), '$')]")) > 0
                    
                    if has_title and has_price:
                        logger.info(f"Found main listing container with selector: {selector}")
                        return container
        except Exception as e:
            logger.warning(f"Error finding container with selector {selector}: {e}")
            continue
    
    # Fallback to body if no specific container found
    return driver.find_element(By.TAG_NAME, "body")

async def extract_title_from_container(container):
    """Extract title from a specific container"""
    title = "Title not found"
    
    title_selectors = [
        "h1", 
        "[role='heading']", 
        "[data-testid='marketplace-listing-item-title']",
        "[data-testid='marketplace_pdp_title']", 
        ".x1heor9g",
        "span.x193iq5w", # Often contains the title
        ".xt0psk2"
    ]
    
    for selector in title_selectors:
        try:
            elements = container.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                text = element.text.strip()
                if text and 5 < len(text) < 100 and not text.startswith("Browse") and "Create new listing" not in text:
                    # Check if the text contains multiple prices (suggesting it's a similar items section)
                    if text.count("$") <= 1 and "similar" not in text.lower():
                        return text
        except Exception:
            continue
    
    # Fallback
    try:
        elements = container.find_elements(By.CSS_SELECTOR, "div[dir='auto']")
        for element in elements:
            text = element.text.strip()
            if text and 10 < len(text) < 100 and text.upper() != text and text.count("$") <= 1:
                return text
    except Exception:
        pass
        
    return title

async def extract_price_from_container(container):
    """Extract price from a specific container"""
    price = "Price not found"
    
    # Look for elements that are likely to be the main price
    price_selectors = [
        # Primary price selectors
        "[data-testid='marketplace_pdp_price']",
        "span.x193iq5w", # Often contains price
        ".x1j85h84", # Known price container
        
        # Fallback selectors
        "h1 + span", # Price is often in span right after title
        ".x1fcty0u span"
    ]
    
    for selector in price_selectors:
        try:
            elements = container.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                text = element.text.strip()
                if '$' in text and any(c.isdigit() for c in text):
                    # Make sure this price is not part of a list
                    parent_text = ""
                    try:
                        parent = element.find_element(By.XPATH, "./..")
                        parent_text = parent.text
                    except:
                        pass
                    
                    # If parent contains multiple prices, skip this one
                    if parent_text.count("$") <= 1:
                        # Clean up the price
                        if len(text) > 15:
                            price_match = re.search(r'\$\s*[\d,]+(?:\.\d{2})?', text)
                            if price_match:
                                return price_match.group(0).strip()
                        return text
        except Exception:
            continue
    
    # If we haven't found a price yet, try XPath to find the first $ sign
    try:
        price_elements = container.find_elements(By.XPATH, ".//*[contains(text(), '$')]")
        for element in price_elements:
            text = element.text.strip()
            # Make sure this is a simple price and not a list
            if '$' in text and any(c.isdigit() for c in text) and len(text) < 30 and text.count("$") == 1:
                price_match = re.search(r'\$\s*[\d,]+(?:\.\d{2})?', text)
                if price_match:
                    return price_match.group(0).strip()
                else:
                    return text
    except Exception:
        pass
    
    return price

async def extract_location_from_container(container):
    """Extract location from a specific container"""
    location = "Location not found"
    
    # Specific location selectors
    location_selectors = [
        "[data-testid='marketplace_pdp_location']",
        "div.x1xmf6yo", # Known location container
        ".x1e56ztr",
        ".x1lliihq"
    ]
    
    for selector in location_selectors:
        try:
            elements = container.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                text = element.text.strip()
                if text and len(text) < 100 and ("Location" in text or "," in text) and "$" not in text:
                    if "Location" in text and ":" in text:
                        text = text.split(":", 1)[1].strip()
                    
                    # Clean up location text
                    for phrase in ["Browse all", "Categories", "Nearby", "Within"]:
                        if phrase in text:
                            text = text.split(phrase, 1)[0].strip()
                    
                    return text
        except Exception:
            continue
    
    # Pattern-based detection
    try:
        elements = container.find_elements(By.CSS_SELECTOR, "span, div")
        
        patterns = [
            re.compile(r'Location[\s:]+([\w\s,]+)'),
            re.compile(r'in ([\w\s]+, [A-Z]{2})'),
            re.compile(r'([\w\s]+, [A-Z]{2})')  # City, State format
        ]
        
        for element in elements:
            try:
                text = element.text.strip()
                # Skip elements that are likely to be similar listings
                if text and len(text) < 100 and "$" not in text:
                    for pattern in patterns:
                        match = pattern.search(text)
                        if match:
                            return match.group(1).strip()
                    
                    # Look for typical "City, State" format
                    if "," in text and len(text) < 50 and "$" not in text:
                        parts = text.split(",")
                        if len(parts) == 2 and all(p.strip() for p in parts):
                            return text
            except Exception:
                continue
    except Exception:
        pass
    
    return location

async def extract_description_from_container(container):
    """Extract description from a specific container"""
    description = "Description not found"
    
    # Description selectors
    description_selectors = [
        "[data-testid='marketplace_listing_item_description']",
        "[data-testid='marketplace_pdp_description']",
        ".xz9dl7a",  # Known selector for descriptions
        "[aria-label*='description']",
        ".x1gslohp",
        ".xw7yly9"
    ]
    
    description_candidates = []
    for selector in description_selectors:
        try:
            elements = container.find_elements(By.CSS_SELECTOR, selector)
            for element in elements:
                text = element.text.strip()
                # Filter out UI elements and similar items
                if (text and len(text) > 20 and 
                    "Browse all" not in text and 
                    "Categories" not in text and
                    "Nearby Cities" not in text):
                    
                    # Skip if text contains multiple prices (likely a list of similar items)
                    if text.count("$") <= 1:
                        description_candidates.append(text)
        except Exception:
            continue
    
    if description_candidates:
        # Filter out UI elements
        filtered_candidates = [text for text in description_candidates 
                             if not any(x in text.lower() for x in ["browse all", "create new", "categories", "miles", "nearby"])]
        
        if filtered_candidates:
            return max(filtered_candidates, key=len)
        else:
            return max(description_candidates, key=len)
    
    # Fallback to any longer text in the container
    try:
        elements = container.find_elements(By.CSS_SELECTOR, "div[dir='auto']")
        
        content_candidates = []
        for element in elements:
            text = element.text.strip()
            # Look for substantial text that's not a similar item list
            if (text and len(text) > 40 and 
                "Browse all" not in text and 
                "Categories" not in text and
                text.count("$") <= 1):  # Not a list of similar items
                content_candidates.append(text)
        
        if content_candidates:
            return max(content_candidates, key=len)
    except Exception:
        pass
    
    return description

async def extract_images_from_container(container):
    """Extract image URLs from the main listing container"""
    image_urls = []
    
    # Common image selectors for Facebook Marketplace
    image_selectors = [
        # Main product image selectors
        "[data-testid='marketplace_pdp_images'] img",
        "[data-testid='marketplace_pdp_carousel'] img",
        "[data-testid='marketplace-pdp-image'] img",
        ".x5yr21d img",   # Common image container class
        ".x1rg5ohu img",  # Another common image container
        ".x6ikm8r img",   # Mobile image container
        "img[src*='scontent']",  # Facebook CDN images
        "img[alt*='product']",
        "img[data-visualcompletion='media-vc-image']"
    ]
    
    # Try each selector to find images
    for selector in image_selectors:
        try:
            images = container.find_elements(By.CSS_SELECTOR, selector)
            for img in images:
                try:
                    # Get the source URL of the image
                    src = img.get_attribute('src')
                    
                    # Validate that it's a proper image URL from Facebook's CDN
                    if src and 'scontent' in src and len(src) > 20 and src not in image_urls:
                        # Skip tiny thumbnails that might be icons
                        width = img.get_attribute('width')
                        if width and int(width) < 50:
                            continue
                            
                        logger.info(f"Found image: {src[:50]}...")
                        image_urls.append(src)
                        
                        # If we're specifically in a carousel or main image section, prioritize this
                        carousel_parent = img.find_elements(By.XPATH, "./ancestor::*[contains(@data-testid, 'carousel') or contains(@data-testid, 'pdp_images')]")
                        if carousel_parent:
                            logger.info("Found image in main carousel")
                            # If it's in a carousel, this is likely the main product image, so return it immediately
                            return [src]
                except Exception as e:
                    logger.warning(f"Error processing image element: {e}")
                    continue
        except Exception as e:
            logger.warning(f"Error with image selector {selector}: {e}")
            continue
    
    # If we found images, return them (largest first if we can determine)
    if image_urls:
        # We'll just return the first image for now
        # In a more advanced version, we could try to sort by size
        return [image_urls[0]]
    
    return []

async def scroll_and_wait(driver, scroll_pause_time=2):
    """Scroll down the page to load more content"""
    try:
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight);")
        await asyncio.sleep(scroll_pause_time)
        driver.execute_script("window.scrollTo(0, document.body.scrollHeight * 0.8);")
        await asyncio.sleep(1)
    except Exception:
        pass

async def scrape_facebook_marketplace(url):
    """
    Scrape Facebook content details using Selenium with improved isolation
    """
    logger.info(f"Attempting to scrape: {url}")
    driver = await setup_webdriver()
    
    if not driver:
        return {
            "title": "Error setting up web browser",
            "price": "Unknown",
            "location": "Unknown",
            "description": "Could not initialize the browser to fetch content details.",
            "image_url": None,
            "success": False
        }
    
    is_marketplace = "marketplace" in url
    
    try:
        # Navigate to the URL
        driver.get(url)
        
        # Wait for the page to load
        try:
            WebDriverWait(driver, 20).until(
                EC.presence_of_element_located((By.CSS_SELECTOR, "h1, [role='heading']"))
            )
        except TimeoutException:
            logger.warning("Timeout waiting for page to load, attempting to parse anyway")
        
        # Give Facebook more time to load content - INCREASED WAIT TIME FOR IMAGES
        await asyncio.sleep(10)  # Increased to 10 seconds to ensure images load
        
        # Scroll to load lazy-loaded content and images
        await scroll_and_wait(driver, 3)  # Longer pause for images
        
        # Find the main container element to isolate content
        main_container = await find_main_listing_container(driver)
        
        # Extract information only from the main container
        title = await extract_title_from_container(main_container)
        
        # For marketplace posts, get price and location
        price = "Price not found" if is_marketplace else "N/A"
        location = "Location not found" if is_marketplace else "N/A"
        
        if is_marketplace:
            price = await extract_price_from_container(main_container)
            location = await extract_location_from_container(main_container)
        
        # Extract description and images
        description = await extract_description_from_container(main_container)
        image_urls = await extract_images_from_container(main_container)
            
        # ------- RETRY LOGIC -------
        if is_marketplace and (price == "Price not found" or location == "Location not found" or not image_urls):
            logger.info("Important information missing, attempting retry...")
            await asyncio.sleep(5)
            await scroll_and_wait(driver, 3)
            
            # Refresh the main container reference
            main_container = await find_main_listing_container(driver)
            
            if title == "Title not found":
                title = await extract_title_from_container(main_container)
            if price == "Price not found":
                price = await extract_price_from_container(main_container)
            if location == "Location not found":
                location = await extract_location_from_container(main_container)
            if not image_urls:
                image_urls = await extract_images_from_container(main_container)
        
        # Post-processing validation
        # If we have multiple prices in the title, something went wrong
        if title.count("$") > 1:
            title = "Facebook Marketplace Listing"
        
        image_url = image_urls[0] if image_urls else None
        
        return {
            "title": title,
            "price": price,
            "location": location,
            "description": description,
            "image_url": image_url,
            "success": True
        }
    
    except Exception as e:
        logger.error(f"Error scraping {url}: {str(e)}")
        return {
            "title": "Error fetching listing",
            "price": "Unknown",
            "location": "Unknown",
            "description": f"Could not retrieve listing details. Error: {str(e)}",
            "image_url": None,
            "success": False
        }
    
    finally:
        driver.quit()

@bot.event
async def on_ready():
    logger.info(f'{bot.user} has connected to Discord!')
    logger.info(f'Monitoring channel with ID: {CHANNEL_ID}')
    
    # Set bot status
    await bot.change_presence(activity=discord.Activity(
        type=discord.ActivityType.watching, 
        name="for Marketplace links"
    ))

@bot.event
async def on_message(message):
    # Ignore messages from the bot itself
    if message.author == bot.user:
        return
    
    # Only process messages in the specified channel
    if message.channel.id != CHANNEL_ID:
        return
    
    # Check if the message contains a Facebook link
    fb_url = None
    for pattern in FB_PATTERNS:
        match = re.search(pattern, message.content)
        if match:
            fb_url = match.group(0)
            break
    
    if fb_url:
        # Let users know the bot is working on it
        processing_msg = await message.channel.send("🔍 Fetching details from Facebook... (this may take a few moments)")
        
        # Scrape the Facebook link
        listing_details = await scrape_facebook_marketplace(fb_url)
        
        # Create and send an embed with the content details
        if listing_details["success"]:
            # Clean up the extracted data
            title = listing_details["title"]
            price = listing_details["price"]
            location = listing_details["location"]
            description = listing_details["description"]
            image_url = listing_details["image_url"]
            
            # Better title filtering
            if title == "Title not found" or "Browse all" in title:
                if "marketplace" in fb_url:
                    title = "Facebook Marketplace Listing"
                else:
                    title = "Facebook Post"
            
            # Better price formatting
            if price == "Price not found":
                price = "Not listed"
                
            # Better location formatting 
            if location == "Location not found":
                location = "Not specified"
            elif "Location" in location:
                location = location.replace("Location", "").replace(":", "").strip()
            
            # Final description cleaning
            if description == "Description not found":
                description = "No description available"
            else:
                # Try to remove common UI text from description
                for phrase in ["Browse all", "Categories", "Nearby Cities", "Create new listing", "Your account"]:
                    if phrase in description:
                        description_parts = description.split(phrase, 1)
                        if len(description_parts) > 1:
                            if len(description_parts[0]) > len(description_parts[1]):
                                description = description_parts[0].strip()
                            else:
                                description = description_parts[1].strip()
                
                # Check if description contains multiple listings
                if description.count("$") > 2:  # Multiple price indicators
                    description = "Description not available"
            
            # Final validation to ensure we're not displaying similar listings
            if price.count("$") > 1:
                price = "Not listed"
            
            # Determine whether this is a marketplace listing or regular post
            is_marketplace = "marketplace" in fb_url or (price != "Not listed" and price != "N/A")
            
            embed = discord.Embed(
                title=title,
                url=fb_url,
                color=0x1877F2,  # Facebook blue
                timestamp=datetime.datetime.now()
            )
            
            # Set the image if we found one
            if image_url:
                embed.set_image(url=image_url)
            
            # Handle potentially long descriptions
            if len(description) > 1024:
                description = description[:1021] + "..."
            
            # Add fields based on content type
            if is_marketplace:
                embed.add_field(name="💰 Price", value=price, inline=True)
                embed.add_field(name="📍 Location", value=location, inline=True)
                
                # Only add description if it's meaningful
                if description and description not in ["No description available", "Description not found"]:
                    embed.add_field(name="📝 Description", value=description, inline=False)
                
                embed.set_footer(text=f"Requested by {message.author.display_name} | Marketplace Listing")
            else:
                # For regular Facebook posts
                if len(description) > 50 and not any(x in description.lower() for x in ["browse all", "create new", "categories"]):
                    embed.description = description
                embed.set_footer(text=f"Requested by {message.author.display_name} | Facebook Post")
            
            await processing_msg.delete()
            await message.channel.send(embed=embed)
        else:
            # Send error message if scraping failed
            embed = discord.Embed(
                title="Error Fetching Details",
                description=f"I couldn't retrieve the details from this Facebook link.",
                color=0xFF0000,
                timestamp=datetime.datetime.now()
            )
            embed.add_field(name="Link", value=fb_url, inline=False)
            
            await processing_msg.delete()
            await message.channel.send(embed=embed)

# Run the bot
if __name__ == "__main__":
    logger.info("Starting bot...")
    try:
        bot.run(TOKEN)
    except Exception as e:
        logger.critical(f"Failed to start bot: {e}")