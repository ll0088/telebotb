#!/usr/bin/env python3
"""
Admin Multi-Session Telegram Bot
Admin creates sessions, each with unique link and custom code
Real-time updates per session via WebSocket
"""

import asyncio
import json
import os
import random
import string
from datetime import datetime
from typing import Dict, Set
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, ConversationHandler
import aiohttp
from aiohttp import web
import aiohttp_cors

# Configuration
BOT_TOKEN = os.getenv('BOT_TOKEN', 'YOUR_BOT_TOKEN_HERE')
ADMIN_ID = int(os.getenv('ADMIN_ID', 0))  # Your Telegram user ID
PORT = int(os.getenv('PORT', 8000))

# Session storage: {session_id: {'code': str, 'created_at': str, 'name': str}}
sessions: Dict[str, dict] = {}

# WebSocket connections per session: {session_id: Set[websockets]}
ws_connections: Dict[str, Set[web.WebSocketResponse]] = {}

# Conversation states
WAITING_FOR_SESSION_NAME = 1
WAITING_FOR_SESSION_CODE = 2
WAITING_FOR_EDIT_CODE = 3

# Generate random session ID
def generate_session_id(length=8):
    return ''.join(random.choices(string.ascii_lowercase + string.digits, k=length))

# Check if user is admin
def is_admin(update: Update) -> bool:
    if ADMIN_ID == 0:
        return True  # No restriction
    return update.effective_user.id == ADMIN_ID

# Broadcast code update to specific session viewers
async def broadcast_to_session(session_id: str, code: str):
    """Send new code to all viewers of a specific session"""
    if session_id not in ws_connections:
        return 0

    message = {
        'type': 'CODE_UPDATE',
        'code': code,
        'session_id': session_id,
        'updated_at': datetime.now().isoformat()
    }

    dead_connections = set()
    for ws in ws_connections[session_id]:
        try:
            await ws.send_json(message)
        except:
            dead_connections.add(ws)

    # Clean up
    ws_connections[session_id].difference_update(dead_connections)
    return len(ws_connections[session_id])

# HTTP endpoint to get session code
async def get_session_code(request):
    """Get code for specific session"""
    session_id = request.match_info.get('session_id')

    if session_id in sessions:
        return web.json_response({
            'success': True,
            'code': sessions[session_id]['code'],
            'session_id': session_id,
            'name': sessions[session_id].get('name', 'Unnamed'),
            'updated_at': sessions[session_id].get('updated_at', datetime.now().isoformat())
        })
    else:
        return web.json_response({
            'success': False,
            'error': 'Session not found'
        }, status=404)

# WebSocket endpoint for session-specific connections
async def websocket_handler(request):
    """WebSocket for real-time updates per session"""
    ws = web.WebSocketResponse()
    await ws.prepare(request)

    session_id = request.match_info.get('session_id')

    # Register connection to session
    if session_id not in ws_connections:
        ws_connections[session_id] = set()
    ws_connections[session_id].add(ws)

    # Send current code if session exists
    if session_id in sessions:
        await ws.send_json({
            'type': 'CODE_UPDATE',
            'code': sessions[session_id]['code'],
            'session_id': session_id,
            'updated_at': sessions[session_id].get('updated_at', datetime.now().isoformat())
        })
    else:
        await ws.send_json({
            'type': 'ERROR',
            'message': 'Session not found'
        })

    # Keep alive
    try:
        async for msg in ws:
            if msg.type == aiohttp.WSMsgType.TEXT:
                if msg.data == 'ping':
                    await ws.send_str('pong')
            elif msg.type == aiohttp.WSMsgType.ERROR:
                break
    finally:
        if session_id in ws_connections:
            ws_connections[session_id].discard(ws)

    return ws

# Telegram Bot Commands

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start - Admin main menu"""
    if not is_admin(update):
        await update.message.reply_text("⛔️ You are not authorized to use this bot.")
        return

    keyboard = [
        [InlineKeyboardButton("➕ Create New Session", callback_data='create_session')],
        [InlineKeyboardButton("📋 List All Sessions", callback_data='list_sessions')],
        [InlineKeyboardButton("✏️ Edit Session Code", callback_data='edit_session')],
        [InlineKeyboardButton("🗑️ Delete Session", callback_data='delete_session')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"🔐 *Admin Control Panel*\n\n"
        f"Welcome! You have {len(sessions)} active session(s).\n\n"
        f"What would you like to do?",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

async def create_session_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start creating new session"""
    query = update.callback_query
    await query.answer()

    await query.edit_message_text(
        "➕ *Create New Session*\n\n"
        "Please enter a name for this session (e.g., 'Client A', 'Project X'):",
        parse_mode='Markdown'
    )

    return WAITING_FOR_SESSION_NAME

