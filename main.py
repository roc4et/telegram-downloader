import os
import re
import sys
import asyncio
import json
from datetime import datetime
from telethon import TelegramClient, errors
from telethon.errors import SessionPasswordNeededError
from telethon.tl.types import MessageMediaDocument, PeerChannel
from terminut import log, printf as print, inputf as input

CONFIG_PATH = './data/config.json'

def load_config(config_path):
    if not os.path.exists(config_path):
        log.fatal(f"Configuration file not found at {config_path}.")
        sys.exit(1)
    with open(config_path, 'r') as f:
        try:
            config = json.load(f)
            return config
        except json.JSONDecodeError as e:
            log.fatal(f"Error parsing the configuration file: {e}")
            sys.exit(1)

config = load_config(CONFIG_PATH)
API_ID = config.get('api_id')
API_HASH = config.get('api_hash')
MAX_RETRIES = config.get('max_retries')
threads = config.get('threads')

def get_timestamp():
    return datetime.now().strftime('%Y-%m-%d_%H-%M-%S')

def create_download_directory():
    timestamp = get_timestamp()
    download_dir = os.path.join('results', timestamp)
    os.makedirs(download_dir, exist_ok=True)
    return download_dir

def extract_link_details(link):
    private_pattern = r"https?://t\.me/c/(\d+)/(\d+)"
    private_match = re.match(private_pattern, link)
    if private_match:
        channel_id = int(private_match.group(1))
        message_id = int(private_match.group(2))
        return ('private', channel_id, message_id)
    public_pattern = r"https?://t\.me/([^/]+)/(\d+)"
    public_match = re.match(public_pattern, link)
    if public_match:
        username = public_match.group(1)
        message_id = int(public_match.group(2))
        return ('public', username, message_id)
    raise ValueError("Invalid Telegram message link format.")

def is_message_link(link):
    return '/c/' in link or ('/c/' not in link and re.match(r"https?://t\.me/[^/]+/\d+", link) is not None)

async def get_private_channel(client, channel_id):
    try:
        async for dialog in client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, PeerChannel) or hasattr(entity, 'id'):
                if hasattr(entity, 'id') and entity.id == channel_id:
                    return entity
        raise ValueError(f"No channel found with ID {channel_id}. Ensure you're a member of the channel.")
    except Exception as e:
        raise ValueError(f"Error retrieving channel: {e}")

async def download_media(message, folder_name, semaphore, max_retries=MAX_RETRIES):
    attempt = 0
    while attempt <= max_retries:
        try:
            async with semaphore:
                file_path = await message.download_media(file=folder_name)
                if file_path:
                    log.success(f"Downloaded: {file_path}")
                    return
                else:
                    raise Exception("Failed to download media.")
        except Exception as e:
            attempt += 1
            if attempt > max_retries:
                log.error(f"Failed to download media after {max_retries} attempts: {e}")
                return
            else:
                log.error(f"Error downloading media: {e}. Retrying ({attempt}/{max_retries})...")
                await asyncio.sleep(2)

async def download_all_media(client, group_link, download_dir):
    try:
        group = await client.get_entity(group_link)
    except errors.InviteHashInvalidError:
        log.error(f"Error: The group link '{group_link}' is invalid or has expired.")
        return
    except Exception as e:
        log.error(f"An error occurred while fetching the group: {e}")
        return
    group_name = group.title if hasattr(group, 'title') else str(group.id)
    group_folder = os.path.join(download_dir, group_name)
    os.makedirs(group_folder, exist_ok=True)
    log.info(f"Downloading media to: {group_folder}")
    semaphore = asyncio.Semaphore(threads)
    log.info("Collecting media messages...")
    tasks = []
    try:
        async for message in client.iter_messages(group, limit=None):
            if message.media:
                tasks.append(download_media(message, group_folder, semaphore))
    except Exception as e:
        log.error(f"An error occurred while iterating messages: {e}")
        return
    if tasks:
        log.info(f"Starting download of {len(tasks)} media files...")
        await asyncio.gather(*tasks)
        log.success("All media downloaded successfully.")
    else:
        log.info("No media found in the specified group.")

async def download_attachment(client, link, download_dir):
    try:
        link_details = extract_link_details(link)
    except ValueError as ve:
        log.error(ve)
        return
    link_type = link_details[0]
    if link_type == 'public':
        _, username, message_id = link_details
        channel = username
    elif link_type == 'private':
        _, channel_id, message_id = link_details
        try:
            channel_entity = await get_private_channel(client, channel_id)
            channel = channel_entity
        except ValueError as ve:
            log.error(ve)
            return
    else:
        log.error("Unsupported link type.")
        return
    try:
        if link_type == 'public':
            message = await client.get_messages(channel, ids=message_id)
        elif link_type == 'private':
            message = await client.get_messages(channel, ids=message_id)
        if not message:
            log.error("Message not found.")
            return
        if not message.media:
            log.info("No attachment found in the message.")
            return
        if isinstance(message.media, MessageMediaDocument) or message.photo or message.video:
            attachment_folder = os.path.join(download_dir, "attachments")
            os.makedirs(attachment_folder, exist_ok=True)
            log.info(f"Downloading attachment to: {attachment_folder}")
            semaphore = asyncio.Semaphore(threads)
            await download_media(message, attachment_folder, semaphore)
        else:
            log.info("The attachment is not a recognized media type (document/photo/video).")
    except Exception as e:
        log.error(f"An error occurred: {e}")

async def authenticate(client):
    await client.start()
    log.success("Logged in successfully!")
    if not await client.is_user_authorized():
        phone = input("Enter your Telegram phone number (in international format, e.g., +123456789): ").strip()
        try:
            await client.send_code_request(phone)
            code = input("Enter the code you received: ").strip()
            await client.sign_in(phone, code)
        except SessionPasswordNeededError:
            password = input("Two-step verification enabled. Please enter your password: ").strip()
            await client.sign_in(password=password)
        except Exception as e:
            log.fatal(f"Failed to sign in: {e}")
            sys.exit(1)

async def main():
    download_dir = create_download_directory()
    log.info(f"All downloads will be saved to: {download_dir}")
    client = TelegramClient('session', API_ID, API_HASH)
    try:
        await authenticate(client)
        link = input("Enter your Telegram Link: ").strip()
        if is_message_link(link):
            await download_attachment(client, link, download_dir)
        else:
            await download_all_media(client, link, download_dir)
    finally:
        await client.disconnect()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        log.fatal("\nProgram interrupted by user. Exiting...")
