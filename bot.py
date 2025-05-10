import os
import asyncio
from pyrogram import Client, filters, types
from pyrogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask
import sqlite3
from datetime import datetime, timedelta
import re
import logging

# Flask app for keep-alive
flask_app = Flask(__name__)

@flask_app.route('/ping')
def ping():
    return "Bot is alive!", 200

# Pyrogram bot setup
API_ID = os.getenv("API_ID")
API_HASH = os.getenv("API_HASH")
BOT_TOKEN = os.getenv("BOT_TOKEN")

app = Client("file_request_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Database setup
def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS requests 
                 (user_id INTEGER, query TEXT, file_id TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, last_request TEXT, request_count INTEGER)''')
    c.execute('''CREATE TABLE IF NOT EXISTS storage_channels 
                 (channel_id INTEGER PRIMARY KEY, added_by INTEGER, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS channel_logs 
                 (action TEXT, channel_id INTEGER, admin_id INTEGER, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS admins 
                 (user_id INTEGER PRIMARY KEY, added_at TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS activity_logs 
                 (log_id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER, action TEXT, details TEXT, timestamp TEXT)''')
    conn.commit()
    conn.close()

# Log activity to activity_logs table
def log_activity(user_id, action, details):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO activity_logs (user_id, action, details, timestamp) VALUES (?, ?, ?, ?)",
             (user_id, action, details, datetime.now().isoformat()))
    conn.commit()
    conn.close()

# Check if admin is set
def get_admin_id():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT user_id FROM admins LIMIT 1")
    admin = c.fetchone()
    conn.close()
    return admin[0] if admin else None

# Set admin
def set_admin(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO admins (user_id, added_at) VALUES (?, ?)",
             (user_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    log_activity(user_id, "admin_setup", "User set as admin")

# Rate limiting
async def check_rate_limit(user_id):
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT last_request, request_count FROM users WHERE user_id = ?", (user_id,))
    user = c.fetchone()
    
    now = datetime.now()
    if not user:
        c.execute("INSERT INTO users (user_id, last_request, request_count) VALUES (?, ?, ?)", 
                 (user_id, now.isoformat(), 1))
        conn.commit()
        conn.close()
        return True
    
    last_request = datetime.fromisoformat(user[0])
    request_count = user[1]
    
    if now - last_request > timedelta(hours=1):
        c.execute("UPDATE users SET request_count = 1, last_request = ? WHERE user_id = ?", 
                 (now.isoformat(), user_id))
        conn.commit()
        conn.close()
        return True
    
    if request_count >= 5:
        conn.close()
        log_activity(user_id, "rate_limit_exceeded", "User exceeded hourly request limit")
        return False
    
    c.execute("UPDATE users SET request_count = request_count + 1, last_request = ? WHERE user_id = ?", 
             (now.isoformat(), user_id))
    conn.commit()
    conn.close()
    return True

# Get list of storage channels
def get_storage_channels():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("SELECT channel_id FROM storage_channels")
    channels = [row[0] for row in c.fetchall()]
    conn.close()
    return channels

# Validate if bot is admin of a channel
async def is_bot_channel_admin(channel_id):
    try:
        chat = await app.get_chat(channel_id)
        if chat.type not in ["channel", "supergroup"]:
            return False
        admins = await app.get_chat_members(channel_id, filter="administrators")
        bot_id = (await app.get_me()).id
        return any(member.user.id == bot_id for member in admins)
    except Exception as e:
        logging.error(f"Error validating channel {channel_id}: {e}")
        log_activity(0, "error", f"Failed to validate channel {channel_id}: {str(e)}")
        return False

# Admin menu with storage channel management
def get_admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📂 Add File", callback_data="admin_add_file"),
         InlineKeyboardButton("🗃 View Logs", callback_data="admin_logs")],
        [InlineKeyboardButton("🚫 User Management", callback_data="admin_users")],
        [InlineKeyboardButton("📚 Manage Storage Channels", callback_data="admin_manage_channels")],
        [InlineKeyboardButton("📜 View Activity Logs", callback_data="admin_activity_logs")]
    ])

# Storage channels menu
def get_storage_channels_menu():
    channels = get_storage_channels()
    buttons = []
    for channel_id in channels:
        buttons.append([InlineKeyboardButton(
            f"Channel {channel_id}",
            callback_data=f"view_channel_{channel_id}"
        )])
    buttons.append([InlineKeyboardButton("➕ Add Storage Channel", callback_data="add_storage_channel")])
    buttons.append([InlineKeyboardButton("⬅️ Back to Admin Menu", callback_data="back_to_admin")])
    return InlineKeyboardMarkup(buttons)

# Channel details menu
def get_channel_details_menu(channel_id):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🗑️ Remove Channel", callback_data=f"remove_channel_{channel_id}")],
        [InlineKeyboardButton("⬅️ Back to Channels", callback_data="admin_manage_channels")]
    ])

# Start command with admin setup
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    admin_id = get_admin_id()
    
    # Admin setup if not configured
    if admin_id is None:
        set_admin(user_id)
        await message.reply(
            "🎉 You are now the admin of this bot!\n"
            "Let's set up the bot. Please forward a message from a channel where I am an admin to add it as a storage channel."
        )
        return
    
    # Normal flow
    if user_id == admin_id:
        await message.reply("Welcome, Admin!", reply_markup=get_admin_menu())
    else:
        await message.reply("Welcome! Send a keyword to search for files.")
        log_activity(user_id, "user_start", "User started the bot")

# Admin command
@app.on_message(filters.command("admin") & filters.private)
async def admin_panel(client, message):
    admin_id = get_admin_id()
    if message.from_user.id != admin_id:
        await message.reply("🚫 Unauthorized.")
        log_activity(message.from_user.id, "unauthorized_access", "Attempted to access admin panel")
        return
    await message.reply("Admin Panel", reply_markup=get_admin_menu())
    log_activity(admin_id, "admin_panel_access", "Admin accessed the panel")

# Search query across multiple storage channels
@app.on_message(filters.text & filters.private & ~filters.command(["start", "admin"]))
async def search_files(client, message):
    user_id = message.from_user.id
    query = message.text.strip()
    
    if len(query) < 3:
        await message.reply("⚠️ Query must be at least 3 characters long.")
        log_activity(user_id, "invalid_query", "Query too short")
        return
    
    # Validate query (basic banned words check)
    banned_words = ["spam", "hack", "illegal"]
    if any(word in query.lower() for word in banned_words):
        await message.reply("🚫 Invalid query.")
        log_activity(user_id, "invalid_query", "Query contains banned words")
        return
    
    await message.reply("🔍 Searching for files...")
    log_activity(user_id, "search_files", f"User searched for: {query}")
    
    # Search across all storage channels
    storage_channels = get_storage_channels()
    if not storage_channels:
        await message.reply("🚫 No storage channels configured. Please contact the admin.")
        log_activity(user_id, "no_channels", "No storage channels available")
        return
    
    buttons = []
    for channel_id in storage_channels:
        async for msg in app.search_messages(channel_id, query=query, limit=10):
            file_name = msg.document.file_name if msg.document else "Unknown"
            file_size = f"{msg.document.file_size / 1024 / 1024:.2f} MB" if msg.document else "N/A"
            buttons.append([InlineKeyboardButton(
                f"{file_name} ({file_size})",
                callback_data=f"request_{channel_id}_{msg.id}"
            )])
    
    if buttons:
        await message.reply(
            "📄 Found files:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await message.reply("🚫 No files found. Try a different keyword.")
        log_activity(user_id, "no_results", f"No files found for query: {query}")

# Handle file request using forward_messages
@app.on_callback_query(filters.regex(r"request_(-?\d+)_(\d+)"))
async def handle_request(client, callback):
    user_id = callback.from_user.id
    channel_id = int(callback.data.split("_")[1])
    message_id = int(callback.data.split("_")[2])
    
    if not await check_rate_limit(user_id):
        await callback.message.reply("⚠️ You've reached your hourly limit. Try again later.")
        return
    
    # Forward file from the specific channel
    await app.forward_messages(user_id, channel_id, message_id)
    
    # Log request
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO requests (user_id, query, file_id, timestamp) VALUES (?, ?, ?, ?)",
             (user_id, callback.message.text, f"{channel_id}_{message_id}", datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    log_activity(user_id, "file_request", f"Requested file from channel {channel_id}, message ID {message_id}")
    await callback.answer("File forwarded!")

# Admin actions
@app.on_callback_query(filters.regex(r"admin_"))
async def handle_admin_action(client, callback):
    admin_id = get_admin_id()
    if callback.from_user.id != admin_id:
        await callback.answer("Unauthorized")
        log_activity(callback.from_user.id, "unauthorized_action", "Attempted admin action")
        return
    
    action = callback.data.split("_")[1]
    
    if action == "stats":
        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        c.execute("SELECT COUNT(DISTINCT user_id) FROM users")
        total_users = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM requests")
        total_requests = c.fetchone()[0]
        conn.close()
        
        await callback.message.reply(
            f"📊 Stats:\nTotal Users: {total_users}\nTotal Requests: {total_requests}"
        )
        log_activity(admin_id, "view_stats", "Admin viewed bot stats")
    
    elif action == "logs":
        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        c.execute("SELECT user_id, query, timestamp FROM requests ORDER BY timestamp DESC LIMIT 10")
        logs = c.fetchall()
        conn.close()
        
        log_text = "🗃 Recent Logs:\n"
        for log in logs:
            log_text += f"User {log[0]}: {log[1]} at {log[2]}\n"
        await callback.message.reply(log_text)
        log_activity(admin_id, "view_request_logs", "Admin viewed request logs")
    
    elif action == "activity_logs":
        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        c.execute("SELECT user_id, action, details, timestamp FROM activity_logs ORDER BY timestamp DESC LIMIT 10")
        logs = c.fetchall()
        conn.close()
        
        log_text = "📜 Activity Logs:\n"
        for log in logs:
            log_text += f"User {log[0]}: {log[1]} - {log[2]} at {log[3]}\n"
        await callback.message.reply(log_text)
        log_activity(admin_id, "view_activity_logs", "Admin viewed activity logs")
    
    elif action == "add_file":
        await callback.message.reply("📂 Please upload a file.")
        log_activity(admin_id, "add_file_prompt", "Admin prompted to add a file")
    
    elif action == "broadcast":
        await callback.message.reply("📢 Enter the broadcast message:")
        log_activity(admin_id, "broadcast_prompt", "Admin prompted to send a broadcast")
    
    elif action == "users":
        await callback.message.reply("🚫 User Management: Not implemented yet.")
        log_activity(admin_id, "user_management_access", "Admin accessed user management (not implemented)")
    
    elif action == "manage_channels":
        await callback.message.edit_text(
            "📚 Manage Storage Channels",
            reply_markup=get_storage_channels_menu()
        )
        log_activity(admin_id, "manage_channels", "Admin accessed manage channels menu")
    
    await callback.answer()

# Handle channel management actions
@app.on_callback_query(filters.regex(r"(add_storage_channel|view_channel_|remove_channel_|back_to_admin)"))
async def handle_channel_management(client, callback):
    admin_id = get_admin_id()
    if callback.from_user.id != admin_id:
        await callback.answer("Unauthorized")
        log_activity(callback.from_user.id, "unauthorized_channel_action", "Attempted channel management")
        return
    
    data = callback.data
    
    if data == "add_storage_channel":
        await callback.message.reply(
            "📚 Please forward a message from the channel you want to add as a storage channel. I need to be an admin of that channel."
        )
        log_activity(admin_id, "add_channel_prompt", "Admin prompted to add a storage channel")
        await callback.answer()
    
    elif data == "back_to_admin":
        await callback.message.edit_text(
            "Admin Panel",
            reply_markup=get_admin_menu()
        )
        log_activity(admin_id, "back_to_admin", "Admin returned to admin menu")
        await callback.answer()
    
    elif data.startswith("view_channel_"):
        channel_id = int(data.split("_")[2])
        await callback.message.edit_text(
            f"📚 Channel {channel_id}",
            reply_markup=get_channel_details_menu(channel_id)
        )
        log_activity(admin_id, "view_channel", f"Admin viewed channel {channel_id}")
        await callback.answer()
    
    elif data.startswith("remove_channel_"):
        channel_id = int(data.split("_")[2])
        conn = sqlite3.connect("bot.db")
        c = conn.cursor()
        c.execute("DELETE FROM storage_channels WHERE channel_id = ?", (channel_id,))
        c.execute("INSERT INTO channel_logs (action, channel_id, admin_id, timestamp) VALUES (?, ?, ?, ?)",
                 ("remove", channel_id, admin_id, datetime.now().isoformat()))
        conn.commit()
        conn.close()
        
        await callback.message.edit_text(
            f"📚 Channel {channel_id} removed successfully.",
            reply_markup=get_storage_channels_menu()
        )
        log_activity(admin_id, "remove_channel", f"Admin removed channel {channel_id}")
        await callback.answer()

# Handle forwarded message from admin to add storage channel
@app.on_message(filters.forwarded & filters.private)
async def handle_forwarded_message(client, message):
    admin_id = get_admin_id()
    if message.from_user.id != admin_id:
        log_activity(message.from_user.id, "unauthorized_forward", "Non-admin attempted to forward a message")
        return
    
    # Extract channel ID from forwarded message
    if not message.forward_from_chat or message.forward_from_chat.type not in ["channel", "supergroup"]:
        await message.reply("🚫 Please forward a message from a channel or supergroup.")
        log_activity(admin_id, "invalid_forward", "Forwarded message not from a channel/supergroup")
        return
    
    channel_id = message.forward_from_chat.id
    
    # Validate if bot is admin of the channel
    if not await is_bot_channel_admin(channel_id):
        await message.reply("🚫 I am not an admin of this channel. Please add me as an admin first.")
        log_activity(admin_id, "channel_validation_failed", f"Bot is not admin of channel {channel_id}")
        return
    
    # Add channel to database
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    try:
        c.execute("INSERT INTO storage_channels (channel_id, added_by, timestamp) VALUES (?, ?, ?)",
                 (channel_id, admin_id, datetime.now().isoformat()))
        c.execute("INSERT INTO channel_logs (action, channel_id, admin_id, timestamp) VALUES (?, ?, ?, ?)",
                 ("add", channel_id, admin_id, datetime.now().isoformat()))
        conn.commit()
        await message.reply(
            f"📚 Channel {channel_id} added successfully.",
            reply_markup=get_storage_channels_menu()
        )
        log_activity(admin_id, "add_channel", f"Admin added channel {channel_id}")
    except sqlite3.IntegrityError:
        await message.reply("🚫 This channel is already added.")
        log_activity(admin_id, "channel_already_added", f"Attempted to add channel {channel_id} again")
    finally:
        conn.close()

# Main function
async def main():
    init_db()
    # Start Flask in a separate thread
    from threading import Thread
    flask_thread = Thread(target=lambda: flask_app.run(host="0.0.0.0", port=int(os.getenv("PORT", 8000))))
    flask_thread.daemon = True
    flask_thread.start()
    
    # Start bot
    await app.start()
    print("Bot started!")
    await asyncio.Event().wait()

if __name__ == "__main__":
    asyncio.run(main())