async def receive_session_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive session name"""
    session_name = update.message.text.strip()
    context.user_data['session_name'] = session_name

    await update.message.reply_text(
        f"✏️ Session name: *{session_name}*\n\n"
        f"Now enter the code to display for this session:",
        parse_mode='Markdown'
    )

    return WAITING_FOR_SESSION_CODE

async def receive_session_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive session code and create session"""
    code = update.message.text.strip().upper()
    session_name = context.user_data.get('session_name', 'Unnamed')

    # Generate unique session ID
    session_id = generate_session_id()
    while session_id in sessions:
        session_id = generate_session_id()

    # Create session
    sessions[session_id] = {
        'code': code,
        'name': session_name,
        'created_at': datetime.now().isoformat(),
        'updated_at': datetime.now().isoformat()
    }

    # Generate web link
    web_url = f"https://your-site.netlify.app/?s={session_id}"

    keyboard = [
        [InlineKeyboardButton("🔗 Open Link", url=web_url)],
        [InlineKeyboardButton("📋 Copy Link", callback_data=f'copy_link_{session_id}')],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"✅ *Session Created!*\n\n"
        f"Name: *{session_name}*\n"
        f"ID: `{session_id}`\n"
        f"Code: `{code}`\n\n"
        f"🔗 *Link:*\n`{web_url}`\n\n"
        f"Anyone with this link will see the code.\n"
        f"Use 'Edit Session Code' to change it anytime.",
        reply_markup=reply_markup,
        parse_mode='Markdown',
        disable_web_page_preview=True
    )

    return ConversationHandler.END

async def list_sessions(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """List all active sessions"""
    query = update.callback_query
    await query.answer()

    if not sessions:
        keyboard = [[InlineKeyboardButton("➕ Create First Session", callback_data='create_session')]]
        await query.edit_message_text(
            "📭 No active sessions.\n\nCreate one to get started!",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
        return

    text = "📋 *Active Sessions:*\n\n"
    keyboard = []

    for session_id, data in sessions.items():
        text += f"• *{data.get('name', 'Unnamed')}*\n"
        text += f"  ID: `{session_id}`\n"
        text += f"  Code: `{data['code']}`\n"
        text += f"  Updated: {data['updated_at'][11:16]}\n\n"

        keyboard.append([
            InlineKeyboardButton(f"✏️ {data.get('name', 'Unnamed')[:20]}", callback_data=f'edit_{session_id}')
        ])

    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data='back_to_menu')])
    reply_markup = InlineKeyboardMarkup(keyboard)

    await query.edit_message_text(text, reply_markup=reply_markup, parse_mode='Markdown')

async def edit_session_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start editing session"""
    query = update.callback_query
    await query.answer()

    if not sessions:
        await query.edit_message_text(
            "No sessions to edit. Create one first!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='back_to_menu')]])
        )
        return ConversationHandler.END

    # Show session selection
    keyboard = []
    for session_id, data in sessions.items():
        keyboard.append([InlineKeyboardButton(
            f"{data.get('name', 'Unnamed')} ({data['code'][:8]}...)", 
            callback_data=f'edit_select_{session_id}'
        )])

    keyboard.append([InlineKeyboardButton("⬅️ Cancel", callback_data='back_to_menu')])

    await query.edit_message_text(
        "✏️ *Edit Session Code*\n\nSelect a session to edit:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

    return ConversationHandler.END  # We'll handle selection via callback

async def edit_session_select(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle session selection for edit"""
    query = update.callback_query
    await query.answer()

    session_id = query.data.replace('edit_select_', '')

    if session_id not in sessions:
        await query.edit_message_text("Session not found!")
        return

    context.user_data['editing_session'] = session_id
    session_name = sessions[session_id].get('name', 'Unnamed')
    current_code = sessions[session_id]['code']

    await query.edit_message_text(
        f"✏️ Editing: *{session_name}*\n\n"
        f"Current code: `{current_code}`\n\n"
        f"Enter the new code:",
        parse_mode='Markdown'
    )

    return WAITING_FOR_EDIT_CODE

