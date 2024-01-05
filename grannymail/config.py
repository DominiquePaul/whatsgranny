import os
from dotenv import load_dotenv, find_dotenv

load_dotenv(find_dotenv())

# Server setup
WEBHOOK_URL = os.environ['WEBHOOK_URL']

# Telegram bot
BOT_TOKEN = os.environ['BOT_TOKEN']
BOT_USERNAME = os.environ['BOT_USERNAME']

