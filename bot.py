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
ADMIN_ID = int(os.getenv("ADMIN_ID"))
STORAGE_CHANNEL = int(os.getenv("STORAGE_CHANNEL"))

app = Client("file_request_bot", api_id=API_ID, api_hash=API_HASH, bot_token=BOT_TOKEN)

# Database setup
def init_db():
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS requests 
                 (user_id INTEGER, query TEXT, file_id TEXT, timestamp TEXT)''')
    c.execute('''CREATE TABLE IF NOT EXISTS users 
                 (user_id INTEGER PRIMARY KEY, last_request TEXT, request_count INTEGER)''')
    conn.commit()
    conn.close()

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
        return False
    
    c.execute("UPDATE users SET request_count = request_count + 1, last_request = ? WHERE user_id = ?", 
             (now.isoformat(), user_id))
    conn.commit()
    conn.close()
    return True

# Admin menu
def get_admin_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📢 Broadcast", callback_data="admin_broadcast"),
         InlineKeyboardButton("📊 Stats", callback_data="admin_stats")],
        [InlineKeyboardButton("📂 Add File", callback_data="admin_add_file"),
         InlineKeyboardButton("🗃 View Logs", callback_data="admin_logs")],
        [InlineKeyboardButton("🚫 User Management", callback_data="admin_users")]
    ])

# Start command
@app.on_message(filters.command("start") & filters.private)
async def start(client, message):
    user_id = message.from_user.id
    if user_id == ADMIN_ID:
        await message.reply("Welcome, Admin!", reply_markup=get_admin_menu())
    else:
        await message.reply("Welcome! Send a keyword to search for files.")

# Admin command
@app.on_message(filters.command("admin") & filters.private & filters.user(ADMIN_ID))
async def admin_panel(client, message):
    await message.reply("Admin Panel", reply_markup=get_admin_menu())

# Search query
@app.on_message(filters.text & filters.private & ~filters.command(["start", "admin"]))
async def search_files(client, message):
    user_id = message.from_user.id
    query = message.text.strip()
    
    if len(query) < 3:
        await message.reply("⚠️ Query must be at least 3 characters long.")
        return
    
    # Validate query (basic banned words check)
    banned_words = ["spam", "hack", "illegal"]  # Add more as needed
    if any(word in query.lower() for word in banned_words):
        await message.reply("🚫 Invalid query.")
        return
    
    await message.reply("🔍 Searching for files...")
    
    # Search storage channel
    buttons = []
    async for msg in app.search_messages(STORAGE_CHANNEL, query=query, limit=10):
        file_name = msg.document.file_name if msg.document else "Unknown"
        file_size = f"{msg.document.file_size / 1024 / 1024:.2f} MB" if msg.document else "N/A"
        buttons.append([InlineKeyboardButton(
            f"{file_name} ({file_size})",
            callback_data=f"request_{msg.id}"
        )])
    
    if buttons:
        await message.reply(
            "📄 Found files:",
            reply_markup=InlineKeyboardMarkup(buttons)
        )
    else:
        await message.reply("🚫 No files found. Try a different keyword.")

# Handle file request
@app.on_callback_query(filters.regex(r"request_(\d+)"))
async def handle_request(client, callback):
    user_id = callback.from_user.id
    message_id = int(callback.data.split("_")[1])
    
    if not await check_rate_limit(user_id):
        await callback.message.reply("⚠️ You've reached your hourly limit. Try again later.")
        return
    
    # Forward file
    await app.forward_messages(user_id, STORAGE_CHANNEL, message_id)
    
    # Log request
    conn = sqlite3.connect("bot.db")
    c = conn.cursor()
    c.execute("INSERT INTO requests (user_id, query, file_id, timestamp) VALUES (?, ?, ?, ?)",
             (user_id, callback.message.text, message_id, datetime.now().isoformat()))
    conn.commit()
    conn.close()
    
    await callback.answer("File sent!")

# Admin actions
@app.on_callback_query(filters.regex(r"admin_"))
async def handle_admin_action(client, callback):
    if callback.from_user.id != ADMIN_ID:
        await callback.answer("Unauthorized")
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
    
    elif action == "add_file":
        await callback.message.reply("📂 Please upload a file.")
        # Add file handling logic here (to be implemented)
    
    elif action == "broadcast":
        await callback.message.reply("📢 Enter the broadcast message:")
        # Add broadcast logic here (to be implemented)
    
    elif action == "users":
        await callback.message.reply("🚫 User Management: Not implemented yet.")
    
    await callback.answer()

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
