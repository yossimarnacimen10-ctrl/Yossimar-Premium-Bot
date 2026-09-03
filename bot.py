import logging
from datetime import datetime, timezone
from decimal import Decimal

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from config import load_settings
from db import Database, units_to_credits
from keyboards import (
    bank_payment_menu,
    cancel_menu,
    main_menu,
    payment_methods_menu,
    paypal_admin_menu,
    paypal_payment_menu,
    paypal_request_menu,
    receipt_admin_menu,
    recharge_menu,
)
from sickw import SickwClient, format_yossimar_report, raw_preview
from validators import normalize_identifier


logging.basicConfig(
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("yossimar")

settings = load_settings()
db = Database(settings.database_path)
sickw = SickwClient(
    settings.sickw_api_url,
    settings.sickw_api_key,
    settings.sickw_service_id,
)

PACKAGES = {
    "10": "L 295.00",
    "25": "L 737.50",
    "50": "L 1,475.00",
    "100": "L 2,950.00",
}


def ensure_user(update: Update):
    user = update.effective_user
    if user:
        db.ensure_user(user.id, user.username, user.first_name)


def is_admin(user_id: int) -> bool:
    return user_id in settings.admin_ids


def is_owner(user_id: int) -> bool:
    return bool(settings.owner_id) and user_id == settings.owner_id


def check_cost_for(user_id: int) -> Decimal:
    if is_owner(user_id):
        return Decimal("0.00")
    return settings.client_check_cost_credits


def format_credits(value) -> str:
    amount = Decimal(str(value)).quantize(Decimal("0.01"))
    return f"{amount:.2f}"


def format_expiry(iso_value: str | None) -> str:
    if not iso_value:
        return "Sin fecha"
    try:
        dt = datetime.fromisoformat(iso_value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%d/%m/%Y %H:%M UTC")
    except Exception:
        return str(iso_value)


def admin_targets() -> list[int]:
    if settings.owner_id:
        return [settings.owner_id]
    return sorted(settings.admin_ids)


async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    context.user_data.pop("awaiting_receipt_request_id", None)

    if not is_owner(update.effective_user.id) and not db.is_user_active(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ *Acceso no activo*\n\n"
            "Tu cuenta todavía no tiene una suscripción activa o ya venció.\n"
            "Contacta a soporte para activar tu acceso por 30 días.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.effective_message.reply_text(
        f"⚡ *{settings.bot_name}*\n\nBienvenido. Selecciona una opción:",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=main_menu(),
    )


async def my_id(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    await update.effective_message.reply_text(
        f"🆔 Tu ID de Telegram es: `{update.effective_user.id}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def perform_check(update: Update, context: ContextTypes.DEFAULT_TYPE, identifier: str):
    ensure_user(update)
    user_id = update.effective_user.id
    identifier = normalize_identifier(identifier)

    if not identifier.isdigit() or len(identifier) != 15:
        await update.effective_message.reply_text(
            "❌ IMEI inválido.\n\n"
            "Envía únicamente un IMEI de exactamente *15 dígitos*.\n"
            "Ejemplo: `123456789012345`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    owner = is_owner(user_id)

    if not owner and not db.is_user_active(user_id):
        await update.effective_message.reply_text(
            "⛔ *Acceso suspendido o vencido*\n\n"
            "Tu acceso al bot no está activo. Contacta a soporte para renovar tu servicio.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    cost = check_cost_for(user_id)
    balance = db.get_balance(user_id)

    if not owner and balance < cost:
        await update.effective_message.reply_text(
            f"❌ *Saldo insuficiente*\n\n"
            f"Este Check Apple cuesta *{format_credits(cost)} crédito(s)*.\n"
            f"Tu saldo: *{format_credits(balance)}*.\n\n"
            "Tu suscripción puede seguir activa, pero necesitas recargar créditos para continuar.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=recharge_menu(),
        )
        return

    wait_message = await update.effective_message.reply_text("⏳ Consultando Apple...")

    if owner:
        new_balance = balance
    else:
        try:
            new_balance = db.reserve_check_credit(user_id, cost)
        except ValueError:
            await wait_message.edit_text("❌ Saldo insuficiente.", reply_markup=main_menu())
            return

    try:
        data = await sickw.check(identifier)
        db.add_check(user_id, identifier, settings.sickw_service_id, cost, "completed", raw_preview(data))
        report = format_yossimar_report(data)
        if owner:
            footer = "\n\n👑 *Administrador:* checks ilimitados."
        else:
            footer = f"\n\n💰 *Saldo restante:* {format_credits(new_balance)} crédito(s)"
        await wait_message.edit_text(
            report + footer,
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
    except Exception as exc:
        logger.exception("Error consultando SICKW")
        if not owner:
            db.refund_check_credit(user_id, cost)
        db.add_check(user_id, identifier, settings.sickw_service_id, cost, "failed", str(exc))
        error_text = (
            "❌ La consulta no pudo completarse."
            if owner
            else "❌ La consulta no pudo completarse y tu crédito fue devuelto."
        )
        await wait_message.edit_text(error_text, reply_markup=main_menu())


async def check_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)
    user_id = update.effective_user.id

    if not is_owner(user_id) and not db.is_user_active(user_id):
        await update.effective_message.reply_text(
            "⛔ Tu suscripción no está activa o ya venció. Contacta a soporte."
        )
        return

    if not context.args:
        cost = check_cost_for(user_id)
        cost_text = (
            "👑 *Administrador:* checks ilimitados."
            if is_owner(user_id)
            else f"Costo: *{format_credits(cost)} crédito(s)*."
        )
        await update.effective_message.reply_text(
            "🍎 *Check Apple*\n\n"
            "Escribe `/check` seguido del IMEI de *15 dígitos*.\n\n"
            "Ejemplo:\n`/check 123456789012345`\n\n"
            f"{cost_text}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    await perform_check(update, context, context.args[0])


async def callbacks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    ensure_user(update)
    user_id = query.from_user.id
    action = query.data

    if not is_owner(user_id) and not is_admin(user_id) and not db.is_user_active(user_id):
        await query.edit_message_text(
            "⛔ *Acceso suspendido o vencido*\n\nContacta a soporte para renovar tu servicio.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    if action in {"menu", "cancel"}:
        context.user_data.pop("awaiting_receipt_request_id", None)
        await query.edit_message_text(
            f"⚡ *{settings.bot_name}*\n\nSelecciona una opción:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    if action == "check":
        cost = check_cost_for(user_id)
        cost_text = (
            "👑 *Administrador:* checks ilimitados."
            if is_owner(user_id)
            else f"Costo para tu cuenta: *{format_credits(cost)} crédito(s)*."
        )
        await query.edit_message_text(
            "🍎 *Check Apple*\n\n"
            "Escribe directamente el IMEI de *15 dígitos*:\n\n"
            "`123456789012345`\n\n"
            "No necesitas usar /check.\n\n"
            f"{cost_text}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    if action == "balance":
        await query.edit_message_text(
            f"💰 *Mi saldo*\n\nTienes *{format_credits(db.get_balance(user_id))} crédito(s)*.",
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
                cost = format_credits(units_to_credits(row["cost_units"] or 0))
                lines.append(f"{icon} `{row['identifier']}` · {cost} crédito(s) · {date} UTC")
            text = "\n".join(lines)

        await query.edit_message_text(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu())
        return

    if action == "recharge":
        await query.edit_message_text(
            "💳 *Recargar saldo*\n\nSelecciona un paquete:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=recharge_menu(),
        )
        return

    if action.startswith("recharge_"):
        amount = action.split("_", 1)[1]
        price = PACKAGES.get(amount)
        if not price:
            await query.edit_message_text("❌ Paquete inválido.", reply_markup=recharge_menu())
            return
        await query.edit_message_text(
            f"💳 *Paquete de {amount} créditos*\n\n"
            f"💰 Precio: *{price}*\n\nSelecciona el método de pago:",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=payment_methods_menu(amount),
        )
        return

    if action.startswith("pay_bac_") or action.startswith("pay_atlantida_"):
        method = "BAC" if action.startswith("pay_bac_") else "ATLANTIDA"
        amount = action.rsplit("_", 1)[1]
        price = PACKAGES.get(amount)
        if not price:
            return

        request_id = db.create_recharge_request(
            user_id, query.from_user.username, query.from_user.first_name,
            amount, price, method, "bank_pending",
        )

        bank_name = "BAC Credomatic" if method == "BAC" else "Banco Atlántida"
        admin_keyboard = InlineKeyboardMarkup([[
            InlineKeyboardButton("✅ Autorizar", callback_data=f"bank_ok_{request_id}"),
            InlineKeyboardButton("❌ Rechazar", callback_data=f"bank_no_{request_id}"),
        ]])

        for admin_id in admin_targets():
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "🛡️ *Solicitud de datos bancarios*\n\n"
                    f"Usuario: {query.from_user.full_name}\n"
                    f"ID: `{user_id}`\n"
                    f"Banco: *{bank_name}*\n"
                    f"Paquete: *{amount} créditos*\n"
                    f"Precio: *{price}*\n"
                    f"Solicitud: `#{request_id}`\n\n"
                    "¿Autorizas mostrarle al cliente el titular y número de cuenta?"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=admin_keyboard,
            )

        await query.edit_message_text(
            f"🔐 *{bank_name} — aprobación previa*\n\n"
            f"Paquete: *{amount} créditos*\n"
            f"Precio: *{price}*\n\n"
            "La información de la cuenta está protegida. "
            "Ya se envió una solicitud al administrador.\n\n"
            "Si la autoriza, recibirás aquí los datos bancarios.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        return

    if action.startswith("bank_ok_"):
        if not is_admin(user_id):
            await query.answer("No autorizado.", show_alert=True)
            return

        request_id = int(action.rsplit("_", 1)[1])
        req = db.get_recharge_request(request_id)
        if not req or req["status"] != "bank_pending":
            await query.answer("La solicitud ya fue procesada o no existe.", show_alert=True)
            return

        method = str(req["method"]).upper()
        if method == "BAC":
            bank_name = "BAC Credomatic"
            holder = settings.bac_holder or "No configurado"
            account = settings.bac_account or "No configurado"
        elif method == "ATLANTIDA":
            bank_name = "Banco Atlántida"
            holder = settings.atlantida_holder or "No configurado"
            account = settings.atlantida_account or "No configurado"
        else:
            await query.answer("Método bancario inválido.", show_alert=True)
            return

        db.set_recharge_status(request_id, "awaiting_receipt")
        target_id = int(req["telegram_id"])
        amount = format_credits(units_to_credits(req["package_units"]))

        await context.bot.send_message(
            chat_id=target_id,
            text=(
                f"✅ *{bank_name} autorizado*\n\n"
                f"Paquete: *{amount} créditos*\n"
                f"Precio: *{req['price_lps']}*\n\n"
                f"Titular: `{holder}`\n"
                f"Cuenta: `{account}`\n\n"
                "Después de realizar el pago, toca *Enviar comprobante*."
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=bank_payment_menu(request_id, amount),
        )
        await query.edit_message_text(query.message.text + "\n\n✅ *AUTORIZADO*", parse_mode=ParseMode.MARKDOWN)
        return

    if action.startswith("bank_no_"):
        if not is_admin(user_id):
            await query.answer("No autorizado.", show_alert=True)
            return
        request_id = int(action.rsplit("_", 1)[1])
        req = db.get_recharge_request(request_id)
        if not req or req["status"] != "bank_pending":
            await query.answer("La solicitud ya fue procesada o no existe.", show_alert=True)
            return
        db.set_recharge_status(request_id, "rejected")
        await context.bot.send_message(
            chat_id=int(req["telegram_id"]),
            text="❌ La solicitud para recibir los datos bancarios no fue autorizada.",
            reply_markup=main_menu(),
        )
        await query.edit_message_text(query.message.text + "\n\n❌ *RECHAZADO*", parse_mode=ParseMode.MARKDOWN)
        return

    if action.startswith("pay_paypal_"):
        amount = action.rsplit("_", 1)[1]
        price = PACKAGES.get(amount)
        if not price:
            return
        await query.edit_message_text(
            f"💳 *PayPal — aprobación previa*\n\n"
            f"Paquete: *{amount} créditos*\nPrecio: *{price}*\n\n"
            "Por seguridad, PayPal necesita autorización del administrador antes de mostrar el botón de pago.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=paypal_request_menu(amount),
        )
        return

    if action.startswith("paypal_request_"):
        amount = action.rsplit("_", 1)[1]
        price = PACKAGES.get(amount)
        if not price:
            return
        request_id = db.create_recharge_request(
            user_id, query.from_user.username, query.from_user.first_name,
            amount, price, "PAYPAL", "paypal_pending"
        )
        for admin_id in admin_targets():
            await context.bot.send_message(
                chat_id=admin_id,
                text=(
                    "🛡️ *Solicitud PayPal*\n\n"
                    f"Usuario: {query.from_user.full_name}\n"
                    f"ID: `{user_id}`\n"
                    f"Paquete: *{amount} créditos*\n"
                    f"Precio: *{price}*\n"
                    f"Solicitud: `#{request_id}`"
                ),
                parse_mode=ParseMode.MARKDOWN,
                reply_markup=paypal_admin_menu(request_id),
            )
        await query.edit_message_text(
            "✅ Solicitud enviada al administrador.\n\n"
            "Cuando sea autorizada recibirás aquí el botón para pagar con PayPal.",
            reply_markup=main_menu(),
        )
        return

    if action.startswith("paypal_ok_"):
        if not is_admin(user_id):
            await query.answer("No autorizado.", show_alert=True)
            return
        request_id = int(action.rsplit("_", 1)[1])
        req = db.get_recharge_request(request_id)
        if not req or req["status"] != "paypal_pending":
            await query.answer("La solicitud ya fue procesada o no existe.", show_alert=True)
            return
        db.set_recharge_status(request_id, "paypal_authorized")
        target_id = int(req["telegram_id"])
        amount = format_credits(units_to_credits(req["package_units"]))
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "✅ *PayPal autorizado*\n\n"
                f"Paquete: *{amount} créditos*\n"
                f"Precio: *{req['price_lps']}*\n\n"
                "Usa el botón de PayPal y después envía tu comprobante."
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=paypal_payment_menu(request_id, settings.paypal_url),
        )
        await query.edit_message_text(query.message.text + "\n\n✅ *AUTORIZADO*", parse_mode=ParseMode.MARKDOWN)
        return

    if action.startswith("paypal_no_"):
        if not is_admin(user_id):
            await query.answer("No autorizado.", show_alert=True)
            return
        request_id = int(action.rsplit("_", 1)[1])
        req = db.get_recharge_request(request_id)
        if not req or req["status"] != "paypal_pending":
            await query.answer("La solicitud ya fue procesada o no existe.", show_alert=True)
            return
        db.set_recharge_status(request_id, "rejected")
        await context.bot.send_message(
            chat_id=int(req["telegram_id"]),
            text="❌ La solicitud para pagar con PayPal no fue autorizada.",
            reply_markup=main_menu(),
        )
        await query.edit_message_text(query.message.text + "\n\n❌ *RECHAZADO*", parse_mode=ParseMode.MARKDOWN)
        return

    if action.startswith("receipt_"):
        request_id = int(action.split("_", 1)[1])
        req = db.get_recharge_request(request_id)
        if not req or int(req["telegram_id"]) != user_id:
            await query.answer("Solicitud inválida.", show_alert=True)
            return
        if req["status"] not in {"awaiting_receipt", "paypal_authorized"}:
            await query.answer("Esta solicitud ya fue procesada.", show_alert=True)
            return
        context.user_data["awaiting_receipt_request_id"] = request_id
        await query.edit_message_text(
            "📸 *Enviar comprobante*\n\n"
            "Ahora envía aquí la foto, captura o archivo del comprobante.\n"
            "El administrador lo revisará antes de acreditar los créditos.",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=cancel_menu(),
        )
        return

    if action.startswith("recharge_ok_"):
        if not is_admin(user_id):
            await query.answer("No autorizado.", show_alert=True)
            return
        request_id = int(action.rsplit("_", 1)[1])
        try:
            target_id, credited, new_balance = db.approve_recharge(request_id)
        except ValueError as exc:
            await query.answer(str(exc), show_alert=True)
            return
        await context.bot.send_message(
            chat_id=target_id,
            text=(
                "✅ *Recarga aprobada*\n\n"
                f"Se acreditaron *{format_credits(credited)} créditos*.\n"
                f"Nuevo saldo: *{format_credits(new_balance)} créditos*."
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        await query.edit_message_text(query.message.text + "\n\n✅ *RECARGA APROBADA*", parse_mode=ParseMode.MARKDOWN)
        return

    if action.startswith("recharge_no_"):
        if not is_admin(user_id):
            await query.answer("No autorizado.", show_alert=True)
            return
        request_id = int(action.rsplit("_", 1)[1])
        req = db.get_recharge_request(request_id)
        if not req or req["status"] != "receipt_pending":
            await query.answer("La solicitud ya fue procesada o no existe.", show_alert=True)
            return
        db.set_recharge_status(request_id, "rejected")
        await context.bot.send_message(
            chat_id=int(req["telegram_id"]),
            text=(
                "❌ *Comprobante rechazado*\n\n"
                "La recarga no fue acreditada. Si crees que hubo un error, contacta a Soporte."
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )
        await query.edit_message_text(query.message.text + "\n\n❌ *RECARGA RECHAZADA*", parse_mode=ParseMode.MARKDOWN)
        return

    if action == "support":
        await query.edit_message_text(
            f"🆘 *Soporte*\n\nContacta a: {settings.support_username}",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=main_menu(),
        )


async def handle_receipt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    if not is_owner(update.effective_user.id) and not db.is_user_active(update.effective_user.id):
        await update.effective_message.reply_text("⛔ Tu suscripción no está activa o ya venció.")
        return

    request_id = context.user_data.get("awaiting_receipt_request_id")
    if not request_id:
        return

    req = db.get_recharge_request(int(request_id))
    if not req or int(req["telegram_id"]) != update.effective_user.id:
        context.user_data.pop("awaiting_receipt_request_id", None)
        return
    if req["status"] not in {"awaiting_receipt", "paypal_authorized"}:
        context.user_data.pop("awaiting_receipt_request_id", None)
        return

    file_id = None
    if update.effective_message.photo:
        file_id = update.effective_message.photo[-1].file_id
    elif update.effective_message.document:
        file_id = update.effective_message.document.file_id

    db.set_recharge_receipt(int(request_id), file_id)
    context.user_data.pop("awaiting_receipt_request_id", None)

    amount = format_credits(units_to_credits(req["package_units"]))
    username = f"@{update.effective_user.username}" if update.effective_user.username else "Sin username"

    for admin_id in admin_targets():
        await context.bot.copy_message(
            chat_id=admin_id,
            from_chat_id=update.effective_chat.id,
            message_id=update.effective_message.message_id,
        )
        await context.bot.send_message(
            chat_id=admin_id,
            text=(
                "🧾 *Comprobante pendiente*\n\n"
                f"Solicitud: `#{request_id}`\n"
                f"Usuario: {update.effective_user.full_name}\n"
                f"Username: {username}\n"
                f"ID: `{update.effective_user.id}`\n"
                f"Método: *{req['method']}*\n"
                f"Paquete: *{amount} créditos*\n"
                f"Precio: *{req['price_lps']}*"
            ),
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=receipt_admin_menu(int(request_id)),
        )

    await update.effective_message.reply_text(
        "✅ Comprobante enviado.\n\n"
        "Tu recarga quedará pendiente hasta que el administrador la apruebe.",
        reply_markup=main_menu(),
    )


async def handle_text(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ensure_user(update)

    if not is_owner(update.effective_user.id) and not db.is_user_active(update.effective_user.id):
        await update.effective_message.reply_text(
            "⛔ Tu suscripción no está activa o ya venció. Usa /id y envía ese ID al administrador."
        )
        return

    if context.user_data.get("awaiting_receipt_request_id"):
        await update.effective_message.reply_text(
            "📸 Envía la foto, captura o archivo del comprobante.",
            reply_markup=cancel_menu(),
        )
        return

    text = normalize_identifier(update.effective_message.text)

    if text.isdigit() and len(text) == 15:
        await perform_check(update, context, text)
        return


async def admin_add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return

    try:
        target = int(context.args[0])
        if is_owner(target):
            await update.effective_message.reply_text("👑 El propietario principal no necesita suscripción.")
            return

        balance, bonus_given, expiry = db.add_subscriber(target, 30)

        bonus_text = (
            "🎁 Créditos iniciales: 5.00"
            if bonus_given
            else "🎁 Bono inicial: ya había sido entregado anteriormente"
        )

        await update.effective_message.reply_text(
            "✅ *Usuario agregado/renovado*\n\n"
            f"ID: `{target}`\n"
            "🗓 Suscripción: *30 días*\n"
            f"📅 Vence: *{format_expiry(expiry)}*\n"
            f"{bonus_text}\n"
            f"💰 Saldo actual: *{format_credits(balance)} créditos*\n"
            "🟢 Estado: *ACTIVO*",
            parse_mode=ParseMode.MARKDOWN,
        )

        try:
            await context.bot.send_message(
                chat_id=target,
                text=(
                    "✅ *Tu acceso a Yossimar Premium Bot fue activado por 30 días.*\n\n"
                    + ("🎁 Recibiste 5 créditos iniciales.\n" if bonus_given else "")
                    + f"📅 Vigencia hasta: *{format_expiry(expiry)}*\n"
                    f"💰 Saldo actual: *{format_credits(balance)} créditos*.\n\n"
                    "Si gastas tus créditos antes de que terminen los 30 días, "
                    "puedes recargar más sin perder la vigencia de tu suscripción."
                ),
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            pass

    except Exception:
        await update.effective_message.reply_text(
            "Uso: /agregar TELEGRAM_ID\nEjemplo: /agregar 123456789"
        )


async def admin_suspend(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        if is_owner(target):
            await update.effective_message.reply_text("👑 El propietario principal no puede ser suspendido.")
            return
        db.set_user_active(target, False)
        await update.effective_message.reply_text(
            f"⛔ Usuario {target} suspendido. Sus créditos se conservan."
        )
        try:
            await context.bot.send_message(
                chat_id=target,
                text="⛔ Tu acceso a Yossimar Premium Bot ha sido suspendido. Contacta a soporte para renovar tu servicio."
            )
        except Exception:
            pass
    except Exception:
        await update.effective_message.reply_text("Uso: /suspender TELEGRAM_ID")


async def admin_activate(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        db.set_user_active(target, True, renew_days=30)
        expiry = db.get_subscription_expires_at(target)
        await update.effective_message.reply_text(
            f"✅ Usuario {target} activado por 30 días.\n"
            f"📅 Vence: {format_expiry(expiry)}\n"
            "🎁 No se agregaron créditos gratis."
        )
        try:
            await context.bot.send_message(
                chat_id=target,
                text=(
                    "✅ Tu acceso a Yossimar Premium Bot ha sido renovado por 30 días.\n"
                    f"📅 Vence: {format_expiry(expiry)}\n"
                    "Tu saldo de créditos se mantiene igual."
                )
            )
        except Exception:
            pass
    except Exception:
        await update.effective_message.reply_text("Uso: /activar TELEGRAM_ID")


async def admin_user_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        active = db.is_user_active(target)
        status = "✅ ACTIVO" if active else "⛔ SUSPENDIDO/VENCIDO"
        expiry = db.get_subscription_expires_at(target)
        await update.effective_message.reply_text(
            f"👤 Usuario {target}\n"
            f"Estado: {status}\n"
            f"Vence: {format_expiry(expiry)}\n"
            f"Saldo: {format_credits(db.get_balance(target))} créditos"
        )
    except Exception:
        await update.effective_message.reply_text("Uso: /estado TELEGRAM_ID")


async def admin_credit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        amount = Decimal(context.args[1])
        if amount <= 0:
            raise ValueError
        balance = db.change_balance(target, amount, f"admin_credit:{user.id}")
        await update.effective_message.reply_text(
            f"✅ +{format_credits(amount)} créditos. Nuevo saldo de {target}: {format_credits(balance)}"
        )
    except Exception:
        await update.effective_message.reply_text(
            "Uso: /credito TELEGRAM_ID CANTIDAD\nEjemplo: /credito 123456789 10"
        )


async def admin_debit(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        amount = Decimal(context.args[1])
        if amount <= 0:
            raise ValueError
        balance = db.change_balance(target, -amount, f"admin_debit:{user.id}")
        await update.effective_message.reply_text(
            f"✅ -{format_credits(amount)} créditos. Nuevo saldo de {target}: {format_credits(balance)}"
        )
    except Exception:
        await update.effective_message.reply_text("Uso: /debito TELEGRAM_ID CANTIDAD")


async def admin_balance(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if not user or not is_admin(user.id):
        return
    try:
        target = int(context.args[0])
        await update.effective_message.reply_text(
            f"💰 Saldo de {target}: {format_credits(db.get_balance(target))} créditos"
        )
    except Exception:
        await update.effective_message.reply_text("Uso: /saldo TELEGRAM_ID")


def main():
    app = Application.builder().token(settings.telegram_bot_token).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", start))
    app.add_handler(CommandHandler("id", my_id))
    app.add_handler(CommandHandler("check", check_command))
    app.add_handler(CommandHandler("agregar", admin_add))
    app.add_handler(CommandHandler("suspender", admin_suspend))
    app.add_handler(CommandHandler("activar", admin_activate))
    app.add_handler(CommandHandler("estado", admin_user_status))
    app.add_handler(CommandHandler("credito", admin_credit))
    app.add_handler(CommandHandler("debito", admin_debit))
    app.add_handler(CommandHandler("saldo", admin_balance))
    app.add_handler(CallbackQueryHandler(callbacks))
    app.add_handler(MessageHandler(filters.PHOTO | filters.Document.ALL, handle_receipt))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_text))
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
