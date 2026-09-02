from telegram import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🍎 Check Apple", callback_data="check")],
        [
            InlineKeyboardButton("💰 Mi saldo", callback_data="balance"),
            InlineKeyboardButton("💳 Recargar saldo", callback_data="recharge"),
        ],
        [
            InlineKeyboardButton("📜 Historial", callback_data="history"),
            InlineKeyboardButton("🆘 Soporte", callback_data="support"),
        ],
    ])


def cancel_menu():
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❌ Cancelar", callback_data="cancel")]
    ])


def recharge_menu():
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("10 créditos", callback_data="recharge_10"),
            InlineKeyboardButton("25 créditos", callback_data="recharge_25"),
        ],
        [
            InlineKeyboardButton("50 créditos", callback_data="recharge_50"),
            InlineKeyboardButton("100 créditos", callback_data="recharge_100"),
        ],
        [InlineKeyboardButton("⬅️ Volver", callback_data="menu")],
    ])


def payment_methods_menu(amount: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🏦 BAC Credomatic", callback_data=f"pay_bac_{amount}")],
        [InlineKeyboardButton("🏦 Banco Atlántida", callback_data=f"pay_atlantida_{amount}")],
        [InlineKeyboardButton("💳 PayPal", callback_data=f"pay_paypal_{amount}")],
        [InlineKeyboardButton("⬅️ Volver a paquetes", callback_data="recharge")],
    ])


def bank_payment_menu(request_id: int, amount: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📸 Enviar comprobante", callback_data=f"receipt_{request_id}")],
        [InlineKeyboardButton("⬅️ Cambiar método", callback_data=f"recharge_{amount}")],
    ])


def paypal_request_menu(amount: str):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🛡️ Solicitar aprobación PayPal", callback_data=f"paypal_request_{amount}")],
        [InlineKeyboardButton("⬅️ Cambiar método", callback_data=f"recharge_{amount}")],
    ])


def paypal_admin_menu(request_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Autorizar PayPal", callback_data=f"paypal_ok_{request_id}"),
            InlineKeyboardButton("❌ Rechazar", callback_data=f"paypal_no_{request_id}"),
        ]
    ])


def paypal_payment_menu(request_id: int, paypal_url: str):
    rows = []
    if paypal_url:
        rows.append([InlineKeyboardButton("💳 Ir a PayPal", url=paypal_url)])
    rows.append([InlineKeyboardButton("📸 Enviar comprobante", callback_data=f"receipt_{request_id}")])
    rows.append([InlineKeyboardButton("⬅️ Volver", callback_data="recharge")])
    return InlineKeyboardMarkup(rows)


def receipt_admin_menu(request_id: int):
    return InlineKeyboardMarkup([
        [
            InlineKeyboardButton("✅ Aprobar recarga", callback_data=f"recharge_ok_{request_id}"),
            InlineKeyboardButton("❌ Rechazar", callback_data=f"recharge_no_{request_id}"),
        ]
    ])
