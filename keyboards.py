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
    return InlineKeyboardMarkup([[InlineKeyboardButton("❌ Cancelar", callback_data="cancel")]])

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