async def receive_edit_code(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Receive new code and update session"""
    session_id = context.user_data.get('editing_session')

    if not session_id or session_id not in sessions:
        await update.message.reply_text("Error: Session not found. Please start over.")
        return ConversationHandler.END

    new_code = update.message.text.strip().upper()
    session_name = sessions[session_id].get('name', 'Unnamed')
    old_code = sessions[session_id]['code']

    # Update session
    sessions[session_id]['code'] = new_code
    sessions[session_id]['updated_at'] = datetime.now().isoformat()

    # Broadcast to all viewers of this session
    viewer_count = await broadcast_to_session(session_id, new_code)

    keyboard = [
        [InlineKeyboardButton("📋 List Sessions", callback_data='list_sessions')],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data='back_to_menu')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)

    await update.message.reply_text(
        f"✅ *Code Updated!*\n\n"
        f"Session: *{session_name}*\n"
        f"Old code: `{old_code}`\n"
        f"New code: `{new_code}`\n"
        f"Broadcasted to {viewer_count} viewer(s)\n"
        f"Time: {datetime.now().strftime('%H:%M:%S')}",
        reply_markup=reply_markup,
        parse_mode='Markdown'
    )

    return ConversationHandler.END

async def delete_session_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start delete session"""
    query = update.callback_query
    await query.answer()

    if not sessions:
        await query.edit_message_text(
            "No sessions to delete.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='back_to_menu')]])
        )
        return

    keyboard = []
    for session_id, data in sessions.items():
        keyboard.append([InlineKeyboardButton(
            f"🗑️ {data.get('name', 'Unnamed')}", 
            callback_data=f'delete_confirm_{session_id}'
        )])

    keyboard.append([InlineKeyboardButton("⬅️ Cancel", callback_data='back_to_menu')])

    await query.edit_message_text(
        "🗑️ *Delete Session*\n\nSelect session to delete:",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode='Markdown'
    )

async def delete_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Confirm and delete session"""
    query = update.callback_query
    await query.answer()

    session_id = query.data.replace('delete_confirm_', '')

    if session_id in sessions:
        session_name = sessions[session_id].get('name', 'Unnamed')
        del sessions[session_id]
        if session_id in ws_connections:
            del ws_connections[session_id]

        await query.edit_message_text(
            f"🗑️ Deleted session: *{session_name}*",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("Session not found!")

    # Show menu again
    await asyncio.sleep(1)
    await start(update, context)

async def back_to_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Return to main menu"""
    query = update.callback_query
    await query.answer()
    await start(update, context)

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel conversation"""
    await update.message.reply_text("Cancelled. Send /start to begin again.")
    return ConversationHandler.END

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle all inline button callbacks"""
    query = update.callback_query
    await query.answer()

    data = query.data

    if data == 'create_session':
        return await create_session_start(update, context)
    elif data == 'list_sessions':
        await list_sessions(update, context)
    elif data == 'edit_session':
        return await edit_session_start(update, context)
    elif data == 'delete_session':
        await delete_session_start(update, context)
    elif data == 'back_to_menu':
        await back_to_menu(update, context)
    elif data.startswith('edit_select_'):
        return await edit_session_select(update, context)
    elif data.startswith('delete_confirm_'):
        await delete_confirm(update, context)
    elif data.startswith('copy_link_'):
        session_id = data.replace('copy_link_', '')
        web_url = f"https://your-site.netlify.app/?s={session_id}"
        await query.answer(f"Link: {web_url}", show_alert=True)

# Web server setup
async def init_web_server():
    app = web.Application()

    # CORS
    cors = aiohttp_cors.setup(app, defaults={
        "*": aiohttp_cors.ResourceOptions(
            allow_credentials=True,
            expose_headers="*",
            allow_headers="*",
            allow_methods="*"
        )
    })

    # Routes
    app.router.add_get('/api/code/{session_id}', get_session_code)
    app.router.add_get('/ws/{session_id}', websocket_handler)

    # Apply CORS
    for route in list(app.router.routes()):
        cors.add(route)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, '0.0.0.0', PORT)
    await site.start()

    print(f"🌐 Web server started on port {PORT}")
    print(f"📡 WebSocket: ws://localhost:{PORT}/ws/<session_id>")
    print(f"📊 HTTP API: http://localhost:{PORT}/api/code/<session_id>")

# Main function
async def main():
    # Setup bot
    application = Application.builder().token(BOT_TOKEN).build()

    # Conversation handler for creating sessions
    create_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(create_session_start, pattern='^create_session$')],
        states={
            WAITING_FOR_SESSION_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_session_name)],
            WAITING_FOR_SESSION_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_session_code)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    # Conversation handler for editing
    edit_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_session_select, pattern='^edit_select_')],
        states={
            WAITING_FOR_EDIT_CODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, receive_edit_code)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )

    # Add handlers
    application.add_handler(CommandHandler("start", start))
    application.add_handler(create_conv)
    application.add_handler(edit_conv)
    application.add_handler(CallbackQueryHandler(button_handler, pattern='^(?!edit_select_|delete_confirm_)'))
    application.add_handler(CallbackQueryHandler(edit_session_select, pattern='^edit_select_'))
    application.add_handler(CallbackQueryHandler(delete_confirm, pattern='^delete_confirm_'))

    # Start web server
    web_task = asyncio.create_task(init_web_server())

    # Start bot
    print("🤖 Bot starting...")
    await application.initialize()
    await application.start()
    await application.updater.start_polling()

    print("✅ Bot is running!")
    print(f"🔑 Admin ID: {ADMIN_ID or 'Any user (set ADMIN_ID to restrict)'}")

    # Keep running
    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        pass
    finally:
        await application.updater.stop()
        await application.stop()
        await application.shutdown()

if __name__ == '__main__':
    # Check dependencies
    try:
        import aiohttp_cors
    except ImportError:
        print("Installing aiohttp-cors...")
        import subprocess
        import sys
        subprocess.check_call([sys.executable, "-m", "pip", "install", "aiohttp-cors"])

    asyncio.run(main())
