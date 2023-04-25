from typing import NamedTuple

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import CallbackContext, CallbackQueryHandler, CommandHandler, ConversationHandler
from web3 import Web3

from pancaketrade.network import Network
from pancaketrade.utils.config import Config
from pancaketrade.utils.db import remove_token
from pancaketrade.utils.generic import chat_message, check_chat_id


class RemoveTokenResponses(NamedTuple):
    CONFIRM: int = 0


class RemoveTokenConversation:
    def __init__(self, parent, config: Config):
        self.parent = parent
        self.net: Network = parent.net
        self.config = config
        self.next = RemoveTokenResponses()
        self.handler = ConversationHandler(
            entry_points=[CallbackQueryHandler(self.command_removetoken, pattern="^removetoken:0x[a-fA-F0-9]{40}$")],
            states={self.next.CONFIRM: [CallbackQueryHandler(self.command_removetoken_confirm)]},
            fallbacks=[CommandHandler("cancel", self.command_cancelremovetoken)],
            name="removetoken_conversation",
        )

    @check_chat_id
    def command_removetoken(self, update: Update, context: CallbackContext):
        assert update.callback_query
        query = update.callback_query
        assert query.data
        token_address = query.data.split(":")[1]
        if not Web3.is_checksum_address(token_address):
            chat_message(update, context, text="⛔️ Invalid token address.", edit=self.config.update_messages)
            return ConversationHandler.END
        token = self.parent.watchers[token_address]
        chat_message(
            update,
            context,
            text=f"Are you sure you want to delete {token.name}?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton("✅ Confirm", callback_data=token_address),
                        InlineKeyboardButton("❌ Cancel", callback_data="cancel"),
                    ]
                ]
            ),
            edit=self.config.update_messages,
        )
        return self.next.CONFIRM

    @check_chat_id
    def command_removetoken_confirm(self, update: Update, context: CallbackContext):
        assert update.callback_query and update.effective_chat
        query = update.callback_query
        if query.data == "cancel":
            chat_message(update, context, text="⚠️ OK, I'm cancelling this command.", edit=self.config.update_messages)
            return ConversationHandler.END
        assert query.data
        if not Web3.is_checksum_address(query.data):
            chat_message(update, context, text="⛔️ Invalid token address.", edit=self.config.update_messages)
            return ConversationHandler.END
        token = self.parent.watchers[query.data]
        token.stop_monitoring()
        token_name = token.name
        self.parent.pause_status_update(
            True
        )  # temporarily stop updating existing messages, other we might try to update the one we're now deleting
        if token.last_status_message_id is not None:
            context.bot.delete_message(chat_id=update.effective_chat.id, message_id=token.last_status_message_id)
        remove_token(self.parent.watchers[query.data].token_record)
        del self.parent.watchers[query.data]
        chat_message(
            update,
            context,
            text=f'✅ Alright, the token <b>"{token_name}"</b> was removed.',
            edit=self.config.update_messages,
        )
        self.parent.pause_status_update(False)  # resume status message updating
        return ConversationHandler.END

    @check_chat_id
    def command_cancelremovetoken(self, update: Update, context: CallbackContext):
        chat_message(update, context, text="⚠️ OK, I'm cancelling this command.", edit=self.config.update_messages)
        return ConversationHandler.END
