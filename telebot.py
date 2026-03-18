import os
import sqlite3
import asyncio
import logging
import traceback
from telethon import TelegramClient, events, Button, errors

# ------------------------------------------------------------------
# CONFIGURATION
# ------------------------------------------------------------------
BOT_TOKEN = "8186243814:AAElF9sV9bP8brPSb3chvIGEDDHZOnz-YfA"
OWNER_ID = 5328913533
API_ID = 31757363
API_HASH = '129ab325bda6c7953bc5116664823dba'
SESSIONS_DIR = "MyTelethon"
DB_PATH = 'bot_database.db'

os.makedirs(SESSIONS_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.FileHandler("bot.log"), logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------------
# DATABASE & INITIALIZATION
# ------------------------------------------------------------------
def get_db_connection():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Ensures all tables exist before the bot starts."""
    conn = get_db_connection()
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users 
                      (user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0.0, is_owner INTEGER DEFAULT 0)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS accounts 
                      (id INTEGER PRIMARY KEY AUTOINCREMENT, phone TEXT UNIQUE, price REAL, status TEXT DEFAULT 'available')''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS settings 
                      (key TEXT PRIMARY KEY, value TEXT)''')
    # Set default price if not exists
    cursor.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('default_price', '100.0')")
    conn.commit()
    conn.close()

def init_user(user_id):
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT OR IGNORE INTO users (user_id, balance, is_owner) VALUES (?, 0.0, ?)", 
                       (user_id, 1 if user_id == OWNER_ID else 0))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"DB Error (init_user): {e}")

def get_user_balance(user_id):
    conn = get_db_connection()
    row = conn.execute("SELECT balance FROM users WHERE user_id = ?", (user_id,)).fetchone()
    conn.close()
    return row['balance'] if row else 0.0

def update_user_balance(user_id, amount):
    try:
        conn = get_db_connection()
        conn.execute("UPDATE users SET balance = balance + ? WHERE user_id = ?", (amount, user_id))
        conn.commit()
        conn.close()
        return True
    except Exception as e:
        logger.error(f"DB Error (update_balance): {e}")
        return False

def get_stats():
    conn = get_db_connection()
    u_count = conn.execute("SELECT COUNT(*) FROM users").fetchone()[0]
    a_count = conn.execute("SELECT COUNT(*) FROM accounts WHERE status = 'available'").fetchone()[0]
    conn.close()
    return u_count, a_count

def get_default_price():
    conn = get_db_connection()
    row = conn.execute("SELECT value FROM settings WHERE key = 'default_price'").fetchone()
    conn.close()
    return float(row['value']) if row else 100.0

def set_default_price(price):
    conn = get_db_connection()
    conn.execute("INSERT OR REPLACE INTO settings (key, value) VALUES ('default_price', ?)", (str(price),))
    conn.commit()
    conn.close()
    return True

# ------------------------------------------------------------------
# BOT & STATE SETUP
# ------------------------------------------------------------------
bot = TelegramClient("bot_session", API_ID, API_HASH).start(bot_token=BOT_TOKEN)
user_states = {} 
active_otp_clients = {}

# ------------------------------------------------------------------
# KEYBOARDS (UI IMPROVEMENTS)
# ------------------------------------------------------------------
def get_owner_keyboard():
    return [
        [Button.text("➕ Add Account", resize=True), Button.text("📢 Announcement")],
        [Button.text("💰 Add Money"), Button.text("📊 Stats")],
        [Button.text("🔍 Check Fund"), Button.text("🗑️ Delete Fund")],
        [Button.text("🏷️ Change Price")]
    ]

def get_user_keyboard():
    return [
        [Button.text("👤 My Account", resize=True), Button.text("🛒 Buy ID")]
    ]

def cancel_button():
    return [Button.text("❌ Cancel Operation", resize=True)]

# ------------------------------------------------------------------
# MAIN HANDLERS (PART 1)
# ------------------------------------------------------------------

@bot.on(events.NewMessage(pattern='/start'))
async def start_handler(event):
    user_id = event.sender_id
    init_user(user_id)
    
    welcome_msg = (
        "<b>✨ Welcome to the Premium ID Store ✨</b>\n"
        "━━━━━━━━━━━━━━━━━━━━\n"
        "Secure, fast, and automated ID delivery.\n\n"
        "🔹 <i>Use the buttons below to navigate.</i>"
    )
    
    if user_id == OWNER_ID:
        await event.respond(f"<b>👋 Welcome Boss!</b>\nAdmin Dashboard is ready.", 
                            buttons=get_owner_keyboard(), parse_mode='html')
    else:
        await event.respond(welcome_msg, buttons=get_user_keyboard(), parse_mode='html')

@bot.on(events.NewMessage)
async def message_handler(event):
    user_id = event.sender_id
    text = event.raw_text
    state_info = user_states.get(user_id, {})
    state = state_info.get('state')

    # Global Cancel Logic
    if text in ["/cancel", "❌ Cancel Operation"]:
        user_states.pop(user_id, None)
        reply = "<b>⚠️ Operation Cancelled.</b>"
        kb = get_owner_keyboard() if user_id == OWNER_ID else get_user_keyboard()
        await event.respond(reply, buttons=kb, parse_mode='html')
        return

    # --- OWNER CORE FEATURES ---
    if user_id == OWNER_ID:
        if text == "➕ Add Account":
            user_states[user_id] = {'state': 'AWAITING_PHONE'}
            await event.respond("<b>📱 Adding New Account</b>\n━\nPlease send the phone number with country code\nExample: <code>+919876543210</code>", 
                                buttons=cancel_button(), parse_mode='html')
            return

        elif text == "📢 Announcement":
            user_states[user_id] = {'state': 'AWAITING_ANNOUNCEMENT', 'messages': []}
            await event.respond("<b>📢 Broadcast Mode</b>\n━\nSend any message (Text, Photo, Video, File).\n\n✅ Press <b>/done</b> when finished.\n❌ Press <b>/cancel</b> to abort.", 
                                buttons=cancel_button(), parse_mode='html')
            return

        elif text == "💰 Add Money":
            user_states[user_id] = {'state': 'AWAITING_ADD_MONEY_CHAT_ID'}
            await event.respond("<b>💸 Add Funds</b>\n━\nPlease enter the <b>User ID</b> you want to credit:", buttons=cancel_button(), parse_mode='html')
            return

        elif text == "📊 Stats":
            u_count, a_count = get_stats()
            stats_text = (
                "<b>📊 Global Statistics</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"👥 Total Users: <code>{u_count}</code>\n"
                f"📦 Available IDs: <code>{a_count}</code>\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            await event.respond(stats_text, parse_mode='html')
            return

        elif text == "🔍 Check Fund":
            user_states[user_id] = {'state': 'AWAITING_CHECK_FUND_CHAT_ID'}
            await event.respond("<b>🔍 Balance Check</b>\n━\nEnter the <b>User ID</b> to view their balance:", buttons=cancel_button(), parse_mode='html')
            return

        elif text == "🗑️ Delete Fund":
            user_states[user_id] = {'state': 'AWAITING_DELETE_FUND_CHAT_ID'}
            await event.respond("<b>🗑️ Remove Funds</b>\n━\nEnter the <b>User ID</b> to deduct money from:", buttons=cancel_button(), parse_mode='html')
            return
        
        elif text == "🏷️ Change Price":
            curr = get_default_price()
            user_states[user_id] = {'state': 'AWAITING_NEW_PRICE'}
            await event.respond(f"<b>🏷️ Price Configuration</b>\n━\nCurrent Default: <code>{curr} INR</code>\n\nEnter the new price:", 
                                buttons=cancel_button(), parse_mode='html')
            return

        # --- OWNER STATE HANDLING ---
        if state == 'AWAITING_PHONE':
            phone = text.strip().replace(" ", "")
            session_file = os.path.join(SESSIONS_DIR, phone.replace("+", ""))
            client = TelegramClient(session_file, API_ID, API_HASH)
            await client.connect()
            try:
                if not await client.is_user_authorized():
                    sent_code = await client.send_code_request(phone)
                    user_states[user_id] = {
                        'state': 'AWAITING_OTP', 
                        'phone': phone, 
                        'client': client, 
                        'phone_code_hash': sent_code.phone_code_hash
                    }
                    await event.respond(f"<b>📩 OTP Sent!</b>\nEnter the code received on <code>{phone}</code>:", buttons=cancel_button(), parse_mode='html')
                else:
                    conn = get_db_connection()
                    conn.execute("INSERT OR IGNORE INTO accounts (phone, price) VALUES (?, ?)", (phone, get_default_price()))
                    conn.commit()
                    conn.close()
                    await event.respond(f"✅ <b>{phone}</b> is already authorized and added to the store.", buttons=get_owner_keyboard(), parse_mode='html')
                    user_states.pop(user_id)
                    await client.disconnect()
            except Exception as e:
                await event.respond(f"<b>❌ Error:</b>\n<code>{str(e)}</code>", buttons=get_owner_keyboard(), parse_mode='html')
                user_states.pop(user_id)
            return

        elif state == 'AWAITING_OTP':
            otp = text.strip()
            try:
                await state_info['client'].sign_in(state_info['phone'], otp, phone_code_hash=state_info['phone_code_hash'])
                conn = get_db_connection()
                conn.execute("INSERT OR IGNORE INTO accounts (phone, price) VALUES (?, ?)", (state_info['phone'], get_default_price()))
                conn.commit()
                conn.close()
                await event.respond(f"<b>✅ Success!</b>\nAccount <code>{state_info['phone']}</code> added.", buttons=get_owner_keyboard(), parse_mode='html')
                user_states.pop(user_id)
                await state_info['client'].disconnect()
            except errors.SessionPasswordNeededError:
                user_states[user_id]['state'] = 'AWAITING_2FA'
                await event.respond("<b>🔐 2FA Detected</b>\nPlease enter the Cloud Password:", buttons=cancel_button(), parse_mode='html')
            except Exception as e:
                await event.respond(f"<b>❌ Error:</b>\n<code>{str(e)}</code>", buttons=get_owner_keyboard(), parse_mode='html')
                user_states.pop(user_id)
            return

        elif state == 'AWAITING_2FA':
            try:
                await state_info['client'].sign_in(password=text.strip())
                conn = get_db_connection()
                conn.execute("INSERT OR IGNORE INTO accounts (phone, price) VALUES (?, ?)", (state_info['phone'], get_default_price()))
                conn.commit()
                conn.close()
                await event.respond(f"<b>✅ Success!</b>\nAccount <code>{state_info['phone']}</code> added with 2FA.", buttons=get_owner_keyboard(), parse_mode='html')
                user_states.pop(user_id)
                await state_info['client'].disconnect()
            except Exception as e:
                await event.respond(f"<b>❌ 2FA Error:</b>\n<code>{str(e)}</code>", buttons=get_owner_keyboard(), parse_mode='html')
                user_states.pop(user_id)
            return

        # --- CONTINUATION OF OWNER STATE HANDLING ---
        elif state == 'AWAITING_ANNOUNCEMENT':
            if text == "/done":
                messages = state_info.get('messages', [])
                if not messages:
                    await event.respond("<b>⚠️ No messages provided.</b> Announcement cancelled.", buttons=get_owner_keyboard(), parse_mode='html')
                else:
                    progress_msg = await event.respond("<b>⏳ Starting Broadcast...</b>", parse_mode='html')
                    conn = get_db_connection()
                    all_users = [row['user_id'] for row in conn.execute("SELECT user_id FROM users").fetchall()]
                    conn.close()
                    
                    success_count = 0
                    for i, uid in enumerate(all_users):
                        try:
                            for msg in messages:
                                await bot.send_message(uid, msg)
                            success_count += 1
                            # Update progress every 5 users to avoid spamming the API
                            if (i + 1) % 5 == 0:
                                await progress_msg.edit(f"<b>⏳ Broadcasting...</b>\nProgress: <code>{success_count}/{len(all_users)}</code>", parse_mode='html')
                        except:
                            continue
                        await asyncio.sleep(0.05) # Flood prevention
                    
                    await progress_msg.edit(f"<b>✅ Broadcast Complete!</b>\nSent to: <code>{success_count}</code> users.", buttons=get_owner_keyboard(), parse_mode='html')
                user_states.pop(user_id)
            else:
                state_info['messages'].append(event.message)
                await event.respond("<b>📥 Message Captured.</b>\nSend another message or type <code>/done</code> to broadcast.", parse_mode='html')
            return

        elif state == 'AWAITING_ADD_MONEY_CHAT_ID':
            try:
                target_id = int(text)
                user_states[user_id] = {'state': 'AWAITING_ADD_MONEY_AMOUNT', 'target_id': target_id}
                await event.respond(f"<b>💰 Amount to Add</b>\n━\nEnter the amount for user <code>{target_id}</code>:", buttons=cancel_button(), parse_mode='html')
            except ValueError:
                await event.respond("<b>❌ Invalid ID.</b> Please send a numeric Chat ID.", parse_mode='html')
            return

        elif state == 'AWAITING_ADD_MONEY_AMOUNT':
            try:
                amount = float(text)
                target_id = state_info['target_id']
                init_user(target_id)
                if update_user_balance(target_id, amount):
                    await event.respond(f"<b>✅ Balance Updated</b>\n━\nUser: <code>{target_id}</code>\nAdded: <code>+{amount} INR</code>", buttons=get_owner_keyboard(), parse_mode='html')
                    await bot.send_message(target_id, f"<b>💰 Funds Added!</b>\nYour account has been credited with <code>{amount} INR</code>.", parse_mode='html')
                else:
                    await event.respond("<b>❌ Database Error.</b>", buttons=get_owner_keyboard(), parse_mode='html')
                user_states.pop(user_id)
            except ValueError:
                await event.respond("<b>❌ Invalid Amount.</b> Send a number.", parse_mode='html')
            return

        elif state == 'AWAITING_CHECK_FUND_CHAT_ID':
            try:
                target_id = int(text)
                balance = get_user_balance(target_id)
                await event.respond(f"<b>🔍 User Balance</b>\n━\nUser: <code>{target_id}</code>\nBalance: <code>{balance} INR</code>", buttons=get_owner_keyboard(), parse_mode='html')
                user_states.pop(user_id)
            except ValueError:
                await event.respond("<b>❌ Invalid ID.</b>", parse_mode='html')
            return

        elif state == 'AWAITING_DELETE_FUND_CHAT_ID':
            try:
                target_id = int(text)
                user_states[user_id] = {'state': 'AWAITING_DELETE_FUND_AMOUNT', 'target_id': target_id}
                await event.respond(f"<b>🗑️ Amount to Deduct</b>\n━\nEnter amount to remove from <code>{target_id}</code>:", buttons=cancel_button(), parse_mode='html')
            except ValueError:
                await event.respond("<b>❌ Invalid ID.</b>", parse_mode='html')
            return

        elif state == 'AWAITING_DELETE_FUND_AMOUNT':
            try:
                amount = float(text)
                target_id = state_info['target_id']
                if update_user_balance(target_id, -amount):
                    await event.respond(f"<b>✅ Balance Deducted</b>\n━\nUser: <code>{target_id}</code>\nRemoved: <code>-{amount} INR</code>", buttons=get_owner_keyboard(), parse_mode='html')
                else:
                    await event.respond("<b>❌ Database Error.</b>", parse_mode='html')
                user_states.pop(user_id)
            except ValueError:
                await event.respond("<b>❌ Invalid Amount.</b>", parse_mode='html')
            return
        
        elif state == 'AWAITING_NEW_PRICE':
            try:
                new_price = float(text)
                if set_default_price(new_price):
                    await event.respond(f"<b>✅ Price Updated!</b>\nNew Default: <code>{new_price} INR</code>", buttons=get_owner_keyboard(), parse_mode='html')
                else:
                    await event.respond("<b>❌ Database Error.</b>", parse_mode='html')
                user_states.pop(user_id)
            except ValueError:
                await event.respond("<b>❌ Invalid Price.</b>", parse_mode='html')
            return

    # --- USER FEATURES ---
    if text == "👤 My Account":
        balance = get_user_balance(user_id)
        account_ui = (
            "<b>👤 Your Account Dashboard</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"🆔 User ID: <code>{user_id}</code>\n"
            f"💰 Balance: <b>{balance} INR</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>To add funds, contact the administrator.</i>"
        )
        await event.respond(account_ui, parse_mode='html')
        return

    elif text == "🛒 Buy ID":
        conn = get_db_connection()
        accounts = conn.execute("SELECT * FROM accounts WHERE status = 'available' LIMIT 5").fetchall()
        conn.close()
        
        if not accounts:
            await event.respond("<b>📭 Out of Stock!</b>\nCheck back later for new IDs.", parse_mode='html')
            return
        
        buttons = []
        for acc in accounts:
            buttons.append([Button.inline(f"📱 {acc['phone']} — {acc['price']} INR", f"buy_{acc['id']}")])
        
        if len(accounts) >= 5:
            buttons.append([Button.inline("Next Page ➡️", b"page_1")])
            
        await event.respond("<b>🛒 Available IDs</b>\nSelect an ID to view details:", buttons=buttons, parse_mode='html')
        return

# ------------------------------------------------------------------
# CALLBACK HANDLERS (PURCHASE & PAGINATION)
# ------------------------------------------------------------------
@bot.on(events.CallbackQuery)
async def callback_handler(event):
    user_id = event.sender_id
    data = event.data.decode('utf-8')
    
    if data.startswith("buy_"):
        acc_id = int(data.split("_")[1])
        conn = get_db_connection()
        acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (acc_id,)).fetchone()
        conn.close()
        
        if not acc or acc['status'] != 'available':
            await event.answer("⚠️ This ID was just sold!", alert=True)
            return
        
        balance = get_user_balance(user_id)
        if balance < acc['price']:
            await event.answer(f"❌ Insufficient Balance! (Needs {acc['price']} INR)", alert=True)
            return
        
        confirm_text = (
            "<b>🧾 Purchase Confirmation</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📱 Number: <code>{acc['phone']}</code>\n"
            f"💵 Price: <b>{acc['price']} INR</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Note: OTPs will be automatically forwarded to this chat after purchase.</i>"
        )
        await event.edit(confirm_text, buttons=[
            [Button.inline("✅ Confirm & Pay", f"confirm_{acc_id}")],
            [Button.inline("❌ Cancel", b"cancel_buy")]
        ], parse_mode='html')

    elif data.startswith("confirm_"):
        acc_id = int(data.split("_")[1])
        conn = get_db_connection()
        acc = conn.execute("SELECT * FROM accounts WHERE id = ?", (acc_id,)).fetchone()
        
        if not acc or acc['status'] != 'available':
            await event.answer("Error: ID unavailable.")
            conn.close()
            return
        
        balance = get_user_balance(user_id)
        if balance < acc['price']:
            await event.answer("Insufficient funds.")
            conn.close()
            return
        
        # TRANSACTION LOGIC
        conn.execute("UPDATE users SET balance = balance - ? WHERE user_id = ?", (acc['price'], user_id))
        conn.execute("UPDATE accounts SET status = 'sold' WHERE id = ?", (acc_id,))
        conn.commit()
        conn.close()
        
        success_ui = (
            "<b>🎉 Purchase Successful!</b>\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 <b>Account:</b> <code>{acc['phone']}</code>\n"
            "📩 <b>OTP Status:</b> Monitoring for codes...\n"
            "━━━━━━━━━━━━━━━━━━━━\n"
            "<i>Please wait for the official Telegram OTP.</i>"
        )
        await event.edit(success_ui, parse_mode='html')
        asyncio.create_task(start_otp_forwarding(acc['phone'], user_id))

    elif data == "cancel_buy":
        await event.edit("<b>⚠️ Purchase cancelled.</b>", parse_mode='html')

    elif data.startswith("page_"):
        page = int(data.split("_")[1])
        offset = page * 5
        conn = get_db_connection()
        accounts = conn.execute("SELECT * FROM accounts WHERE status = 'available' LIMIT 5 OFFSET ?", (offset,)).fetchall()
        conn.close()
        
        buttons = []
        for acc in accounts:
            buttons.append([Button.inline(f"📱 {acc['phone']} — {acc['price']} INR", f"buy_{acc['id']}")])
        
        nav = []
        if page > 0:
            nav.append(Button.inline("⬅️ Back", f"page_{page-1}"))
        if len(accounts) == 5:
            nav.append(Button.inline("Next ➡️", f"page_{page+1}"))
        if nav:
            buttons.append(nav)
            
        await event.edit("<b>🛒 Available IDs</b>", buttons=buttons, parse_mode='html')

# ------------------------------------------------------------------
# OTP FORWARDING LOGIC
# ------------------------------------------------------------------
async def start_otp_forwarding(phone, user_id):
    if phone in active_otp_clients:
        return 
        
    session_file = os.path.join(SESSIONS_DIR, phone.replace("+", ""))
    client = TelegramClient(session_file, API_ID, API_HASH)
    active_otp_clients[phone] = client
    
    try:
        await client.connect()
        
        @client.on(events.NewMessage(from_users=777000))
        async def otp_handler(event):
            clean_otp = event.raw_text
            otp_ui = (
                "<b>📩 NEW OTP RECEIVED</b>\n"
                "━━━━━━━━━━━━━━━━━━━━\n"
                f"📱 <b>Account:</b> <code>{phone}</code>\n\n"
                f"{clean_otp}\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
            await bot.send_message(user_id, otp_ui, parse_mode='html')
            logger.info(f"Forwarded OTP for {phone}")
        
        # Keep connection alive for 15 minutes to allow multiple OTP attempts if needed
        await asyncio.sleep(900)
    except Exception as e:
        logger.error(f"OTP Client Error for {phone}: {e}")
    finally:
        await client.disconnect()
        active_otp_clients.pop(phone, None)

# ------------------------------------------------------------------
# STARTUP
# ------------------------------------------------------------------
if __name__ == "__main__":
    try:
        init_db() # Create tables if they don't exist
        logger.info("--- Bot Started Successfully ---")
        bot.run_until_disconnected()
    except Exception as e:
        logger.critical(f"Crashed: {e}\n{traceback.format_exc()}")

