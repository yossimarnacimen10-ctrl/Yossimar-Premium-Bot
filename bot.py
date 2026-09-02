import logging
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application, CallbackQueryHandler, CommandHandler,
    ContextTypes, MessageHandler, filters
)

from config import load_settings
from db import Database
from keyboards import main_menu, cancel_menu, recharge_menu
from sickw import SickwClient, format_yossimar_report, raw_preview
from validators import is_valid_identifier, normalize_identifier

logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("yossimar")

settings = load_settings()
db = Database(settings.database_path)
sickw = SickwClient(settings.sickw_api_url, settings.sickw_api_key, settings.sickw_service_id)

def ensure_user(update: Update):
    user = update.effective_user
    if user:
        db.ensure_user(user.id, user.username, user.first_name)

def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    context.user_data["awaiting_identifier"] = False
    await update.effective_message.reply_text(
        f"⚡ *{settings.bot_name}*\n\nBienvenido. Selecciona una opción:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )

async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.effective_message.reply_text(
        f"🆔 Tu ID de Telegram es: `{update.effective_user.id}`",
        parse_mode=ParseMode.MARKDOWN,
    )

async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ensure_user(update)
    user_id = query.from_user.id
    action = query.data

    if action in {"menu", "cancel"}:
        context.user_data["awaiting_identifier"] = False
        await query.edit_message_text(
            f"⚡ *{settings.bot_name}*\n\nSelecciona una opción:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    if action == "check":
        balance = db.get_balance(user_id)
        if balance < settings.check_cost_credits:
            await query.edit_message_text(
                f"❌ *Saldo insuficiente*\n\nEste check cuesta *{settings.check_cost_credits} crédito(s)*.\nTu saldo: *{balance}*.",
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=recharge_menu(),
            )
            return
        context.user_data["awaiting_identifier"] = True
        await query.edit_message_text(
            f"🍎 *Check Apple*\n\nEnvíame el *IMEI (15 dígitos)* o el *número de serie*.\n\nCosto: *{settings.check_cost_credits} crédito(s)*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_menu(),
        )
        return

    if action == "balance":
        await query.edit_message_text(
            f"💰 *Mi saldo*\n\nTienes *{db.get_balance(user_id)} crédito(s)*.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    if action == "history":
        rows = db.history(user_id, 10)
        if not rows:
            text = "📜 *Historial*\n\nTodavía no tienes consultas."
        else:
            lines = ["📜 *Últimas consultas*", ""]
            for row in rows:
                icon = "✅" if row["status"] == "completed" else "❌"
                date = row["created_at"][:16].replace("T", " ")
                lines.append(f"{icon} `{row['identifier']}` · {row['cost']} crédito(s) · {date} UTC")
            text = "\n".join(lines)
        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())
        return

    if action == "recharge":
        await query.edit_message_text(
            "💳 *Recargar saldo*\n\nSelecciona un paquete.\n\n⚠️ La pasarela automática se conectará en el próximo módulo.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=recharge_menu(),
        )
        return

    if action.startswith("recharge_"):
        amount = action.split("_", 1)[1]
        await query.edit_message_text(
            f"💳 *Paquete de {amount} créditos*\n\nLa pasarela todavía no está activada. Contacta a {settings.support_username}.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    if action == "support":
        await query.edit_message_text(
            f"🆘 *Soporte*\n\nContacta a: {settings.support_username}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )

async def handle_identifier(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    if not context.user_data.get("awaiting_identifier"):
        return

    user_id = update.effective_user.id
    identifier = normalize_identifier(update.effective_message.text)

    if not is_valid_identifier(identifier):
        await update.effective_message.reply_text(
            "❌ No parece un IMEI o serial válido.\n\n• IMEI: 15 dígitos\n• Serial: letras/números, sin espacios",
            reply_markup=cancel_menu(),
        )
        return

    cost = settings.check_cost_credits
    if db.get_balance(user_id) < cost:
        context.user_data["awaiting_identifier"] = False
        await update.effective_message.reply_text("❌ Saldo insuficiente.", reply_markup=main_menu())
        return

    context.user_data["awaiting_identifier"] = False
    wait_message = await update.effective_message.reply_text("⏳ Consultando Apple...")

    try:
        new_balance = db.reserve_check_credit(user_id, cost)
    except ValueError:
        await wait_message.edit_text("❌ Saldo insuficiente.", reply_markup=main_menu())
        return

    try:
        data = await sickw.check(identifier)
        db.add_check(user_id, identifier, settings.sickw_service_id, cost, "completed", raw_preview(data))
        report = format_yossimar_report(data)
        await wait_message.edit_text(
            report + f"\n\n💰 *Saldo restante:* {new_balance} crédito(s)",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
    except Exception as exc:
        logger.exception("Error consultando SICKW")
        db.refund_check_credit(user_id, cost)
        db.add_check(user_id, identifier, settings.sickw_service_id, cost, "failed", str(exc))
        await wait_message.edit_text(
            "❌ La consulta no pudo completarse y tu crédito fue devuelto.",
            reply_markup=main_menu(),
        )

async def admin_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
        balance = db.change_balance(target, amount, f"admin_credit:{user.id}")
        await update.effective_message.reply_text(f"✅ +{amount} créditos. Nuevo saldo de {target}: {balance}")
    except Exception:
        await update.effective_message.reply_text("Uso: /credito TELEGRAM_ID CANTIDAD")

async def admin_debit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        amount = int(context.args[1])
        if amount <= 0:
            raise ValueError
        balance = db.change_balance(target, -amount, f"admin_debit:{user.id}")
        await update.effective_message.reply_text(f"✅ -{amount} créditos. Nuevo saldo de {target}: {balance}")
    except Exception:
        await update.effective_message.reply_text("Uso: /debito TELEGRAM_ID CANTIDAD")

async def admin_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        await update.effective_message.reply_text(f"💰 Saldo de {target}: {db.get_balance(target)} créditos")
    except Exception:
        await update.effective_message.reply_text("Uso: /saldo TELEGRAM_ID")

def main():
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("credito", admin_credit))
    app.add_handler(CommandHandler("debito", admin_debit))
    app.add_handler(CommandHandler("saldo", admin_balance))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_identifier))
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
