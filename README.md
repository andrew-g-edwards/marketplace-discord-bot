# Facebook Marketplace Discord Bot

A Discord bot that extracts and displays information from Facebook Marketplace links posted in a specific channel.

## Features
- Detects Facebook Marketplace links in Discord messages
- Extracts listing title, price, location, description, and images
- Displays information in a formatted Discord embed

## Setup Instructions

1. Clone this repository
2. Install dependencies: `pip install -r requirements.txt`
3. Create a `.env` file with your bot token and channel ID:
DISCORD_TOKEN=your_discord_bot_token
CHANNEL_ID=your_channel_id
Copy
4. Run the bot: `python main.py`

## Requirements
- Python 3.8+
- Chrome browser
- ChromeDriver
